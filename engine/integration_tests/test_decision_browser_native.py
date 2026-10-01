import asyncio
import ast
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect
from decision import Decision
from decision_browser import BrowseContract, browse, candidates
from decision_browser_tools import BrowserBridge
from recorder_parser import generate_from_review
from test_browser_grounding import session


def test_previous_form_input_does_not_satisfy_later_form(session):
    from decision_browser import Milestone
    contract = setup(session)
    bridge = BrowserBridge(session, contract, 'session_local_forms')
    first = Milestone(goal='First form', inputs=['query'], outcomes=[
        {'name': 'First', 'kind': 'page_contains_text', 'value': 'Companies'}])
    session.page.get_by_role('searchbox', name='Search').fill('no-match')
    assert bridge.check(first)['ok']
    assert 'query' in bridge.applied_inputs
    session.page.set_content('<main><h1>Second form</h1><input aria-label="Company" value=""></main>')
    second = Milestone(goal='Second form relationship', inputs=['query'], outcomes=[
        {'name': 'Second', 'kind': 'page_contains_text', 'value': 'Second form'}])
    result = bridge.check(second)
    assert result['status'] == 'required_inputs_pending'
    assert result['input_slots'] == ['query']


def setup(session):
    session.page.evaluate("history.replaceState(null, '', '#/companies')")
    session.page.set_content('''<main><h1>Companies</h1><input aria-label="Search" type="search"
      oninput="document.querySelector('#rows').hidden=!!this.value; document.querySelector('#empty').hidden=!this.value">
      <button onclick="document.querySelector('input').value=''; document.querySelector('input').dispatchEvent(new Event('input'))">Clear search</button>
      <p id="empty" hidden>No companies found</p><div id="rows"><a href="#/companies/123">Company A</a></div></main>''')
    return BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Search then restore the list",
        "inputs": [{"name": "query", "value": "no-match"}], "milestones": [
        {"goal": "No matches", "outcomes": [
            {"name": "Query", "kind": "element_has_value", "value": "no-match", "input_slot": "query"},
            {"name": "Empty", "kind": "page_contains_text", "value": "No companies found"}]},
        {"goal": "Restored", "outcomes": [
            {"name": "Cleared", "kind": "element_has_value", "value": "", "input_slot": "query"},
            {"name": "Rows", "kind": "links_present", "value": "#/companies/"}]}]})


