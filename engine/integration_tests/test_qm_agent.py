"""FastAgent end to end with a scripted planner standing in for the model."""
import functools
import http.server
import json
import os
import sys
import threading

import pytest
from playwright.sync_api import sync_playwright

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
import qm_agent
import qm_planner
from qm_agent import FastAgent, summary_text
from qm_planner import Planner, page_summary


@pytest.fixture(scope="module")
def server():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=os.path.join(ENGINE, "fixtures")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


class ScriptedPlanner:
    """Returns pre-written plans in order and records what it was shown."""
    def __init__(self, plans):
        self.plans, self.calls = list(plans), []

    def plan(self, task, observation, *, done_steps=(), problem=None, test_data=None, history=(), known_pages=None):
        self.calls.append({"page": page_summary(observation), "problem": problem,
                           "done": [s.get("name") for s in done_steps], "known_pages": known_pages})
        return qm_planner.normalize_plan(self.plans.pop(0) if self.plans else {"steps": [], "done": True})


def make_agent(monkeypatch, page, browser, server, tmp_path, plans, **kw):
    scripted = ScriptedPlanner(plans)
    monkeypatch.setattr(qm_agent, "Planner", lambda *a, **k: scripted)
    events = []
    agent = FastAgent(page, browser, {}, base_url=server, tests_root=str(tmp_path / "tests"),
                      emit=events.append, **kw)
    return agent, scripted, events


def test_task_is_planned_executed_replayed_and_saved(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, events = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "click", "target": "New transfer"},
                   {"do": "fill", "target": "Transfer name", "value": "Batch 5"},
                   {"do": "select", "target": "Region", "value": "South"},
                   {"do": "select", "target": "Destination", "value": "Chennai"},
                   {"do": "fill", "target": "Units", "value": "3"},
                   {"do": "click", "target": "Save transfer"}],
         "done": False, "test": {"flow": "transfers", "title": "Create a transfer to Chennai"}},
        {"steps": [{"do": "expect_text", "value": "Destination: Chennai"},
                   {"do": "expect_text", "value": "Status: Scheduled"}], "done": True},
    ])
    result = agent.run_task("Create transfer Batch 5 from South to Chennai with 3 units and check it is scheduled")
    assert result["saved"] and result["saved"]["tc_id"] == "TC-TRANSFERS-001", result
    assert result["replay"]["ok"]
    # The second planning round saw the saved transfer's page, not a guess.
    assert any("Transfer details" in line for line in scripted.calls[1]["page"]["elements"])
    source = open(result["saved"]["path"], encoding="utf-8").read()
    assert 'flow.expect_page_text("Status: Scheduled"' in source
    kinds = [e["event"] for e in events]
    assert kinds.count("tool_call") == 8 and "plan" in kinds
    assert "Saved **TC-TRANSFERS-001**" in summary_text(result)
    page.close()


def test_surprise_goes_back_to_the_planner_with_candidates(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "click", "target": "Start a shipment"}], "test": {"flow": "transfers"}},
        {"steps": [{"do": "click", "target": "New transfer"},
                   {"do": "expect_text", "value": "New transfer"}], "done": True},
    ])
    result = agent.run_task("Open the new transfer form")
    problem = scripted.calls[1]["problem"]
    assert problem and problem["reason"] in ("ambiguous", "not_found") and problem["step"]["target"] == "Start a shipment"
    assert result["saved"], result
    page.close()


def test_a_guessed_synonym_label_needs_no_replan(monkeypatch, browser, server, tmp_path):
    # Planned before seeing the page: "Create transfer button" for the "New transfer" link.
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "click", "target": "Create transfer button"},
                   {"do": "expect_text", "value": "New transfer"}], "done": True, "test": {"flow": "transfers"}},
    ])
    result = agent.run_task("Open the new transfer form")
    assert len(scripted.calls) == 1 and result["saved"], result
    assert 'get_by_role("link", name="New transfer")' in "\n".join(result["code"])
    page.close()


def test_a_stuck_plan_stops_within_its_bounds(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path,
                                    [{"steps": [{"do": "click", "target": "Teleport"}]}] * 10, max_rounds=3)
    result = agent.run_task("Do the impossible")
    assert len(scripted.calls) == 3
    assert result["saved"] is None and "3 planning rounds" in result["stop_reason"]
    page.close()


def test_destructive_step_asks_and_respects_a_no(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.route(server + "account.html", lambda route: route.fulfill(
        content_type="text/html", body='<button onclick="this.textContent=\'Deleted\'">Delete account</button>'))
    page.goto(server + "account.html")
    asked = []
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path,
                                    [{"steps": [{"do": "click", "target": "Delete account"}]}, {"steps": [], "done": True, "blocked": "user declined"}],
                                    confirm=lambda intent, element: asked.append(element) or False)
    result = agent.run_task("Delete my account")
    assert asked and page.get_by_role("button").inner_text() == "Delete account"
    assert scripted.calls[1]["problem"]["reason"] == "needs_confirmation" and result["saved"] is None
    page.close()


