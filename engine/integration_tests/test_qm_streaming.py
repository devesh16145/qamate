"""The fast agent runs plan steps while the model is still writing the plan, and drops the
rest of a plan as soon as a step doesn't fit the page. Real Planner + a local fake
OpenAI-compatible model that writes slowly; the Dispatch Desk fixture app."""
import functools
import http.server
import json
import os
import sys
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
from qm_agent import FastAgent

PER_STEP_S = 0.5   # how long the fake model takes to write each step


class SlowModel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        srv = self.server
        reply = srv.replies.pop(0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        sent = []
        srv.log.append(sent)
        try:
            for delay, piece in reply:
                time.sleep(delay)
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
                self.wfile.flush()
                sent.append(time.monotonic())
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def written_slowly(test, steps, done=True):
    head = json.dumps({"test": test})[:-1] + ', "steps": ['
    body = [(0.2, head)] + [(PER_STEP_S, json.dumps(s) + ("," if i < len(steps) - 1 else ""))
                            for i, s in enumerate(steps)]
    return body + [(0, f'], "done": {json.dumps(done)}}}')]


@pytest.fixture(scope="module")
def app():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=os.path.join(ENGINE, "fixtures")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


@pytest.fixture
def model(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowModel)
    srv.replies, srv.log = [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QM_TEST_KEY", "test")
    srv.config = {"llm": {"schema": 2, "default_provider": "slow", "providers": {"slow": {
        "preset": "custom", "model": "slow", "api_key_env": "QM_TEST_KEY",
        "base_url": f"http://127.0.0.1:{srv.server_port}/v1"}}}}
    yield srv
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


def run(agent_page, browser, model, app, tmp_path, task):
    events = []
    stamp = lambda e: events.append({**e, "_t": time.monotonic()})
    agent = FastAgent(agent_page, browser, model.config, base_url=app, tests_root=str(tmp_path / "tests"), emit=stamp)
    return agent.run_task(task), events


TRANSFER = [{"do": "click", "target": "New transfer"},
            {"do": "fill", "target": "Transfer name", "value": "Batch 7"},
            {"do": "select", "target": "Region", "value": "North"},
            {"do": "select", "target": "Destination", "value": "Jaipur"},
            {"do": "fill", "target": "Units", "value": "4"},
            {"do": "click", "target": "Save transfer"},
            {"do": "expect_text", "value": "Destination: Jaipur"}]


def test_steps_run_while_the_plan_is_still_being_written(browser, model, app, tmp_path):
    model.replies = [written_slowly({"flow": "transfers", "title": "Create a transfer to Jaipur"}, TRANSFER)]
    page = browser.new_page()
    page.goto(app + "operations.html#/transfers")
    result, events = run(page, browser, model, app, tmp_path, "Create transfer Batch 7 to Jaipur with 4 units")
    assert result["saved"] and result["saved"]["flow"] == "transfers" and result["replay"]["ok"], result
    calls = [e["_t"] for e in events if e.get("event") == "tool_call"]
    last_piece = model.log[0][-1]
    assert len(calls) == 7 and calls[1] < last_piece - 2 * PER_STEP_S   # well before the plan was finished
    assert result["timing"]["first_step_s"] is not None and result["timing"]["first_step_s"] < 2 * PER_STEP_S + 0.5
    page.close()


def test_a_step_that_does_not_fit_drops_the_rest_of_the_plan(browser, model, app, tmp_path):
    wrong = [TRANSFER[0], {"do": "click", "target": "Teleport"}] + [{"do": "expect_text", "value": f"x{i}"} for i in range(8)]
    model.replies = [written_slowly({"flow": "transfers", "title": "Open the transfer form"}, wrong, done=False),
                     written_slowly({"flow": "transfers"}, [{"do": "expect_text", "value": "New transfer"}])]
    page = browser.new_page()
    page.goto(app + "operations.html#/transfers")
    started = time.monotonic()
    result, events = run(page, browser, model, app, tmp_path, "Open the new transfer form")
    assert result["saved"] and result["saved"]["flow"] == "transfers", result
    assert result["replanned"] and result["replanned"][0]["step"].startswith("click \"Teleport\"")
    # The first reply was abandoned at the bad step instead of being read to its end (10 steps).
    assert len(model.log[0]) < len(wrong) and time.monotonic() - started < len(wrong) * PER_STEP_S + 4
    page.close()


def test_a_second_task_in_the_same_chat_is_saved(browser, model, app, tmp_path):
    """Regression (live run, 2026-10-04): the chat runtime drains the planner's token totals
    after every turn; the next turn's bookkeeping crashed, the agent re-planned around the
    crash, and a test whose replay had passed was never saved."""
    model.replies = [written_slowly({"flow": "transfers", "title": "Open the transfer form"},
                                    [TRANSFER[0], {"do": "expect_text", "value": "New transfer"}]),
                     written_slowly({"flow": "transfers", "title": "Create a transfer to Jaipur"}, TRANSFER)]
    page = browser.new_page()
    page.goto(app + "operations.html#/transfers")
    agent = FastAgent(page, browser, model.config, base_url=app, tests_root=str(tmp_path / "tests"))
    first = agent.run_task("Open the new transfer form")
    for key in ("input", "output"):
        agent.planner.usage.pop(key, 0)          # what agent_chat does after every turn
    page.goto(app + "operations.html#/transfers")   # the next task starts from the list
    second = agent.run_task("Create transfer Batch 7 to Jaipur with 4 units")
    assert first["saved"] and second["saved"] and not second["stop_reason"], second
    assert second["timing"]["planner_calls"] == 1 and not second["replanned"]
    # The saved transfer's page is checked by route, not by the id this run happened to get.
    assert any('expect_url="/operations.html#/transfers/:id"' in line for line in second["code"]), second["code"]
    page.close()
