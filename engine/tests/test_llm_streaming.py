"""Streaming model replies: providers hand text over as it is generated, the planner
pulls complete steps out of a half-written reply, and abandoning a stream closes it.
A local fake OpenAI-compatible server stands in for the model (no network, no keys)."""
import http.server
import json
import threading
import time

import pytest

import llm
from qm_planner import StepScanner


class FakeModel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.requests.append(body)
        if srv.reject_stream_options and "stream_options" in body:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "unknown field stream_options"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for delay, piece in srv.pieces:
                time.sleep(delay)
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                srv.sent += 1
            usage = {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 20}}
            self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
        except (BrokenPipeError, ConnectionResetError):
            srv.disconnected = True


@pytest.fixture
def model():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    srv.requests, srv.pieces, srv.sent, srv.disconnected, srv.reject_stream_options = [], [], 0, False, False
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def provider_for(srv, monkeypatch):
    monkeypatch.setenv("QM_TEST_KEY", "test")
    return llm.OpenAIProvider("local", {"model": "fake", "api_key_env": "QM_TEST_KEY",
                                        "base_url": f"http://127.0.0.1:{srv.server_port}/v1"})


def test_openai_compatible_stream_delivers_pieces_and_usage(model, monkeypatch):
    model.pieces = [(0, '{"steps": '), (0, '[{"do": "click", '), (0, '"target": "Login"}]}')]
    got = []
    text = provider_for(model, monkeypatch).stream("sys", "user", got.append)
    assert text == '{"steps": [{"do": "click", "target": "Login"}]}' and len(got) == 3
    assert model.requests[0]["stream"] is True and model.requests[0]["stream_options"] == {"include_usage": True}


def test_usage_in_stream_is_optional(model, monkeypatch):
    model.reject_stream_options = True
    model.pieces = [(0, "hello")]
    p = provider_for(model, monkeypatch)
    assert p.stream("sys", "user", lambda piece: None) == "hello"
    assert "stream_options" not in model.requests[-1] and p.last_usage["completion_tokens"] == 20


def test_abandoning_a_stream_returns_at_once(model, monkeypatch):
    model.pieces = [(0, "first")] + [(0.3, f" more{i}") for i in range(10)]   # 3 s if read to the end
    started = time.monotonic()
    text = provider_for(model, monkeypatch).stream("sys", "user", lambda piece: False)
    assert text == "first" and time.monotonic() - started < 1.0


def test_step_scanner_yields_each_step_as_its_brace_closes():
    reply = ('```json\n{"test": {"flow": "orders", "title": "Edit {draft}"}, "steps": [\n'
             '{"do": "fill", "target": "Note", "value": "a } brace and a \\" quote"},\n'
             '{"do": "expect_text", "value": "Saved"}], "done": true}\n```')
    scanner, seen = StepScanner(), []
    for i in range(len(reply)):          # one character at a time, the worst case
        seen += [(i, s) for s in scanner.feed(reply[i])]
    assert [s["do"] for _, s in seen] == ["fill", "expect_page_text"]
    assert seen[0][1]["value"] == 'a } brace and a " quote'
    assert seen[0][0] < reply.index('"expect_text"')   # yielded before the next step was written
