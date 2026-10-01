import asyncio
from types import SimpleNamespace
import uuid

import pytest
from playwright.sync_api import sync_playwright
from pydantic_ai.models.test import TestModel

import agent_chat as ac
from benchmark_apps import request_apps
from bench_multi_app import audit_workflow, TASKS, FIELDS
from decision import Decision
from goal_controller import GoalAction
from multi_app import MultiAppReplay
from multi_app_recording import MultiAppRecording
from multi_app_tools import MultiAppCheck
import project_store


def test_form_validation_fixture_replays_and_rejects_broken_correlation_and_propagation():
    with request_apps() as (state, urls), sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        bindings = [{"app": app, "actor": "user", "url": url} for app, url in zip(("producer", "consumer"), urls)]
        steps = []
        def step(app, op, **kwargs): steps.append({"app": app, "actor": "user", "op": op, "timeout_ms": 1800, **kwargs})
        step("producer", "open")
        step("producer", "click", test_id="create")
        step("producer", "expect_text", test_id="error", value="Name is required")
        for key, value in FIELDS.items(): step("producer", "fill", test_id=key, value=value)
        step("producer", "click", test_id="create")
        step("producer", "capture", test_id="record", capture="request")
        step("producer", "expect_text", test_id="status", value="Pending", record="request")
        step("consumer", "open")
        for key, value in FIELDS.items(): step("consumer", "expect_text", test_id="request-" + key, value=value, record="request")
        step("consumer", "click", test_id="approve", record="request")
        step("producer", "expect_text", test_id="status", value="Approved", record="request")
        workflow = {"bindings": bindings, "steps": steps}
        assert audit_workflow(workflow, TASKS["validation"])
        try:
            for wrong, propagate in [(False, True), (False, True), (True, True), (False, False)]:
                state.update(record="REQ-" + uuid.uuid4().hex, created=False, approved_at=None, fields={}, wrong=wrong, propagate=propagate)
                with MultiAppReplay(browser) as runtime:
                    if wrong or not propagate:
                        with pytest.raises(AssertionError): runtime.run(workflow)
                    else:
                        assert runtime.run(workflow)["status"] == "passed"
                        previous = runtime.captures["request"]["value"]
                        # Same fixture state, fresh browser contexts: a new request must
                        # not inherit the preceding request's Approved state.
                        assert runtime.run(workflow)["status"] == "passed"
                        assert runtime.captures["request"]["value"] != previous
        finally:
            browser.close()


def test_tool_roundtrips_return_fresh_refs_and_batch_assertions(tmp_path, monkeypatch):
    import multi_app_tools
    class Decider:
        def choose(self, state, criteria): return Decision(next(key for key in criteria if key != "stop"), .99)
    monkeypatch.setattr(multi_app_tools, "ChoiceDecider", lambda *a, **kw: Decider())
    with request_apps() as (state, urls):
        project = project_store.create_project(str(tmp_path), "Fused", "", apps=[
            {"id": name, "url": url, "actors": ["user"]} for name, url in zip(("producer", "consumer"), urls)])
        async def run():
            def start():
                pw = sync_playwright().start()
                browser = pw.chromium.launch(channel="chrome", headless=True)
                return pw, browser, MultiAppRecording(browser, project)
            pw, browser, recorder = await ac._bro(start)
            session = ac.BrowserSession()
            session._multi_app_recording = recorder
            d = ac.Deps(session=session, ats_root=str(tmp_path), project=project,
                        config={"agent_execution": {"multi_app_enabled": True, "hybrid_enabled": True}, "llm": {"roles": {"decision": "fake"}}})
            d.mode = "auto"
            agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True)
            ctx = SimpleNamespace(deps=d)
            funcs = {name: tool.function for name, tool in agent._function_toolset.tools.items()}
            refs = lambda obs: {t["test_id"]: t["ref"] for t in obs["targets"]}
            try:
                obs = await funcs["multi_app_observe"](ctx, "producer", "user")
                old = refs(obs)
                result = await funcs["multi_app_execute_goal"](ctx, "Fill all fields then create", [
                    *[GoalAction("fill", old[k], v) for k, v in FIELDS.items()], GoalAction("click", old["create"])])
                assert result["completed_actions"] == 4, result
                fresh = refs(result["observation"])
                assert fresh["create"] != old["create"]
                result = await funcs["multi_app_act"](ctx, "capture", fresh["record"], capture="request")
                assert result["ok"] and "observation" in result
                obs = await funcs["multi_app_observe"](ctx, "consumer", "user")
                current = refs(obs)
                checked = await funcs["multi_app_check"](ctx, [MultiAppCheck(ref=current["request-" + k], value=v) for k, v in FIELDS.items()])
                assert checked["ok"] and len(checked["checks"]) == 3 and not checked["verified"]
                assert len([s for s in recorder.steps if s.op == "expect_text"]) == 3
                assert refs(checked["observation"])["approve"] != current["approve"]
                # A failed later check is reported, never hidden behind earlier successes.
                failed = await funcs["multi_app_check"](ctx, [MultiAppCheck(ref=refs(checked["observation"])["request-title"], value="Wrong")])
                assert not failed["ok"] and not failed["checks"][0]["ok"]
                assert not recorder.tainted
            finally:
                await ac._bro(recorder.close)
                await ac._bro(browser.close)
                await ac._bro(pw.stop)
        asyncio.run(run())
