import asyncio
from types import SimpleNamespace
import pytest
from decision import Decision
from decision_browser import BrowseContract, candidates, browse, state_for_decision
from planner_policy import prepare_planner_tools
from pydantic_ai.tools import ToolDefinition


def contract():
    return BrowseContract.model_validate({"start_url": "https://app.test/", "goal": "Find the empty list",
        "inputs": [{"name": "query", "value": "no-match"}], "milestones": [
            {"goal": "Search", "outcomes": [{"name": "Empty", "kind": "page_contains_text", "value": "No results"}]}]})


def test_start_url_outcome_cannot_supply_guessed_route():
    from decision_browser import Outcome
    assert Outcome(name="Entry", kind="url_matches_start").value == ""
    for fields in ({"value": "login.html"}, {"input_slot": "route"}):
        with pytest.raises(ValueError):
            Outcome(name="Entry", kind="url_matches_start", **fields)


def test_omitted_readback_uses_approved_slot_but_explicit_empty_checks_clearing():
    data = {"start_url": "https://app.test/", "goal": "Fill and clear", "inputs": [{"name": "query", "value": "example"}],
        "milestones": [{"goal": "Read back", "outcomes": [
            {"name": "Filled", "kind": "element_has_value", "input_slot": "query"}]},
            {"goal": "Clear", "inputs": [], "outcomes": [
            {"name": "Cleared", "kind": "element_has_value", "input_slot": "query", "value": ""}]}]}
    task = BrowseContract.model_validate(data)
    assert [m.outcomes[0].value for m in task.milestones] == ["example", ""]
    assert BrowseContract.model_validate(task.model_dump()) == task


def test_null_readback_derives_value_and_contradictions_fail_before_browsing():
    data = contract().model_dump()
    data['milestones'][0].update(inputs=['query'], outcomes=[
        {'name': 'Readback', 'kind': 'element_has_value', 'input_slot': 'query', 'value': None}])
    assert BrowseContract.model_validate(data).milestones[0].outcomes[0].value == 'no-match'
    data['milestones'][0]['outcomes'][0]['value'] = ''
    with pytest.raises(ValueError, match='conflicts with its required readback'):
        BrowseContract.model_validate(data)
    data['milestones'][0]['inputs'] = []
    assert BrowseContract.model_validate(data).milestones[0].outcomes[0].value == ''
    data['milestones'][0]['outcomes'].append({'name': 'Other', 'kind': 'element_has_value', 'input_slot': 'query', 'value': 'x'})
    with pytest.raises(ValueError, match='conflicting simultaneous'):
        BrowseContract.model_validate(data)
    data['milestones'][0]['outcomes'] = [{'name': kind, 'kind': kind, 'value': 'same'}
        for kind in ('page_contains_text', 'page_not_contains_text')]
    with pytest.raises(ValueError, match='present and absent'):
        BrowseContract.model_validate(data)


def test_route_reference_must_be_backward_and_cannot_be_guessed():
    from decision_browser import Outcome
    for fields in ({}, {"milestone_index": 0, "value": "/guessed"}):
        with pytest.raises(ValueError):
            Outcome(name="Return", kind="url_matches_milestone", **fields)
    data = contract().model_dump()
    data["milestones"][0]["outcomes"] = [{"name": "Self", "kind": "url_matches_milestone", "milestone_index": 0}]
    with pytest.raises(ValueError, match="earlier"):
        BrowseContract.model_validate(data)


def test_compact_state_roundtrips_all_evidence_without_dropping_targets():
    import json
    from decision_browser import compact_decision_state
    state = state_for_decision(observation())
    state["elements"] += [{"ref": "nullable", "name": None}, {"ref": "missing"}]
    packed = compact_decision_state(state)
    restored = [{key: row[i] for i, key in enumerate(packed["element_columns"]) if mask & (1 << i)}
                for row, mask in zip(packed["element_rows"], packed["element_presence"])]
    assert restored == state["elements"]
    assert packed["url"] == state["url"] and packed["text"] == state["text"]
    assert "secret" not in json.dumps(packed)
    assert len(json.dumps(packed)) < len(json.dumps(state))


def observation(url="https://app.test/"):
    return {"url": url, "targets": [
        {"ref": "companies", "node_id": "doc:1", "tag": "a", "name": "Companies", "href": "/companies"},
        {"ref": "contacts", "node_id": "doc:2", "tag": "a", "name": "Contacts", "href": "/contacts"},
        {"ref": "query", "node_id": "doc:3", "tag": "input", "input_type": "search", "name": "Search", "value": ""},
        {"ref": "save", "node_id": "doc:4", "tag": "button", "name": "Save"},
        {"ref": "offsite", "node_id": "doc:5", "tag": "a", "href": "https://other.test/", "name": "Other"},
        {"ref": "password", "node_id": "doc:6", "tag": "input", "input_type": "password", "name": "Search", "value": "secret"}]}


