"""The chooser comparison over real HTTP: a stand-in server speaks both wire formats (the
decisions endpoint and chat completions) and answers every case correctly, so the tool's
requests, parsing and scoring are checked end to end without a model or a key."""
import http.server
import json
import os
import sys
import threading

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
import qm_chooser_eval
import qm_decide

CASES = json.load(open(qm_chooser_eval.CASES, encoding="utf-8"))["cases"]
BY_STEP = {qm_decide.describe(c["step"]) + "|" + "|".join(sorted(c["options"])): c for c in CASES}


def right_answer(step_text, options):
    case = BY_STEP[step_text + "|" + "|".join(sorted(t for k, t in options.items() if k != qm_decide.NONE_ID))]
    if case["gold"] is None:
        return qm_decide.NONE_ID
    return next(k for k, t in options.items() if t == case["options"][case["gold"]])


class Server(http.server.BaseHTTPRequestHandler):
    seen = []          # (path, model, request extras)
    reject_reasoning = False

    def log_message(self, *a):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        Server.seen.append((self.path, body.get("model"), body.get("reasoning")))
        if self.path.endswith("/decisions"):
            question = body["questions"]["element"]
            choice = right_answer(body["state"]["step"], question["criteria"])
            return self.reply(200, {"answers": {"element": {"choice": choice, "confidence": 0.9}},
                                    "usage": {"input_tokens": 200, "output_tokens": 1}})
        if Server.reject_reasoning and body.get("reasoning"):
            return self.reply(400, {"error": {"message": "reasoning is not supported by this model"}})
        asked = json.loads(body["messages"][1]["content"].split("\nRespond with only JSON.")[0])
        choice = right_answer(asked["step"], asked["options"])
        self.reply(200, {"choices": [{"message": {"content": "```json\n" + json.dumps({"choice": choice}) + "\n```"}}],
                         "usage": {"prompt_tokens": 250, "completion_tokens": 8}})


def test_comparison_speaks_both_wire_formats_and_scores(tmp_path, monkeypatch, capsys):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("QM_TEST_KEY", "test-key")
    (tmp_path / "config.json").write_text(json.dumps({"llm": {"schema": 2, "roles": {"decision": "decider"}, "providers": {
        "decider": {"preset": "custom", "protocol": "openrouter_decisions", "model": "acme/decider-1",
                    "api_key_env": "QM_TEST_KEY", "base_url": base + "/api/alpha"},
        "chatty": {"preset": "custom", "model": "acme/chat-1", "api_key_env": "QM_TEST_KEY", "base_url": base + "/v1"}}}}))
    try:
        assert qm_chooser_eval.main(["--ats-root", str(tmp_path), "role", "profile:chatty", "profile:nope"]) == 0
    finally:
        srv.shutdown()
    card = json.load(open(next((tmp_path / "results" / "_chooser_eval").glob("*/scorecard.json"))))
    decider, chatty, missing = card["models"]
    for row in (decider, chatty):
        assert row["correct"] == len(CASES) and row["wrong_pick"] == 0 and row["error"] == 0, row
        assert row["p50_ms"] is not None
    assert decider["resolved_model"] == "acme/decider-1" and "Unknown model profile" in missing["unavailable"]
    paths = {path for path, _, _ in Server.seen}
    assert paths == {"/api/alpha/decisions", "/v1/chat/completions"}
    assert len(Server.seen) == 4 * len(CASES)          # two orders per case, two models
    out = capsys.readouterr().out
    assert "Fewest wrong picks, then most correct" in out and "unavailable" in out


def test_a_chat_chooser_is_asked_without_reasoning_and_survives_a_refusal(monkeypatch):
    """Through OpenRouter a chat model is asked with reasoning off; a model that rejects
    the switch is asked again without it instead of failing the choice."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QM_TEST_KEY", "test-key")
    case = CASES[0]
    cfg = {"protocol": "openai", "model": "acme/chat-1", "api_key_env": "QM_TEST_KEY", "timeout": 10,
           "base_url": f"http://127.0.0.1:{srv.server_port}/openrouter.ai/v1"}
    config = {"llm": {"schema": 2, "providers": {"c": {"preset": "custom", **cfg}}}}
    options = {key: (qm_decide.NONE_TEXT if i is None else case["options"][i])
               for key, i in qm_decide.arrangements(len(case["options"]))[0]}
    try:
        Server.seen.clear()
        Server.reject_reasoning = False
        first = qm_decide.ask(config, "c", cfg, qm_decide.describe(case["step"]), options, {"title": "", "path": ""})
        assert first["choice"] == right_answer(qm_decide.describe(case["step"]), options)
        assert [extra for _, _, extra in Server.seen] == [{"effort": "none"}]
        Server.seen.clear()
        Server.reject_reasoning = True
        again = qm_decide.ask(config, "c", cfg, qm_decide.describe(case["step"]), options, {"title": "", "path": ""})
        assert again["choice"] == first["choice"]
        assert [extra for _, _, extra in Server.seen] == [{"effort": "none"}, None]
    finally:
        Server.reject_reasoning = False
        srv.shutdown()
