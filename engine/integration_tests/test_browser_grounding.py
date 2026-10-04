"""Explicit local-browser gate. No network, API keys, or production app fixtures."""
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_chat import BrowserSession
from playwright.sync_api import sync_playwright

HTML = '''<html><title>Local grounding fixture</title><body>
<input aria-label="Name" data-test="name">
<select aria-label="Sort" data-test="sort"><option value="az">Name A to Z</option>
<option value="lohi">Price low to high</option></select>
<button data-cy="save" onclick="document.querySelector('output').textContent='Saved'">Save</button>
<output></output></body></html>'''


@pytest.fixture
def session():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        session = BrowserSession()
        session.browser, session.context, session.page = browser, context, context.new_page()
        session.page.set_default_timeout(2000)
        session.page.route("https://fixture.test/**", lambda route: route.fulfill(body=HTML, content_type="text/html"))
        session.page.goto("https://fixture.test/", timeout=10000)
        yield session
        browser.close()


def ref_for(session, test_id):
    session.inspect()
    return next(ref for ref, el in session.by_ref.items() if el.get("test_id") == test_id)


def test_start_url_outcome_is_exact_atomic_and_portable(session):
    from decision_browser import BrowseContract
    from decision_browser_tools import BrowserBridge
    from recorder_parser import _generate_assertion_code
    from playwright.sync_api import expect
    from urllib.parse import urljoin
    task = BrowseContract.model_validate({"start_url": "https://fixture.test", "goal": "Remain at entry",
        "milestones": [{"goal": "Entry", "outcomes": [{"name": "At entry", "kind": "url_matches_start"}]}]})
    bridge = BrowserBridge(session, task)
    assert bridge.check(task.milestones[0])["ok"]
    assertion = session.assertions[0]
    assert assertion["type"] == "url_equals" and assertion["relative_to_base"]
    code = _generate_assertion_code({**assertion, "timeout": 150})
    scope = {"page": session.page, "expect": expect, "urljoin": urljoin, "base_url": "https://fixture.test/"}
    exec(code, scope)
    session.page.goto("https://fixture.test/#other")
    count = len(session.assertions)
    assert bridge.check(task.milestones[0])["status"] == "start_url_not_observed"
    assert len(session.assertions) == count
    with pytest.raises(AssertionError):
        exec(code, scope)
    session.page.route("https://relocated.test/**", lambda route: route.fulfill(body=HTML, content_type="text/html"))
    session.page.goto("https://relocated.test/")
    exec(code, {**scope, "base_url": "https://relocated.test/"})


def test_return_route_cannot_pass_on_other_page_with_identical_text(session):
    from decision_browser import BrowseContract
    from decision_browser_tools import BrowserBridge
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Return to parent",
        "milestones": [
            {"goal": "Parent", "outcomes": [{"name": "Saved", "kind": "page_contains_text", "value": "Shared record"}]},
            {"goal": "Return", "outcomes": [{"name": "Parent URL", "kind": "url_matches_milestone", "milestone_index": 0},
                {"name": "Saved", "kind": "page_contains_text", "value": "Shared record"}]}]})
    session.page.set_content("<p>Shared record</p>")
    session.page.evaluate("history.replaceState(null, '', '#/parents/7')")
    bridge = BrowserBridge(session, task)
    initial_probe = bridge.probe(task.milestones[0])
    assert initial_probe["ok"], initial_probe
    assert bridge.completed_routes == {} and session.assertions == []
    assert bridge.check(task.milestones[1])["status"] == "referenced_milestone_not_completed"
    assert bridge.check(task.milestones[0])["ok"]
    session.page.evaluate("history.replaceState(null, '', '#/children/9')")
    count = len(session.assertions)
    assert bridge.check(task.milestones[1])["status"] == "milestone_url_not_observed"
    assert len(session.assertions) == count
    session.page.evaluate("history.replaceState(null, '', '#/parents/7')")
    assert bridge.check(task.milestones[1])["ok"]
    assert session.assertions[count]["value"] == "/#/parents/7"


