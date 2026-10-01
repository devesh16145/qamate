import asyncio
from types import SimpleNamespace

from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
import agent_chat as ac
from planner_policy import MULTI_APP_TOOLS, prepare_planner_tools


def test_feature_is_hidden_and_disabled_by_default(tmp_path):
    agent = ac.build_agent(TestModel(call_tools=[]))
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None)
    ctx = SimpleNamespace(deps=deps)
    definitions = [ToolDefinition(name=name) for name in MULTI_APP_TOOLS | {"click"}]
    assert [d.name for d in prepare_planner_tools(ctx, definitions)] == ["click"]
    assert asyncio.run(agent._function_toolset.tools["multi_app_catalog"].function(ctx))["status"] == "disabled"


def test_active_recording_surface_excludes_legacy_tools_and_catalog_redacts(tmp_path):
    model = TestModel(call_tools=[])
    agent = ac.build_agent(model)
    session = ac.BrowserSession()
    session._multi_app_recording = object()
    deps = ac.Deps(session=session, ats_root=str(tmp_path), project={"apps": [{"id": "store", "actors": ["seller"],
        "url": "https://store.test", "credentials": {"password": "secret"}}]},
        config={"agent_execution": {"multi_app_enabled": True}})
    agent.run_sync("Report ready without tools", deps=deps)
    names = {d.name for d in model.last_model_request_parameters.function_tools}
    assert MULTI_APP_TOOLS - {"multi_app_execute_goal"} <= names
    assert "multi_app_execute_goal" not in names
    assert not names & {"navigate", "click", "fill", "execute_goal", "clear_recording", "create_test_case"}
    result = asyncio.run(agent._function_toolset.tools["multi_app_catalog"].function(SimpleNamespace(deps=deps)))
    assert "secret" not in str(result) and "credentials" not in str(result)
    assert "secret" not in str(model.last_model_request_parameters.instruction_parts)
    assert asyncio.run(ac._bro(session.navigate, "https://store.test"))["status"] == "multi_app_active"


def test_hybrid_multi_app_mutations_require_controller_and_missing_profile_falls_back(tmp_path):
    model = TestModel(call_tools=[])
    agent = ac.build_agent(model, hybrid=True)
    session = ac.BrowserSession()
    recorder = SimpleNamespace(decision_disabled=False, recovery_actions=0)
    session._multi_app_recording = recorder
    deps = ac.Deps(session=session, ats_root=str(tmp_path), project=None,
                  config={"agent_execution": {"multi_app_enabled": True, "hybrid_enabled": True}})
    ctx = SimpleNamespace(deps=deps)
    agent.run_sync("Report ready", deps=deps)
    names = {d.name for d in model.last_model_request_parameters.function_tools}
    assert "multi_app_execute_goal" in names and "execute_goal" not in names
    act = agent._function_toolset.tools["multi_app_act"].function
    assert asyncio.run(act(ctx, "click", "ref"))["status"] == "controller_required"
    execute = agent._function_toolset.tools["multi_app_execute_goal"].function
    result = asyncio.run(execute(ctx, "Click", []))
    assert result["status"] == "not_configured" and result["fallback"]
    assert recorder.decision_disabled
    result = asyncio.run(execute(ctx, "Click again", []))
    assert result["status"] == "decision_disabled"


def test_active_multi_app_descriptions_do_not_request_legacy_repairs_or_bookkeeping():
    deps = SimpleNamespace(session=SimpleNamespace(_multi_app_recording=object()),
                           config={"agent_execution": {"multi_app_enabled": True, "hybrid_enabled": True}})
    names = ["set_plan", "update_plan", "read_test_file", "multi_app_act"]
    originals = [ToolDefinition(name=name, description="original") for name in names]
    definitions = {d.name: d.description for d in prepare_planner_tools(SimpleNamespace(deps=deps), originals)}
    assert "standalone" in definitions["update_plan"]
    assert "NEW flow" in definitions["read_test_file"]
    assert "MUST use multi_app_execute_goal" in definitions["multi_app_act"]
    assert all(d.description == "original" for d in originals)


def test_repeated_preflight_errors_stop_mutations_without_provider_fallback(tmp_path, monkeypatch):
    import multi_app_tools
    monkeypatch.setattr(multi_app_tools, "ChoiceDecider", lambda *a, **kw: None)
    def invalid(*args): raise ValueError("stale")
    recorder = SimpleNamespace(decision_disabled=False, recovery_actions=0, tainted=False, goal_identity=invalid)
    session = ac.BrowserSession()
    session._multi_app_recording = recorder
    d = ac.Deps(session=session, ats_root=str(tmp_path), project=None,
                config={"agent_execution": {"multi_app_enabled": True, "hybrid_enabled": True}, "llm": {"roles": {"decision": "fake"}}})
    model = TestModel(call_tools=[])
    agent = ac.build_agent(model, hybrid=True)
    tool = agent._function_toolset.tools["multi_app_execute_goal"].function
    from goal_controller import GoalAction
    ctx = SimpleNamespace(deps=d)
    for i in range(3):
        result = asyncio.run(tool(ctx, "Goal", [GoalAction("click", str(i))]))
    assert result["status"] == "preflight_retry_exhausted" and result["terminal"]
    assert not recorder.decision_disabled  # No silent direct-execution escape.
    act = agent._function_toolset.tools["multi_app_act"].function
    assert asyncio.run(act(ctx, "click", "ref"))["status"] == "preflight_retry_exhausted"
    agent.run_sync("Report blocked without tools", deps=d)
    assert "multi_app_execute_goal" not in {t.name for t in model.last_model_request_parameters.function_tools}
    # Stale remaining candidates after a successful partial mutation are NOT
    # preflight failures; they must never acquire a "nothing was dispatched" note.
    import multi_app_goal
    async def partial(*args, **kwargs):
        return {"ok": False, "status": "stale_ref", "trace": [{"kind": "click", "ref": "x", "ok": True}]}
    monkeypatch.setattr(multi_app_goal, "execute_recording_goal", partial)
    recorder.preflight_blocked = False
    recorder.preflight_failures = 2
    result = asyncio.run(tool(ctx, "Partial", [GoalAction("click", "x")]))
    assert recorder.preflight_failures == 0 and not recorder.preflight_blocked
    assert result["execution_state"] == "partial" and result["completed_actions"] == 1


def test_read_test_file_finds_multi_app_wrapper_and_typed_workflow(tmp_path):
    import json
    folder = tmp_path / "tests" / "flows" / "multi"
    folder.mkdir(parents=True)
    (folder / "test_tc_multi_001.py").write_text("def test_TC_MULTI_001(): pass")
    workflow = {"bindings": [{"app": "store", "actor": "user", "url": "https://store.test"}],
                "steps": [{"app": "store", "actor": "user", "op": "open"}]}
    (folder / "workflow.json").write_text(json.dumps(workflow))
    agent = ac.build_agent(TestModel(call_tools=[]))
    d = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None)
    ctx = SimpleNamespace(deps=d)
    read = agent._function_toolset.tools["read_test_file"].function
    result = asyncio.run(read(ctx, "multi", "TC-MULTI-001"))
    assert result["ok"] and "test_TC_MULTI_001" in result["code"]
    assert result["workflow"]["steps"][0]["op"] == "open"
    assert not asyncio.run(read(ctx, "../multi", "TC-MULTI-001"))["ok"]