def test_candidates_are_dom_derived_and_read_only():
    rows = candidates(observation(), contract())
    assert {c.ref for c in rows.values() if c.ref} == {"companies", "contacts", "query"}
    assert "secret" not in str(state_for_decision(observation()))
    assert "no-match" not in str(rows)


def test_local_forms_are_explicit_and_options_must_be_grounded():
    task = contract()
    task.inputs[0].value = "Industrials"
    obs = observation()
    obs["targets"] += [
        {"ref": "sector", "node_id": "doc:7", "tag": "select", "name": "Sector",
         "options": [{"label": "Industrials", "value": "industry", "selected": False}]},
        {"ref": "description", "node_id": "doc:8", "tag": "textarea", "name": "Description", "value": ""},
        {"ref": "delete", "node_id": "doc:9", "tag": "button", "name": "Delete"},
        {"ref": "option", "node_id": "doc:10", "tag": "div", "role": "option", "name": "Industrials"},
        {"ref": "unknown", "node_id": "doc:11", "tag": "div", "role": "option", "name": "Invented"},
        {"ref": "disabled", "node_id": "doc:12", "tag": "button", "name": "Save", "disabled": True}]
    rows = candidates(obs, task, "session_local_forms")
    refs = {c.ref for c in rows.values()}
    assert {"save", "sector", "description", "option"} <= refs
    assert not {"password", "offsite", "delete", "unknown", "disabled"} & refs
    assert next(c for c in rows.values() if c.ref == "sector").kind == "select"
    assert not {"save", "sector", "description", "option"} & {c.ref for c in candidates(obs, task).values()}
    with pytest.raises(ValueError):
        candidates(obs, task, "unrestricted")


def test_disabled_and_ambiguous_native_options_are_not_candidates():
    task, obs = contract(), observation()
    obs["targets"] = [{"ref": "select", "node_id": "d:1", "tag": "select", "name": "Select",
                       "options": [{"label": "no-match", "value": "1", "disabled": True}]}]
    assert not any(c.kind == "select" for c in candidates(obs, task, "session_local_forms").values())
    obs["targets"][0]["options"] = [{"label": "no-match", "value": "1"}, {"label": "no-match", "value": "2"}]
    assert not any(c.kind == "select" for c in candidates(obs, task, "session_local_forms").values())


def test_public_demo_password_capability_requires_both_host_flag_and_local_policy():
    obs = observation()
    obs['public_demo_auth'] = True
    assert any(c.ref == 'password' and c.kind == 'fill' for c in candidates(obs, contract(), 'session_local_forms').values())
    assert not any(c.ref == 'password' for c in candidates(obs, contract(), 'read_only').values())
    obs['public_demo_auth'] = False
    assert not any(c.ref == 'password' for c in candidates(obs, contract(), 'session_local_forms').values())


def test_local_combobox_open_is_separate_from_submit_and_default_policy():
    obs = observation()
    obs['targets'].append({'ref': 'sector', 'node_id': 'd:7', 'tag': 'button', 'role': 'combobox', 'name': 'Sector'})
    rows = candidates(obs, contract(), 'session_local_forms')
    assert next(c for c in rows.values() if c.ref == 'sector').kind == 'open'
    assert next(c for c in rows.values() if c.ref == 'save').kind == 'click'
    assert not any(c.ref == 'sector' for c in candidates(obs, contract()).values())
    assert next(c for c in rows.values() if c.ref == 'companies').kind == 'navigate_link'
    assert next(c for c in candidates(obs, contract()).values() if c.ref == 'companies').kind == 'click'


def test_overflow_is_explicit_not_truncated():
    obs = observation()
    obs["targets"] *= 100
    with pytest.raises(ValueError):
        candidates(obs, contract())


def test_decider_controls_multiple_routes_and_verification_without_planner():
    obs, selected = observation(), []
    class Decider:
        def choose(self, state, choices):
            selected.append(choices)
            if len(selected) == 1:
                assert any("Companies" in v for v in choices.values())
                assert any("Contacts" in v for v in choices.values())
                return Decision(next(k for k, v in choices.items() if "Companies" in v), .99)
            return Decision("check", .99)
    async def observe(): return obs
    async def perform(action):
        obs["url"] = "https://app.test/companies"
        return {"ok": True}
    async def check(m): return {"ok": True}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider()))
    assert result["ok"] and not result["verified"]
    assert len(selected) == 2 and len(result["trace"]) == 2


@pytest.mark.parametrize("case,expected", [("stop", "decision_handoff"), ("invalid", "invalid_decision"),
    ("error", "decision_error"), ("low", "decision_handoff"), ("uncertain", "action_uncertain")])