def test_required_roundtrip_records_intermediate_route_evidence(session):
    from decision_browser import BrowseContract
    from decision_browser_tools import BrowserBridge
    from recorder_parser import _generate_assertion_code
    from playwright.sync_api import expect
    from urllib.parse import urljoin
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Roundtrip",
        "milestones": [{"goal": "Leave and return", "transition": "roundtrip",
            "outcomes": [{"name": "Same text", "kind": "page_contains_text", "value": "Shared"}]}]})
    session.page.set_content("<p>Shared</p><a href='#/away'>Away</a><a href='/'>Home</a>")
    bridge = BrowserBridge(session, task)
    milestone = task.milestones[0]
    assert bridge.probe(milestone)["status"] == "required_transition_pending"
    session.page.evaluate("history.replaceState(null, '', '?filter=changed')")
    session.steps.append({"id": 1})
    assert bridge.check(milestone)["status"] == "required_transition_pending"
    session.page.evaluate("history.replaceState(null, '', '/#/away')")
    session.steps.append({"id": 2})
    assert bridge.check(milestone)["status"] == "required_transition_pending"
    assert session.assertions == []
    session.page.evaluate("history.replaceState(null, '', '/')")
    session.steps.append({"id": 3})
    assert bridge.probe(milestone)["ok"]
    assert session.assertions == []
    assert bridge.check(milestone)["ok"]
    evidence = [a for a in session.assertions if a["description"] == "Required observed route transition"]
    assert [(a["value"], a["afterStep"]) for a in evidence] == [("/#/away", 2), ("/", 3)]
    scope = {"page": session.page, "expect": expect, "urljoin": urljoin, "base_url": "https://fixture.test/"}
    # A replay that skips the excursion must fail its intermediate assertion.
    with pytest.raises(AssertionError):
        exec(_generate_assertion_code({**evidence[0], "timeout": 100}), scope)


def test_generated_repeated_pagination_clicks_are_not_dropped(session, tmp_path):
    import ast
    from recorder_parser import generate_from_review
    session.page.set_content('''<button onclick="document.querySelector('output').textContent=Number(document.querySelector('output').textContent)+1">Next page</button><output>1</output>''')
    payload = {"tc_id": "TC-PAGE-001", "flowId": "pagination", "steps": [
        {"id": n, "type": "click", "rawLine": 'page.get_by_role("button", name="Next page").click()'}
        for n in range(1, 4)], "assertions": [], "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/pagination/test_pagination.py").read_text(encoding="utf-8")
    clicks = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Expr)
              and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
              and n.value.func.attr == "click"]
    assert len(clicks) == 3
    for statement in clicks:
        exec(compile(ast.Module(body=[statement], type_ignores=[]), "<generated>", "exec"),
             {"page": session.page})
    assert session.page.locator("output").inner_text() == "4"


