import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from planner_policy import CORE, GROUPS, prepare_planner_tools, direct_action_allowed
from planner_policy import GoalCheckpoints, goal_plan_status
from pydantic import TypeAdapter, ValidationError
import agent_chat as ac


def deps(enabled=True):
    return SimpleNamespace(config={"agent_execution": {"hybrid_enabled": enabled}},
                           tool_group="", recovery_actions=0, controller_active=False)


def test_hybrid_routes_grounded_actions_and_preserves_core():
    d = deps()
    names = CORE | {"click", "fill", "select_option", "delete_test_case", "read_context_file"}
    definitions = [ToolDefinition(name=n) for n in names]
    visible = {t.name for t in prepare_planner_tools(SimpleNamespace(deps=d), definitions)}
    assert visible == CORE
    d.tool_group = "context"
    visible = {t.name for t in prepare_planner_tools(SimpleNamespace(deps=d), definitions)}
    assert "read_context_file" in visible and "fill" not in visible
    d.recovery_actions = 2
    assert direct_action_allowed(d)
    assert direct_action_allowed(d)
    assert not direct_action_allowed(d)
    d.controller_active = True
    assert direct_action_allowed(d) and d.recovery_actions == 0


def test_normal_mode_preserved():
    definitions = [ToolDefinition(name=n) for n in ("click", "request_tool_group", "read_memory")]
    assert {t.name for t in prepare_planner_tools(SimpleNamespace(deps=deps(False)), definitions)} == {"click", "read_memory"}
    assert direct_action_allowed(deps(False))


@pytest.mark.parametrize("enabled", [True, False])
def test_unconfigured_visual_tools_are_not_advertised(enabled):
    d = deps(enabled)
    d.tool_group = "diagnostics"
    definitions = [ToolDefinition(name=n) for n in ("look", "find_on_screen", "observe")]
    assert {t.name for t in prepare_planner_tools(SimpleNamespace(deps=d), definitions)} == {"observe"}
    d.config["vision_oracle"] = {"protocol": "ollama", "model": "vision"}
    assert {t.name for t in prepare_planner_tools(SimpleNamespace(deps=d), definitions)} == {"look", "find_on_screen", "observe"}


@pytest.mark.parametrize("name,args", [("look", {}), ("find_on_screen", {"description": "search"})])
def test_cached_visual_calls_fail_without_screenshot_or_provider(name, args):
    agent = ac.build_agent(TestModel(call_tools=[]))
    result = asyncio.run(agent._function_toolset.tools[name].function(SimpleNamespace(deps=deps()), **args))
    assert result == {"ok": False, "status": "vision_unavailable", "reason": "vision_not_configured"}


def test_terminal_vision_rejection_disables_repeat_calls(monkeypatch):
    calls = []
    def reject(*args):
        calls.append(True)
        raise ac._llm.LLMError("Unsupported model", status_code=404)
    monkeypatch.setattr(ac._llm, "call_vision_oracle", reject)
    cfg = {"vision_oracle": {"protocol": "ollama", "model": "vision"}}
    assert "error" in asyncio.run(ac._call_vision_oracle(cfg, b"image"))
    assert "unavailable" in asyncio.run(ac._call_vision_oracle(cfg, b"image"))
    assert len(calls) == 1


def test_group_listing_matches_visual_capability():
    agent = ac.build_agent(TestModel(call_tools=[]))
    result = asyncio.run(agent._function_toolset.tools["request_tool_group"].function(SimpleNamespace(deps=deps()), "diagnostics"))
    assert not {"look", "find_on_screen"} & set(result["tools"])
    assert result["vision_unavailable"] == "vision_not_configured"


def test_prepared_model_surface_is_small(tmp_path):
    model = TestModel(call_tools=[])
    agent = ac.build_agent(model, hybrid=True)
    d = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project={"auth": {"type": "none"}},
                config={"agent_execution": {"hybrid_enabled": True}})
    agent.run_sync("Report ready without calling any tools.", deps=d)
    names = {t.name for t in model.last_model_request_parameters.function_tools}
    assert "execute_goal" in names and "create_test_case" in names
    assert not names & {"click", "fill", "select_option"}
    assert len(names) <= 20


@pytest.mark.parametrize("name,args", [("click", {"ref": "x"}), ("fill", {"ref": "x", "value": "a"}),
                                      ("select_option", {"ref": "x", "value": "a"})])
def test_direct_tool_cannot_bypass_routing(name, args):
    agent = ac.build_agent(TestModel(call_tools=[]))
    tool = agent._function_toolset.tools[name].function
    result = asyncio.run(tool(SimpleNamespace(deps=deps()), **args))
    assert result["status"] == "controller_required"


def test_group_request_cannot_unlock_direct_actions():
    agent = ac.build_agent(TestModel(call_tools=[]))
    tool = agent._function_toolset.tools["request_tool_group"].function
    d = deps()
    for group in GROUPS:
        result = asyncio.run(tool(SimpleNamespace(deps=d), group))
        assert result["ok"] and not direct_action_allowed(d)