def test_failures_do_not_unlock_planner_actions(case, expected):
    class Decider:
        def choose(self, state, choices):
            if case == "error": raise RuntimeError("provider failed")
            if case in {"uncertain", "low"}: return Decision(next(k for k, v in choices.items() if "Companies" in v), .2 if case == "low" else .99)
            return Decision(case, .99)
    async def observe(): return observation()
    async def perform(a): return {"ok": False}
    async def check(m): return {"ok": False}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider()))
    assert result["status"] == expected and not result["verified"]


def test_repeated_failed_checks_are_removed_and_budgets_terminate():
    calls = []
    class Decider:
        def choose(self, state, choices):
            calls.append(choices)
            return Decision("check" if "check" in choices else "stop", .2)
    async def observe(): return observation()
    async def perform(a): raise AssertionError("No actions allowed in this test")
    async def check(m): return {"ok": False}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider()))
    assert result["status"] == "decision_handoff" and len(calls) == 3
    assert "check" not in calls[-1]


@pytest.mark.parametrize("choice,status", [("check", "outcomes_observed"), ("stop", "decision_handoff")])
def test_ready_milestone_blocks_mutations_until_decision_commits_checks(choice, status):
    task = contract()
    from decision_browser import Outcome
    task.milestones[0].outcomes = [Outcome(name="Readback", kind="element_has_value", input_slot="query", value="no-match")]
    class Decider:
        def choose(self, state, choices):
            assert set(choices) == {"check", "wait", "stop"}
            return Decision(choice, .99)
    async def observe(): return observation()
    async def perform(action): raise AssertionError("Ready readback must not be invalidated")
    async def check(milestone): return {"ok": True}
    async def probe(milestone): return {"ok": True}
    result = asyncio.run(browse(task, observe, perform, check, Decider(),
                               policy="session_local_forms", probe=probe))
    assert result["status"] == status
    assert result["milestones_completed"] == (1 if choice == "check" else 0)


def test_shared_ready_text_does_not_block_required_navigation():
    visited = []
    class Decider:
        def choose(self, state, choices):
            if not visited:
                return Decision(next(k for k, v in choices.items() if "Companies" in v), .99)
            return Decision("check", .99)
    async def observe(): return observation()
    async def perform(action):
        visited.append(action.ref)
        return {"ok": True}
    async def check(milestone): return {"ok": True}
    async def probe(milestone): return {"ok": True}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider(), probe=probe))
    assert result["ok"] and visited == ["companies"]


@pytest.mark.parametrize("readback_ok", [True, False])
def test_readonly_binding_is_not_a_mutation_but_readback_remains_required(readback_ok):
    from decision_browser import Outcome
    task = contract()
    task.milestones[0].outcomes = [Outcome(name="Cleared", kind="element_has_value", input_slot="query", value="")]
    actions = []
    class Decider:
        def choose(self, state, choices):
            if not actions:
                return Decision(next(k for k, v in choices.items() if v.startswith("Bind input slot")), .62)
            return Decision("check" if "check" in choices else "stop", .99)
    async def observe():
        return {"ok": True, "url": task.start_url, "targets": [{"ref": "search", "node_id": "doc:1",
            "tag": "input", "role": "textbox", "input_type": "search", "name": "Search", "value": "",
            "bound_inputs": ["query"] if actions else []}]}
    async def perform(action):
        assert action.kind == "bind"
        actions.append(action)
        return {"ok": True, "dispatched": False}
    async def check(milestone): return {"ok": readback_ok}
    result = asyncio.run(browse(task, observe, perform, check, Decider()))
    assert len(actions) == 1
    assert result["ok"] is readback_ok


def test_native_combobox_offers_typed_selection_not_click():
    task = contract()
    obs = observation()
    obs["targets"] = [{"ref": "sort", "node_id": "doc:7", "tag": "select",
                       "role": "combobox", "name": "Sort", "value": "old",
                       "options": [{"value": "no-match", "label": "New", "selected": False}]}]
    actions = list(candidates(obs, task, "session_local_forms").values())
    assert any(a.kind == "select" and a.ref == "sort" for a in actions)
    assert not any(a.kind in {"click", "open"} for a in actions)


@pytest.mark.parametrize("second,changed,expected,dispatches", [
    (.95, False, "outcomes_observed", 1),
    (.74, False, "decision_handoff", 0),
    (None, False, "decision_handoff", 0),
    (.95, True, "confirmation_state_changed", 0)])