def test_generated_numeric_correction_replaces_existing_value(session, tmp_path):
    import ast
    from recorder_parser import generate_from_review
    session.page.set_content('<input type="number" aria-label="Units" value="0">')
    payload = {"tc_id": "TC-NUM-001", "flowId": "numeric", "description": "Correct units",
               "steps": [{"id": 1, "type": "fill", "value": "7",
                          "rawLine": 'page.get_by_role("spinbutton", name="Units").fill("7")'}],
               "assertions": [], "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/numeric/test_numeric.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Execute the emitted browser statements, including selection before typing.
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    statements = [n for n in ast.walk(function) if isinstance(n, ast.Expr)
                  and isinstance(n.value, ast.Call) and ast.unparse(n.value).startswith("page.")]
    for initial in ("0", "123", ""):
        session.page.get_by_role("spinbutton", name="Units").fill(initial)
        for statement in statements:
            exec(compile(ast.Module(body=[statement], type_ignores=[]), "<generated>", "exec"),
                 {"page": session.page, "tc_data": {"input_1": "7"}})
        assert session.page.get_by_role("spinbutton", name="Units").input_value() == "7"


def test_observe_exposes_empty_state_without_hidden_or_editable_text(session):
    session.page.set_content('''<nav>Distracting navigation</nav><main><h1>Companies</h1>
        <input aria-label="Search" value="private input"><textarea>private textarea</textarea>
        <div contenteditable="true">private editor</div><p>No matching companies</p>
        <div hidden>hidden secret</div><div aria-hidden="true">aria secret</div>
        <div style="opacity:0"><span>transparent secret</span></div>
        <script>"script secret"</script><button>Clear search</button></main>''')
    observed = session.inspect()
    evidence = observed["rendered_text"]
    assert evidence["scope"] == "main" and not evidence["truncated"]
    assert "No matching companies" in evidence["text"]
    assert "private" not in evidence["text"] and "secret" not in evidence["text"]
    assert "Distracting" not in evidence["text"]
    from interaction_context import decision_page
    assert "rendered_text" not in decision_page(observed)  # No extra decision payload.


def test_text_evidence_is_bounded_and_prefers_active_dialog(session):
    session.page.set_content('<main><p>' + 'Company ' * 1000 + '</p><button>Open</button></main>')
    evidence = session.inspect()["rendered_text"]
    assert evidence["truncated"] and len(evidence["text"]) <= 2000
    session.page.set_content('<main>Background <button>Open</button></main><div role="dialog"><h2>Confirmation</h2><button>Cancel</button></div>')
    evidence = session.inspect()["rendered_text"]
    assert evidence["scope"] == "dialog" and "Confirmation" in evidence["text"]
    assert "Background" not in evidence["text"]


def test_native_select_records_exact_value_and_replays(session):
    ref = ref_for(session, "sort")
    assert session.by_ref[ref]["primary"] == {"by": "css", "value": '[data-test="sort"]'}
    result = session.select_from_dropdown(ref, "Price low to high")
    assert result["ok"], result
    assert session.page.locator("select").input_value() == "lohi"

    step = session.steps[-1]
    assert "lohi" in step["rawLine"]
    session.page.reload()
    exec(step["rawLine"], {"page": session.page})
    assert session.page.locator("select").input_value() == "lohi"


def test_option_recording_keeps_exact_match_when_create_option_appears(session):
    session.page.set_content('''<div role="listbox">
      <div role="option" onclick="document.querySelector('output').textContent='existing'">Acme</div>
      </div><output></output>''')
    session.inspect()
    ref = next(ref for ref, el in session.by_ref.items() if el.get("role") == "option")
    assert session.act("click", ref)["ok"]
    code = session.steps[-1]["rawLine"]
    assert "exact=True" in code
    session.page.evaluate('''() => {
      document.querySelector('output').textContent = '';
      const option = document.createElement('div');
      option.setAttribute('role', 'option');
      option.textContent = 'Create Acme';
      option.onclick = () => document.querySelector('output').textContent = 'wrong';
      document.querySelector('[role=listbox]').prepend(option);
    }''')
    exec(code, {"page": session.page})
    assert session.page.locator("output").inner_text() == "existing"


def test_value_checkpoint_is_true_and_replayable(session):
    from recorder_parser import _generate_assertion_code
    from playwright.sync_api import expect
    ref = ref_for(session, "sort")
    assert not session.add_checkpoint("Sort selected", "element_has_value", "lohi", ref)["ok"]
    assert session.select_from_dropdown(ref, "Price low to high")["ok"]
    assert session.add_checkpoint("Sort selected", "element_has_value", "lohi", ref)["ok"]
    code = _generate_assertion_code(session.assertions[-1])
    exec(code, {"page": session.page, "expect": expect})
    step = session.steps[-1]
    assert "lohi" in step["rawLine"]
    session.page.reload()
    exec(step["rawLine"], {"page": session.page})
    exec(code, {"page": session.page, "expect": expect})


def test_negative_checkpoint_replays_and_detects_planted_wrong_result(session):
    from recorder_parser import _generate_assertion_code
    from playwright.sync_api import expect
    value = 'Removed "product"'
    assert session.add_checkpoint("Removed", "page_not_contains_text", value)["ok"]
    assertion = {**session.assertions[-1], "timeout": 100}
    code = _generate_assertion_code(assertion)
    exec(code, {"page": session.page, "expect": expect})
    session.page.locator("output").evaluate("(el, value) => el.textContent = value", value)
    with pytest.raises(AssertionError):
        exec(code, {"page": session.page, "expect": expect})
    assert not session.add_checkpoint("Still removed", "page_not_contains_text", value)["ok"]


def test_plain_input_has_no_autocomplete_wait(session, monkeypatch):
    ref = ref_for(session, "name")
    def forbidden():
        pytest.fail("A plain input must not poll dropdowns")
    monkeypatch.setattr(session, "get_options", forbidden)
    start = time.monotonic()
    result = session.act("fill", ref, "Alice")
    assert result["ok"], result
    assert session.page.locator("input").input_value() == "Alice"
    assert time.monotonic() - start < 5


def test_generated_test_replays_quoted_css_locators(session, tmp_path):
    import inspect
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace
    from recorder_parser import generate_from_review

    assert session.act("fill", ref_for(session, "name"), "Alice")["ok"]
    sort_ref = ref_for(session, "sort")
    assert session.select_from_dropdown(sort_ref, "Price low to high")["ok"]
    assert session.add_checkpoint("Sort", "element_has_value", "lohi", sort_ref)["ok"]
    assert session.act("click", ref_for(session, "save"))["ok"]
    generate_from_review({"tc_id": "TC-LOCAL-001", "flowId": "local",
                          "steps": session.steps, "assertions": session.assertions}, str(tmp_path))
    flow = tmp_path / "tests" / "flows" / "local"
    generated = flow / "test_local.py"
    namespace = {"__file__": str(generated)}
    exec(compile(generated.read_text(encoding="utf-8"), str(generated), "exec"), namespace)
    session.page.reload()
    fixtures = {"page": session.page, "base_url": "https://fixture.test/", "admin_url": "",
                "tc_data": json.loads((flow / "test_data.json").read_text(encoding="utf-8"))["TC-LOCAL-001"],
                "checkpoints": SimpleNamespace(step=lambda *a, **kw: nullcontext(), mark_passed=lambda *a: None)}
    test = namespace["test_TC_LOCAL_001"]
    test(**{name: fixtures[name] for name in inspect.signature(test).parameters})
    assert session.page.locator("input").input_value() == "Alice"
    assert session.page.locator("select").input_value() == "lohi"
    assert session.page.locator("output").inner_text() == "Saved"


def test_replaced_node_cannot_receive_stale_action(session):
    ref = ref_for(session, "save")
    session.page.locator("button").evaluate("el => el.replaceWith(el.cloneNode(true))")
    result = session.act("click", ref)
    assert result.get("stale_ref"), result
    assert not session.steps
    assert session.page.locator("output").inner_text() == ""


def test_wrong_native_option_does_not_act(session):
    ref = ref_for(session, "sort")
    assert not session.select_from_dropdown(ref, "price")['ok']
    assert not session.steps


def test_same_label_product_actions_remain_distinguishable(session):
    session.page.set_content('<article>First product<button data-test="add-first">Add to cart</button></article>'
                             '<article>Second product<button data-test="add-second">Add to cart</button></article>')
    result = session.inspect()
    buttons = [item for item in result["elements"] if item.get("test_id") in {"add-first", "add-second"}]
    assert len(buttons) == 2
    assert {item["ref"] for item in buttons} == {"add-first", "add-second"}
    assert any("First product" in item["entity"] for item in buttons)
    assert any("Second product" in item["entity"] for item in buttons)


def test_equivalent_noop_is_bounded(session):
    ref = ref_for(session, "name")
    assert session.act("fill", ref, "")["ok"]
    assert session.act("fill", ref, "")["ok"]
    assert session.act("fill", ref, "")["retry_exhausted"]
    assert len(session.steps) == 2


def test_registered_goal_tool_uses_guided_gate_and_records_replay(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import agent_chat as ac
    from decision import Decision
    from goal_controller import GoalAction
    from planner_policy import GoalCheckpoint
    from provenance import ProvenanceTracker
    from pydantic_ai.models.test import TestModel
    class StubDecider:
        def __init__(self, *args, **kwargs):
            pass
        def choose(self, state, candidates):
            return Decision(next(key for key in candidates if key != "stop"), 0.99)
    monkeypatch.setattr(ac, "ChoiceDecider", StubDecider)
    async def scenario():
        session = ac.BrowserSession()
        await ac._bro(session.start, headless=True)
        try:
            await ac._bro(session.page.route, "https://fixture.test/**", lambda route: route.fulfill(body=HTML, content_type="text/html"))
            await ac._bro(session.navigate, "https://fixture.test/")
            await ac._bro(session.inspect)
            refs = {el.get("test_id"): ref for ref, el in session.by_ref.items() if el.get("test_id")}
            tracker = ProvenanceTracker()
            tracker.add_value("Price low to high", "page-option")
            queue = asyncio.Queue()
            queue.put_nowait("Alice")  # Human corrects the planner's unproven 'Bob'.
            deps = ac.Deps(session=session, ats_root=str(tmp_path), project=None,
                           config={"agent_execution": {"hybrid_enabled": True}, "llm": {"roles": {"decision": "stub"}}},
                           mode="guided", user_input_q=queue, provenance=tracker)
            agent = ac.build_agent(TestModel(call_tools=[]))
            tool = agent._function_toolset.tools["execute_goal"].function
            deps.plan = [{"step": "Save form", "status": "pending"}]
            result = await tool(SimpleNamespace(deps=deps), "Fill name, sort, then save", [
                GoalAction("fill", refs["name"], "Bob"),
                GoalAction("select", refs["sort"], "Price low to high"),
                GoalAction("click", refs["save"]),
            ], checkpoints=[GoalCheckpoint("Saved", "page_contains_text", "Saved")], plan_step=1)
            assert result["ok"], result
            assert result["checkpoints"][0]["ok"]
            assert deps.plan[0]["status"] == "done"
            assert "elements" in result["observation"]
            assert deps.recovery_actions == 0 and not deps.controller_active
            assert result["verified"] is False
            assert await ac._bro(session.page.locator("input").input_value) == "Alice"
            assert len(session.steps) == 4  # initial navigation plus three actions
            # Replay the recorded code on a fresh document, not the authoring state.
            def replay():
                session.page.reload()
                for step in session.steps:
                    exec(step["rawLine"], {"page": session.page})
                assert session.page.locator("input").input_value() == "Alice"
                assert session.page.locator("select").input_value() == "lohi"
                assert session.page.locator("output").inner_text() == "Saved"
            await ac._bro(replay)
            await ac._bro(session.inspect)
            name_ref = next(ref for ref, el in session.by_ref.items() if el.get("test_id") == "name")
            tracker.add_value("Eve", "user")
            failed = await tool(SimpleNamespace(deps=deps), "Change the name", [GoalAction("fill", name_ref, "Eve")],
                                checkpoints=[GoalCheckpoint("Wrong value", "element_has_value", "Wrong", name_ref)], plan_step=1)
            assert failed["status"] == "checkpoint_failed" and not failed["ok"]
            assert deps.plan[0]["status"] == "failed"
            assert deps.recovery_actions == 2 and not deps.controller_active
            assert len(session.assertions) == 1  # False checkpoints are never recorded.
        finally:
            await ac._bro(session.close)
    asyncio.run(scenario())


@pytest.mark.parametrize("overlay,modals,receives", [("inline", 0, True), ("modal", 1, False), ("cover", 0, False)])
def test_validation_context_distinguishes_messages_from_obstructions(session, overlay, modals, receives):
    session.page.evaluate("""kind => {
        const error = document.createElement('div');
        error.setAttribute('role', 'alert'); error.textContent = 'Name is required';
        document.body.append(error);
        if (kind !== 'inline') {
            const cover = document.createElement('div');
            cover.style.cssText = 'position:fixed;inset:0;z-index:99999;background:white';
            if (kind === 'modal') { cover.setAttribute('role','dialog'); cover.setAttribute('aria-modal','true'); }
            document.body.append(cover);
        }
    }""", overlay)
    observed = session.inspect()
    ref = next(ref for ref, el in session.by_ref.items() if el.get("test_id") == "name")
    field = next(el for el in observed["elements"] if el["ref"] == ref)
    assert observed["interaction_context"]["visible_modal_count"] == modals
    assert field["center_receives_pointer"] is receives
    assert observed["validation_errors"]
    assert "does not establish" in observed["validation_guidance"]


def test_restored_recording_replays_on_fresh_document(session):
    from recording_history import clear_recording
    from recorder_parser import _generate_assertion_code
    from playwright.sync_api import expect
    ref = ref_for(session, "sort")
    assert session.select_from_dropdown(ref, "Price low to high")["ok"]
    assert session.add_checkpoint("Sort value", "element_has_value", "lohi", ref)["ok"]
    cleared = clear_recording(session, "A concrete failed replay requires a corrected flow")
    assert not session.steps and not session.assertions
    assert session.recording_history.restore(session, cleared["snapshot_id"])["ok"]
    session.page.reload()
    for step in session.steps:
        exec(step["rawLine"], {"page": session.page})
    for assertion in session.assertions:
        exec(_generate_assertion_code(assertion), {"page": session.page, "expect": expect})
    assert session.page.locator("select").input_value() == "lohi"


def _emitted_page_statements(source):
    import ast
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef))
    return [n for n in ast.walk(function) if isinstance(n, (ast.Expr, ast.With))
            and ast.unparse(n).lstrip().startswith(("page.", "expect(", "with page."))]