@pytest.mark.parametrize("discovery", [False, True])
def test_native_loop_records_positive_collection_and_empty_value(tmp_path, discovery):
    from playwright.sync_api import sync_playwright
    from agent_chat import BrowserSession, _bro
    class Decider:
        turns = 0
        def choose(self, state, choices):
            self.turns += 1
            if self.turns in (2, 4): return Decision("check", .99)
            wanted = "fill " if self.turns == 1 else "Clear search"
            return Decision(next(k for k, v in choices.items() if wanted in v), .99)
    def start():
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        native = BrowserSession()
        native.browser, native.context, native.page = browser, context, context.new_page()
        native.page.route("https://fixture.test/**", lambda r: r.fulfill(body="<html></html>", content_type="text/html"))
        native.page.goto("https://fixture.test/")
        contract = setup(native)
        if discovery:
            from decision_browser import Outcome
            contract.milestones[0].outcomes[1] = Outcome(name="Empty", kind="observed_empty_results")
            contract.milestones[1].outcomes[1] = Outcome(name="Rows", kind="record_links_present")
        return pw, browser, BrowserBridge(native, contract)
    async def run():
        pw, browser, bridge = await _bro(start)
        try:
            result = await browse(bridge.contract, lambda: _bro(bridge.snapshot),
                lambda a: _bro(bridge.act, a, bridge.inputs.get(a.slot)),
                lambda m: _bro(bridge.check, m), Decider())
            return result, bridge.session.steps, bridge.session.assertions
        finally:
            await _bro(browser.close)
            await _bro(pw.stop)
    result, steps, assertions = asyncio.run(run())
    assert result["ok"] and result["milestones_completed"] == 2
    assert [a["type"] for a in assertions] == ["locator_has_value", "page_contains_text", "url_contains", "locator_has_value", "collection_nonempty", "url_contains"]
    payload = {"tc_id": "TC-LOOP-001", "flowId": "loop", "description": "Decision browsing",
               "steps": steps, "assertions": assertions, "criteria": []}
    assert generate_from_review(payload, str(tmp_path))["status"] == "success"
    source = (tmp_path / "tests/flows/loop/test_loop.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '.first).to_be_visible' in source
    assert ".to_have_value(''" in source


def test_missing_rows_fails_and_does_not_commit_partial_milestone(session):
    bridge = BrowserBridge(session, setup(session))
    fill = next(c for c in candidates(bridge.snapshot(), bridge.contract).values() if c.kind == "fill")
    assert bridge.act(fill, "no-match")["ok"]
    assert bridge.probe(bridge.contract.milestones[0])["ok"]
    assert session.assertions == []
    # The first value assertion is true, but the second required outcome fails.
    from decision_browser import Milestone
    milestone = Milestone.model_validate({"goal": "Wrong rows", "outcomes": [
        {"name": "Query", "kind": "element_has_value", "value": "no-match", "input_slot": "query"},
        {"name": "Rows", "kind": "links_present", "value": "#/companies/"}]})
    assert not bridge.check(milestone)["ok"]
    assert session.assertions == []


def test_replaced_target_is_rejected_before_dispatch(session):
    bridge = BrowserBridge(session, setup(session))
    action = next(c for c in candidates(bridge.snapshot(), bridge.contract).values() if c.kind == "fill")
    session.page.locator('input').evaluate('e => e.replaceWith(e.cloneNode(true))')
    assert bridge.act(action, "no-match")["status"] == "stale_target"
    assert session.page.locator('input').input_value() == ""


def test_local_form_native_select_records_actual_option_and_rechecks_identity(session):
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Select sector",
        "inputs": [{"name": "sector", "value": "Industrials"}], "milestones": [
            {"goal": "Selected sector", "outcomes": [{"name": "Selected", "kind": "element_has_value",
              "input_slot": "sector", "value": "industry"}]}]})
    session.page.set_content('<select aria-label="Sector"><option value="">Choose</option><option value="industry">Industrials</option></select>')
    bridge = BrowserBridge(session, task, "session_local_forms")
    action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy).values() if c.kind == "select")
    assert bridge.act(action, "Industrials")["ok"]
    assert bridge.check(task.milestones[0])["ok"]
    assert '.select_option("industry")' in session.steps[-1]["rawLine"]
    assert session.assertions[0]["value"] == "industry"


def test_untouched_and_recreated_fields_can_be_explicitly_bound_for_readback(session):
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Check untouched form",
        "inputs": [{"name": "company_name", "value": "QA company"}], "milestones": [
            {"goal": "Untouched name", "outcomes": [{"name": "Empty name", "kind": "element_has_value",
              "input_slot": "company_name", "value": ""}]}]})
    session.page.set_content('<input aria-label="Company name">')
    bridge = BrowserBridge(session, task, "session_local_forms")
    for _ in range(2):
        assert bridge.probe(task.milestones[0])["status"] == "input_needs_binding"
        action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy, task.milestones[0]).values() if c.kind == "bind")
        assert bridge.act(action) == {"ok": True, "status": "field_bound", "dispatched": False}
        assert bridge.check(task.milestones[0])["ok"]
        assert session.steps == []  # Binding cannot fabricate an input action.
        session.page.locator('input').evaluate('e => e.replaceWith(e.cloneNode(true))')


def test_unique_label_fallback_cannot_replace_the_grounded_input(session):
    session.page.set_content('<label for="company">Company name <span aria-hidden="true">*</span></label>'
                             '<input id="company" placeholder="Company name">')
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Fill actual input",
        "inputs": [{"name": "company_name", "value": "QA company"}], "milestones": [
            {"goal": "Read back", "outcomes": [{"name": "Name", "kind": "element_has_value",
              "input_slot": "company_name", "value": "QA company"}]}]})
    bridge = BrowserBridge(session, task, "session_local_forms")
    action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy).values() if c.kind == "fill")
    assert session.by_ref[action.ref]["name"] == "Company name *"
    assert bridge.act(action, "QA company")["ok"]
    assert bridge.check(task.milestones[0])["ok"]
    assert 'get_by_placeholder("Company name")' in session.steps[-1]["rawLine"]
    assert 'placeholder' in session.assertions[0]["selector"]