def test_missing_decision_binding_restores_regular_tools(tmp_path):
    from goal_controller import GoalAction
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True)
    d = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None,
                config={"agent_execution": {"hybrid_enabled": True}})
    tool = agent._function_toolset.tools["execute_goal"].function
    result = asyncio.run(tool(SimpleNamespace(deps=d), "Click a target", [GoalAction("click", "missing")]))
    assert result["status"] == "not_configured" and result["terminal"]
    assert direct_action_allowed(d)


@pytest.mark.parametrize("fail_at", [None, 1])
def test_assertion_only_goal_needs_no_decider_and_does_not_unlock_mutations(tmp_path, monkeypatch, fail_at):
    from planner_policy import GoalCheckpoint
    calls = []
    class Session:
        _multi_app_recording = None
        def add_checkpoint(self, name, *args):
            calls.append(name)
            return {"ok": len(calls) != fail_at}
        def inspect(self, limit):
            return {"elements": [{"ref": "fresh"}]}
    async def immediate(fn, *args):
        return fn(*args)
    monkeypatch.setattr(ac, "_bro", immediate)
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True)
    d = ac.Deps(session=Session(), ats_root=str(tmp_path), project=None,
                config={"agent_execution": {"hybrid_enabled": True}})
    d.plan = [{"status": "active", "text": "Check outcomes"}]
    checks = [GoalCheckpoint("Page", "url_contains", "/companies"),
              GoalCheckpoint("Empty", "page_contains_text", "No companies")]
    result = asyncio.run(agent._function_toolset.tools["execute_goal"].function(
        SimpleNamespace(deps=d), "Check observed outcomes", [], checks, plan_step=1))
    assert result["ok"] == (fail_at is None)
    assert len(calls) == (2 if fail_at is None else 1)
    assert result["status"] == ("checks_completed" if fail_at is None else "checkpoint_failed")
    assert result["attempted_actions"] == 0 and not result["verified"]
    assert result["observation"]["elements"][0]["ref"] == "fresh"
    assert d.recovery_actions == 0 and not direct_action_allowed(d)
    assert d.plan[0]["status"] == ("done" if fail_at is None else "failed")


def test_repeated_controller_handoff_restores_regular_tools_without_provider_call(tmp_path):
    from goal_controller import GoalAction
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True)
    d = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None,
                config={"agent_execution": {"hybrid_enabled": True}, "llm": {"roles": {"decision": "unused"}}})
    actions = [GoalAction("click", "missing")]
    key = d.goal_handoffs.key(actions)
    state = d.session._action_state()
    d.goal_handoffs.record(state, key, False)
    d.goal_handoffs.record(state, key, False)
    result = asyncio.run(agent._function_toolset.tools["execute_goal"].function(
        SimpleNamespace(deps=d), "Reworded goal", actions))
    assert result["status"] == "controller_retry_exhausted"
    assert direct_action_allowed(d)


def test_checkpoint_array_normalization_preserves_typed_schema():
    adapter = TypeAdapter(GoalCheckpoints)
    rows = [{"name": "Cart", "assert_type": "url_contains", "value": "/cart"}]
    assert adapter.validate_python(rows) == adapter.validate_python(json.dumps(rows))
    assert adapter.json_schema()["type"] == "array"
    assert adapter.json_schema()["maxItems"] == 10


@pytest.mark.parametrize("value", ["{", '"[]"', "null", "{}", "1", "x" * 65537,
    '[{"name":"Bad","assert_type":"execute_code","value":"x"}]',
    '[{"name":"Bad","assert_type":"element_has_value","value":"x"}]',
    '[{"name":"Bad","assert_type":"url_contains","value":""}]',
    '[{"name":"Bad","assert_type":"url_contains","value":"x","script":"x"}]',
    json.dumps([{"name": "Cart", "assert_type": "url_contains", "value": "/cart"}] * 11)],
    ids=["malformed", "double-encoded", "null", "object", "scalar", "oversized",
         "unknown-assertion", "missing-ref", "blank-value", "extra-field", "too-many"])
def test_invalid_checkpoints_still_rejected(value):
    with pytest.raises(ValidationError):
        TypeAdapter(GoalCheckpoints).validate_python(value)


@pytest.mark.parametrize("result,status", [
    ({"ok": True}, "active"),
    ({"ok": True, "checkpoints": [{"ok": True}]}, "done"),
    ({"ok": False, "checkpoints": [{"ok": False}]}, "failed"),
    ({"ok": True, "checkpoints": [{"ok": True}, {}]}, "active"),
])
def test_goal_plan_status_does_not_equate_actions_with_verified_outcomes(result, status):
    assert goal_plan_status(result) == status


def test_hybrid_plan_tool_descriptions_do_not_demand_extra_rounds():
    definitions = [ToolDefinition(name="set_plan", description="Call FIRST"),
                   ToolDefinition(name="update_plan", description="Update IMMEDIATELY")]
    prepared = prepare_planner_tools(SimpleNamespace(deps=deps()), definitions)
    assert "plan_step" in prepared[0].description
    assert "standalone" in prepared[1].description
    assert definitions[0].description == "Call FIRST"  # No mutation of the legacy tools.