def _run_statements(statements, page):
    import ast
    import re
    from playwright.sync_api import expect
    for statement in statements:
        exec(compile(ast.Module(body=[statement], type_ignores=[]), "<generated>", "exec"),
             {"page": page, "expect": expect, "re": re, "tc_data": {}})


def test_generated_scroll_and_set_checked_steps_replay(session, tmp_path):
    """Regression: scroll steps emitted `page.mouse.wheel(0, )` (TypeError) and a
    set_checked fallback got `.scroll_into_view_if_needed()` chained onto its None result."""
    from recorder_parser import generate_from_review
    session.page.set_content('<div style="height:3000px"></div><label><input id="agree" type="checkbox"> Agree</label>'
                             '<p id="far">Terms end here</p>')
    payload = {"tc_id": "TC-SCROLL-001", "flowId": "scrollcheck", "steps": [
        {"id": 1, "type": "scroll", "value": "",
         "rawLine": 'page.get_by_text("Terms end here").first.scroll_into_view_if_needed()'},
        {"id": 2, "type": "check", "rawLine": 'page.locator("#agree").set_checked(True, force=True)'}],
        "assertions": [], "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/scrollcheck/test_scrollcheck.py").read_text(encoding="utf-8")
    assert "mouse.wheel(0, )" not in source and ").scroll_into_view_if_needed()" not in source.split("set_checked")[1].split("\n")[0]
    _run_statements(_emitted_page_statements(source), session.page)
    assert session.page.locator("#agree").is_checked()