def test_named_form_fallback_survives_generated_id_changes(session):
    from recorder_parser import _generate_assertion_code
    session.page.set_content('<label for="r12">First name <span aria-hidden="true">*</span></label><input id="r12" name="first_name">')
    session.inspect()
    ref = next(r for r, el in session.by_ref.items() if el["tag"] == "input")
    assert session.act('fill', ref, 'Qamate')["ok"]
    assert session.add_checkpoint('First name', 'element_has_value', 'Qamate', ref)["ok"]
    recorded = session.steps[-1]["rawLine"]
    assertion = _generate_assertion_code(session.assertions[-1])
    assert 'name=' in recorded and 'r12' not in recorded
    session.page.set_content('<label for="r77">First name <span aria-hidden="true">*</span></label><input id="r77" name="first_name">')
    exec(recorded, {"page": session.page})
    exec(assertion, {"page": session.page, "expect": expect})


def test_card_link_records_ancestor_not_heading_or_generated_record_id(session):
    session.page.set_content('<a href="#/companies/123"><h3>QA company</h3><span>Industrials</span></a>')
    session.inspect()
    ref = next(r for r, el in session.by_ref.items() if el["tag"] == "a")
    assert session.act("click", ref)["ok"]
    recorded = session.steps[-1]["rawLine"]
    assert "123" not in recorded
    session.page.set_content('<a href="#/companies/456"><h3>QA company</h3><span>Industrials</span></a>')
    exec(recorded, {"page": session.page})
    assert session.page.url.endswith("#/companies/456")


def test_combobox_search_keeps_nearby_field_context_separate_from_locator_name(session):
    session.page.set_content('<div><label>Company</label><button role="combobox">Search</button></div>')
    session.inspect()
    element = next(el for el in session.by_ref.values() if el["role"] == "combobox")
    assert element["name"] == "Search"
    assert element["field_context"] == "Company"


def test_aria_hidden_combobox_text_records_button_not_child(session):
    session.page.set_content('<label>Company</label><button role="combobox" onclick="this.dataset.clicked=1"><span aria-hidden="true">Search</span></button>')
    session.inspect()
    ref = next(r for r, e in session.by_ref.items() if e["role"] == "combobox")
    assert session.act("click", ref)["ok"]
    assert 'button:has-text' in session.steps[-1]["rawLine"]
    assert session.page.locator("button").get_attribute("data-clicked") == "1"


def test_context_locator_multiple_matches_records_grounded_ordinal(session):
    session.page.set_content('<label>Sector</label><button role="combobox" id="sector"></button><label>Size</label><button role="combobox" id="size"></button>')
    session.inspect()
    ref = next(r for r, e in session.by_ref.items() if e.get("field_context") == "Sector")
    el = session.by_ref[ref]
    el["primary"] = {"by": "css", "value": '[role="combobox"]'}
    el.pop("match_index", None)
    assert session.act("click", ref)["ok"]
    recorded = session.steps[-1]["rawLine"]
    assert '.nth(0)' in recorded
    session.page.set_content('<button role="combobox" onclick="this.dataset.clicked=1"></button><button role="combobox"></button>')
    exec(recorded, {"page": session.page})
    assert session.page.locator('button').nth(0).get_attribute('data-clicked') == '1'


def test_entity_link_replays_with_later_duplicate_rendering(session):
    session.page.set_content('<a href="#/companies/1/show">QA company</a>')
    session.inspect()
    ref = next(r for r, e in session.by_ref.items() if e["tag"] == "a")
    assert session.act("click", ref)["ok"]
    recorded = session.steps[-1]["rawLine"]
    session.page.set_content('<a href="#/companies/2/show">QA company</a><aside><a href="#/companies/2/show">QA company</a></aside>')
    exec(recorded, {"page": session.page})
    assert session.page.url.endswith('#/companies/2/show')