def test_local_click_confirmation_is_bounded_and_requires_fresh_identity(second, changed, expected, dispatches):
    calls, actions, events = [], [], []
    obs = observation()
    class Decider:
        def choose(self, state, choices):
            calls.append(state)
            if actions:
                return Decision("check", .99)
            if len(calls) == 2:
                assert state["pending_confirmation"]
                assert any("Contacts" in v for v in choices.values())
            return Decision(next(k for k, v in choices.items() if "Save" in v), .74 if len(calls) == 1 else second)
    async def observe():
        if changed and calls:
            obs["targets"][3]["node_id"] = "replacement:4"
        return obs
    async def perform(a):
        actions.append(a)
        return {"ok": True}
    async def check(m): return {"ok": True}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider(),
                               policy="session_local_forms", emit=events.append))
    assert result["status"] == expected
    assert len(actions) == dispatches
    assert len(calls) <= 3
    assert sum(e.get("status") == "requested" for e in events) == 1


def test_outer_planner_has_no_browser_action_or_checkpoint_tools():
    names = ["browse_goal", "navigate", "observe", "click", "execute_goal", "add_checkpoint", "create_test_case", "run_test_case"]
    deps = SimpleNamespace(config={"agent_execution": {"decision_loop_enabled": True}})
    defs = [ToolDefinition(name=n) for n in names]
    assert [d.name for d in prepare_planner_tools(SimpleNamespace(deps=deps), defs)] == ["browse_goal"]
    deps.decision_browser_complete = True
    assert {d.name for d in prepare_planner_tools(SimpleNamespace(deps=deps), defs)} == {"browse_goal", "create_test_case", "run_test_case"}


@pytest.mark.parametrize("dispatched,recoveries", [(False, 2), (True, 0), (None, 0)])
def test_stale_recovery_requires_explicit_non_dispatch_and_is_bounded(dispatched, recoveries):
    events, calls = [], []
    class Decider:
        def choose(self, state, choices):
            return Decision(next(k for k, v in choices.items() if "Companies" in v), .99)
    async def observe():
        obs = observation()
        obs["rendered_text"] = str(len(calls))
        return obs
    async def perform(a):
        calls.append(a)
        return {"ok": False, "stale_ref": True, "dispatched": dispatched}
    async def check(m): return {"ok": False}
    result = asyncio.run(browse(contract(), observe, perform, check, Decider(), emit=events.append))
    assert result["status"] == "action_uncertain"
    assert len(calls) == recoveries + 1
    assert sum(e["event"] == "decision_browser_recovery" for e in events) == recoveries


def test_contract_has_no_action_refs_and_supports_clear_value():
    data = contract().model_dump()
    data["milestones"][0]["outcomes"] = [{"name": "Cleared", "kind": "element_has_value", "value": "", "input_slot": "query"}]
    assert BrowseContract.model_validate(data)
    data["actions"] = [{"kind": "click", "ref": "chosen-by-planner"}]
    with pytest.raises(ValueError): BrowseContract.model_validate(data)


def test_runtime_guards_and_settings_do_not_expose_legacy_control_or_credentials(tmp_path):
    import agent_chat as ac
    from pydantic_ai.models.test import TestModel
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True, decision_loop=True)
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path),
        config={"agent_execution": {"hybrid_enabled": True, "decision_loop_enabled": True}},
        project={"apps": [{"label": "Demo", "url": "https://app.test/", "credentials": {"password": "do-not-expose"}}]})
    ctx = SimpleNamespace(deps=deps)
    async def run():
        tool = lambda name: agent._function_toolset.tools[name].function
        assert (await tool("navigate")(ctx, "https://app.test/"))["status"] == "decision_loop_required"
        assert (await tool("execute_goal")(ctx, "Legacy step", []))["status"] == "decision_loop_required"
        assert (await tool("add_checkpoint")(ctx, "Weak", "url_contains", "/"))["status"] == "decision_loop_required"
        assert (await tool("create_test_case")(ctx, "TC-X-001", "x", "Too early"))["status"] == "outcomes_incomplete"
        settings = await tool("get_settings")(ctx)
        assert settings["apps"] == [{"label": "Demo", "url": "https://app.test/"}]
        assert "do-not-expose" not in str(settings)
        deps.config['agent_execution']['required_outcome_groups'] = [
            {'name': 'persisted value', 'outcomes': [{'kind': 'page_contains_text', 'value': 'Required saved value'}]}]
        rejected = await tool('browse_goal')(ctx, contract())
        assert rejected['status'] == 'contract_coverage_incomplete'
        assert rejected['dispatched'] is False
        assert not getattr(deps, 'decision_browser_used', False)
        deps.config['agent_execution']['required_outcome_groups'].append(
            {'name': 'second missing outcome', 'outcomes': [{'kind': 'page_contains_text', 'value': 'Another saved value'}]})
        rejected = await tool('browse_goal')(ctx, contract())
        assert len(rejected['missing_groups']) == 2
        assert (await tool('browse_goal')(ctx, contract()))['status'] == 'contract_coverage_retry_exhausted'
        deps.config['agent_execution']['required_outcome_groups'] = []
        assert (await tool('browse_goal')(ctx, contract()))['status'] == 'contract_coverage_retry_exhausted'
    asyncio.run(run())