def test_planner_sends_a_compact_page_and_parses_steps(monkeypatch, browser, server):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers/new")
    sent = {}

    class FakeProvider:
        last_usage = {"prompt_tokens": 900, "completion_tokens": 120}
        def complete(self, system, user, max_tokens=None, temperature=0.2):
            sent["system"], sent["user"] = system, json.loads(user.split("\n\nRespond with ONLY")[0])
            return json.dumps({"steps": [{"do": "fill", "target": "Transfer name", "value": "X"},
                                         {"do": "expect_text", "value": "Saved"}, {"bad": 1}],
                               "test": {"flow": "Transfers & More"}})
    monkeypatch.setattr(qm_planner, "make_provider", lambda *a, **k: FakeProvider())
    events = []
    from qm_observe import observe
    plan = Planner({}, "mimo", emit=events.append).plan("make a transfer", observe(page), test_data={"user": "a"})
    assert [s["do"] for s in plan["steps"]] == ["fill", "expect_page_text"] and plan["test"]["flow"] == "transfers_more"
    msg = sent["user"]
    assert msg["page"]["url"] == "/operations.html#/transfers/new"
    assert any(line.startswith('textbox "Transfer name"') for line in msg["page"]["elements"])
    assert len(json.dumps(msg)) < 6000           # a compact page, not a DOM dump
    assert events[0]["role"] == "planner" and events[0]["input"] == 900
    page.close()


def test_blank_start_without_project_records_the_real_first_page(monkeypatch, browser, server, tmp_path):
    """Regression (first live run): a session with no project starts on about:blank;
    the saved test must open the site itself, not about:blank."""
    page = browser.new_page()
    assert page.url == "about:blank"
    scripted = ScriptedPlanner([
        {"steps": [{"do": "goto", "url": server + "operations.html#/transfers"},
                   {"do": "click", "target": "New transfer"},
                   {"do": "expect_text", "value": "New transfer"}], "done": True, "test": {"flow": "transfers"}},
    ])
    monkeypatch.setattr(qm_agent, "Planner", lambda *a, **k: scripted)
    agent = FastAgent(page, browser, {}, base_url=None, tests_root=str(tmp_path / "tests"),
                      log_dir=str(tmp_path / "logs"))
    result = agent.run_task("Open the new transfer form")
    assert result["saved"], result
    source = open(result["saved"]["path"], encoding="utf-8").read()
    assert "about:blank" not in source
    assert f'flow.goto("{server}operations.html#/transfers"' in source.split("flow = Flow")[1].splitlines()[1]
    assert result["timing"]["planner_calls"] == 0 or result["timing"]["browser_s"] >= 0
    log = json.load(open(result["log"], encoding="utf-8"))
    assert log["trace"] and log["replay"]["ok"]
    page.close()


def test_a_fresh_app_is_learned_first_so_the_plan_sees_unvisited_forms(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, events = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "click", "target": "New transfer"},
                   {"do": "expect_text", "value": "New transfer"}], "done": True, "test": {"flow": "transfers"}},
    ])
    result = agent.run_task("Open the new transfer form")
    known = "\n".join(scripted.calls[0]["known_pages"] or [])
    assert 'textbox "Transfer name"' in known and 'button "Save transfer"' in known   # a page not yet visited
    assert result["saved"] and result["timing"]["scan_s"] is not None
    # A second task in the same session doesn't scan again.
    scripted.plans.append({"steps": [{"do": "expect_text", "value": "Transfers"}], "done": True})
    assert agent.run_task("Check the transfers page")["timing"]["scan_s"] is None
    page.close()


def test_explore_maps_the_app_instead_of_writing_a_test(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [])
    result = agent.run_task("Explore the app and list its main user flows, with the pages each flow touches.")
    assert result["explored"]["pages"] >= 3 and not scripted.calls and result["saved"] is None
    assert "Mapped **" in summary_text(result) and "Save transfer" in summary_text(result)
    page.close()


def test_a_relative_goto_without_a_project_url_records_the_full_address(browser, server):
    """Regression (live run): with no project URL, "goto /path" failed as an invalid URL."""
    from qm_explorer import Explorer
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    explorer = Explorer(page, base_url=None)
    outcome = explorer.run_intent({"do": "goto", "url": "/operations.html#/inventory"})
    assert outcome["ok"], outcome
    assert explorer.steps[-1]["value"] == server + "operations.html#/inventory" and page.url.endswith("#/inventory")
    page.close()