def test_button_combobox_selected_value_can_bind_and_assert_exact_text(session):
    from recorder_parser import _generate_assertion_code
    task = BrowseContract.model_validate({'start_url': 'https://fixture.test/', 'goal': 'Selected company',
        'inputs': [{'name': 'company', 'value': 'QA company'}], 'milestones': [{'goal': 'Selection retained',
        'outcomes': [{'name': 'Company', 'kind': 'element_has_value', 'input_slot': 'company', 'value': 'QA company'}]}]})
    session.page.set_content('<button role="combobox" aria-expanded="false">QA company</button>')
    bridge = BrowserBridge(session, task, 'session_local_forms')
    bridge.probe(task.milestones[0])
    binding = next(c for c in candidates(bridge.snapshot(), task, bridge.policy, task.milestones[0]).values() if c.kind == 'bind')
    assert bridge.act(binding)['ok']
    assert bridge.check(task.milestones[0])['ok']
    assertion = dict(session.assertions[0], timeout=200)
    assert assertion['type'] == 'locator_has_text'
    exec(_generate_assertion_code(assertion), {'page': session.page, 'expect': expect})
    session.page.locator('button').evaluate("e => e.textContent = 'Wrong company'")
    with pytest.raises(AssertionError):
        exec(_generate_assertion_code(assertion), {'page': session.page, 'expect': expect})


def test_milestone_cannot_skip_required_dropdown_input(session):
    task = setup(session)
    from decision_browser import InputSlot
    task.inputs.append(InputSlot(name='sector', value='Industrials'))
    task.milestones[0].inputs = ['query', 'sector']
    bridge = BrowserBridge(session, task, 'session_local_forms')
    action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy).values() if c.kind == 'fill' and c.slot == 'query')
    assert bridge.act(action, 'no-match')["ok"]
    assert bridge.check(task.milestones[0]) == {"ok": False, "status": "required_inputs_pending", "input_slots": ['sector']}
    assert session.assertions == []


def test_local_form_textarea_fill_and_save_are_recorded_not_assumed(session):
    task = BrowseContract.model_validate({"start_url": "https://fixture.test/", "goal": "Save description",
        "inputs": [{"name": "description", "value": "Synthetic QA record"}], "milestones": [
            {"goal": "Saved", "inputs": ["description"], "outcomes": [{"name": "Saved description", "kind": "page_contains_text", "value": "Synthetic QA record"}]}]})
    session.page.set_content('<textarea aria-label="Description"></textarea><button onclick="document.querySelector(\'output\').textContent=document.querySelector(\'textarea\').value">Save</button><output></output>')
    bridge = BrowserBridge(session, task, "session_local_forms")
    assert not bridge.probe(task.milestones[0])["ok"]
    action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy).values() if c.kind == "fill")
    assert bridge.act(action, "Synthetic QA record")["ok"]
    assert not bridge.probe(task.milestones[0])["ok"]
    action = next(c for c in candidates(bridge.snapshot(), task, bridge.policy).values() if c.label == "Save")
    assert bridge.act(action)["ok"]
    assert bridge.check(task.milestones[0])["ok"]
    assert [s["type"] for s in session.steps] == ["fill", "click"]


@pytest.mark.parametrize("fault", [None, "hidden", "create_only"])
def test_generated_collection_assertion_rejects_planted_wrong_results(session, fault):
    from recorder_parser import _generate_assertion_code
    setup(session)
    selector = 'a[href^="#/companies/"]:visible:not([href$="/create"]):not([href$="/new"]):not([href$="/import"])'
    emitted = _generate_assertion_code({"type": "collection_nonempty", "selector": selector, "timeout": 250})
    if fault == "hidden":
        session.page.locator('#rows').evaluate('e => e.hidden = true')
    elif fault == "create_only":
        session.page.locator('#rows').evaluate('e => e.innerHTML = \'<a href="#/companies/create">Create Company</a>\'')
    if fault:
        with pytest.raises(AssertionError):
            exec(emitted, {"page": session.page, "expect": expect})
    else:
        exec(emitted, {"page": session.page, "expect": expect})
