"""The fast benchmark end to end on one Dispatch Desk task: a fake model plans, the fast
agent authors and saves, the test is re-run independently through pytest, and the
scorecard records timing, tokens and the verdict."""
import http.server
import json
import os
import sys
import threading

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
import qm_bench

PLAN = {"test": {"flow": "transfers", "title": "Create transfer QA Dispatch Cedar"},
        "steps": [{"do": "click", "target": "New transfer"},
                  {"do": "fill", "target": "Transfer name", "value": "QA Dispatch Cedar"},
                  {"do": "select", "target": "Region", "value": "North"},
                  {"do": "select", "target": "Destination", "value": "Jaipur"},
                  {"do": "fill", "target": "Units", "value": "12"},
                  {"do": "click", "target": "Save transfer"},
                  {"do": "expect_text", "value": "QA Dispatch Cedar"},
                  {"do": "expect_text", "value": "Destination: Jaipur"}],
        "done": True}


class Model(http.server.BaseHTTPRequestHandler):
    asked = []                      # the model id of every request

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))).get("model"))
        text = json.dumps(PLAN)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(text), 40):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': text[i:i + 40]}}]})}\n\n".encode())
        usage = {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 150}}
        self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())


def test_bench_authors_verifies_independently_and_scores(tmp_path, monkeypatch, capsys):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QM_TEST_KEY", "test")
    (tmp_path / "config.json").write_text(json.dumps({"llm": {"schema": 2, "default_provider": "fake", "providers": {
        "fake": {"preset": "custom", "model": "fake", "api_key_env": "QM_TEST_KEY",
                 "base_url": f"http://127.0.0.1:{srv.server_port}/v1"}}}}), encoding="utf-8")
    try:
        assert qm_bench.main(["--only", "ops-transfer", "--verify-runs", "1", "--ats-root", str(tmp_path),
                              "--model", "other/planner"]) == 0
    finally:
        srv.shutdown()
    # --model reached the provider and the scorecard; the saved settings are untouched.
    assert Model.asked and set(Model.asked) == {"other/planner"}
    assert json.loads((tmp_path / "config.json").read_text())["llm"]["providers"]["fake"]["model"] == "fake"
    card_path = next((tmp_path / "results" / "_agent_bench").glob("*-fast/scorecard.json"))
    card = json.loads(card_path.read_text())
    row = card["tasks"][0]
    assert card["engine"] == "fast" and row["task_id"] == "ops-transfer"
    assert card["provider"] == "fake" and card["model"] == "other/planner"
    assert row["verdict"] == "reliable" and row["independent"]["runs"] == [True], row
    assert row["tokens"]["planner_input"] == 1200 and row["run_attempts"] == 1 and row["replans"] == 0
    assert card["scorecard"]["independent_reliable"] == 1 and card["scorecard"]["coverage_audited"] is False
    assert "1/1 independently reliable" in capsys.readouterr().out


def test_task_selection_by_app_or_id():
    assert [t["id"] for t in qm_bench.select_tasks(["crm"])] == ["crm-lifecycle", "crm-related-contact", "crm-search"]
    assert [t["id"] for t in qm_bench.select_tasks(["smoke,ops-allocation"])] == ["smoke", "ops-allocation"]
    assert len(qm_bench.select_tasks([])) == 10