def test_pressed_keys_are_recorded_and_replayed(session, tmp_path):
    from recorder_parser import generate_from_review
    session.page.set_content('<form onsubmit="event.preventDefault();document.querySelector(\'output\').textContent=\'sent\'">'
                             '<input aria-label="Query"></form><output></output>')
    session.page.get_by_label("Query").focus()
    assert session.press_key("Enter")["ok"]
    assert session.steps[-1]["rawLine"] == 'page.keyboard.press("Enter")' and session.steps[-1]["type"] == "press"
    payload = {"tc_id": "TC-KEY-001", "flowId": "keys", "steps": session.steps, "assertions": [], "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/keys/test_keys.py").read_text(encoding="utf-8")
    assert 'page.keyboard.press("Enter")' in source


def test_generated_body_text_checks_use_visible_text(session, tmp_path):
    """Live checks read visible text; replay must not pass or fail on hidden text."""
    from recorder_parser import generate_from_review
    session.page.set_content('<p>Order placed</p><div hidden>Payment failed</div>')
    payload = {"tc_id": "TC-TEXT-001", "flowId": "visible", "steps": [
        {"id": 1, "type": "navigate", "rawLine": 'page.goto("https://fixture.test/")'}],
        "assertions": [{"type": "page_contains_text", "value": "Order placed", "afterStep": 1},
                       {"type": "page_not_contains_text", "value": "Payment failed", "afterStep": 1}],
        "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/visible/test_visible.py").read_text(encoding="utf-8")
    assert source.count("use_inner_text=True") == 2
    from playwright.sync_api import expect
    expect(session.page.locator("body")).not_to_contain_text("Payment failed", use_inner_text=True, timeout=1000)


def test_live_numeric_retyping_replaces_value_on_every_platform(session):
    """Control+a is not select-all on macOS; the live path must replace, not append."""
    session.page.set_content('<input type="number" aria-label="Units" value="0">')
    session.inspect()
    ref = next(r for r, el in session.by_ref.items() if el.get("name") == "Units")
    assert session.act("fill", ref, "7")["ok"]
    assert session.page.get_by_label("Units").input_value() == "7"
