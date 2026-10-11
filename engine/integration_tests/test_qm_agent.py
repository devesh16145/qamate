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
    assert f'flow.goto("{server}operations.html#/transfers"' in source.split("flow.use_data")[1].splitlines()[1]
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


def test_a_check_the_app_fails_becomes_a_bug_draft_with_reproduction(monkeypatch, browser, server, tmp_path):
    """The task ends on a check the page does not satisfy (not on a step the plan got wrong):
    the result carries a finding -- steps that led there, expected, actual, a screenshot,
    what the app reported -- and a draft lands in the app's bug list."""
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    store = tmp_path / "jira_items.json"
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "click", "target": "New transfer"},
                   {"do": "fill", "target": "Transfer name", "value": "Batch 9"},
                   {"do": "click", "target": "Save transfer"},
                   {"do": "expect_text", "value": "Status: Delivered"}], "test": {"flow": "transfers"}},
        {"steps": [], "done": True, "blocked": "the transfer is Scheduled, not Delivered"},
    ], log_dir=str(tmp_path / "logs"), bug_store=str(store))
    planned = scripted.plan

    def plan_while_the_app_complains(*a, **k):          # what the app says while the steps run
        page.evaluate("() => console.error('inventory service: 503')")
        return planned(*a, **k)
    scripted.plan = plan_while_the_app_complains
    result = agent.run_task("Create transfer Batch 9 and check it is delivered")
    assert result["saved"] is None and result["stop_reason"].startswith("blocked")
    finding = result["finding"]
    assert finding["expected"] == "'Status: Delivered' is shown" and "does not show" in finding["actual"]
    assert finding["steps"] == ["New transfer", "Transfer name", "Save transfer"]
    assert finding["code"][-1].startswith('flow.click(') and os.path.exists(finding["screenshot"])
    assert any("503" in issue for issue in finding["issues"])
    items = json.load(open(store, encoding="utf-8"))
    assert len(items) == 1 and items[0]["id"] == finding["draft"] and items[0]["status"] == "draft"
    assert "Steps to reproduce:\n1. New transfer" in items[0]["description"] and items[0]["attachments"] == [finding["screenshot"]]
    text = summary_text(result)
    assert "**Possible bug:**" in text and "Saved as a draft in Bugs & stories" in text
    page.close()


def test_the_same_failed_check_twice_ends_the_task_with_the_finding(monkeypatch, browser, server, tmp_path):
    """A planner that keeps asserting what the page does not show would burn every round:
    after the second identical failure the task ends, and the finding is written up."""
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    first = {"steps": [{"do": "click", "target": "New transfer"},
                       {"do": "fill", "target": "Transfer name", "value": "Batch 3"},
                       {"do": "click", "target": "Save transfer"},
                       {"do": "expect_text", "value": "Status: Delivered"}], "test": {"flow": "transfers"}}
    # The plan acts again (opens the record) and asks the same question: the app's answer.
    acted = {"steps": [{"do": "click", "target": "Transfers"}, {"do": "expect_text", "value": "Status: Delivered"}]}
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [first] + [acted] * 6,
                                    log_dir=str(tmp_path / "logs"))
    result = agent.run_task("Create transfer Batch 3 and check it is delivered")
    assert len(scripted.calls) == 2 and "fails the same check twice" in result["stop_reason"]
    assert result["finding"]["expected"] == "'Status: Delivered' is shown" and result["saved"] is None
    # The plan only repeats the question without acting: the planner's loop, not a finding.
    page.goto(server + "operations.html#/transfers")
    same = {"steps": [{"do": "expect_text", "value": "Status: Delivered"}]}
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [first] + [same] * 6,
                                    log_dir=str(tmp_path / "logs"))
    result = agent.run_task("Create transfer Batch 3 and check it is delivered")
    assert len(scripted.calls) == 2 and "without acting" in result["stop_reason"] and not result.get("finding")
    page.close()


def test_a_pause_the_task_did_not_ask_for_is_skipped(monkeypatch, browser, server, tmp_path):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    agent, scripted, _ = make_agent(monkeypatch, page, browser, server, tmp_path, [
        {"steps": [{"do": "wait", "seconds": 3}, {"do": "click", "target": "New transfer"}, {"do": "wait", "seconds": 2},
                   {"do": "expect_text", "value": "New transfer"}], "done": True, "test": {"flow": "transfers"}}])
    result = agent.run_task("Open the new transfer form")
    assert result["saved"] and "flow.wait(" not in "\n".join(result["code"]) and result["authoring_s"] < 20
    assert sum(1 for t in agent.trace if t.get("how") == "skipped") == 2
    page.close()
