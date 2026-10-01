"""Bounded decision integration on a dedicated Playwright thread; no API calls."""
import asyncio
from pathlib import Path

from test_multi_app_replay import apps  # local two-origin fixture
from playwright.sync_api import sync_playwright, expect
import agent_chat as ac
import project_store
from decision import Decision
from goal_controller import GoalAction
from multi_app_goal import execute_recording_goal
from multi_app_recording import MultiAppRecording
from multi_app import MultiAppReplay, Workflow


def test_decisions_across_apps_record_and_replay_with_new_ids(apps, tmp_path):
    state, urls = apps
    project = project_store.create_project(str(tmp_path), "Goals", "", apps=[
        {"id": name, "url": url, "actors": ["user"]} for name, url in zip(("producer", "consumer"), urls)])
    class Decider:
        calls = 0
        def choose(self, state, criteria):
            self.calls += 1
            assert state["page"]["app"] in {"producer", "consumer"}
            if state["page"]["app"] == "consumer":
                approve = next(e for e in state["page"]["elements"] if e["test_id"] == "approve")
                assert approve["correlation"] == {"capture": "order", "source_app": "producer",
                                                  "source_actor": "user", "exact_row_match": True}
            return Decision("0", .99)
    decider = Decider()
    async def run():
        def start():
            pw = sync_playwright().start()
            browser = pw.chromium.launch(channel="chrome", headless=True)
            return pw, browser, MultiAppRecording(browser, project)
        pw, browser, recorder = await ac._bro(start)
        try:
            async def target(app, test_id):
                obs = await ac._bro(recorder.observe, app, "user")
                return next(t["ref"] for t in obs["targets"] if t["test_id"] == test_id)
            async def authorize(action): return action.value
            create_ref = await target("producer", "create")
            result = await execute_recording_goal(recorder, "Create one record", [GoalAction("click", create_ref)], ac._bro, decider, authorize)
            assert len(result["trace"]) == 1
            await ac._bro(lambda: expect(recorder.pages[("producer", "user")].get_by_test_id("record")).not_to_have_text(""))
            await ac._bro(recorder.act, "capture", await target("producer", "record"), capture="order")
            approve_ref = await target("consumer", "approve")
            result = await execute_recording_goal(recorder, "Approve the captured record", [GoalAction("click", approve_ref)], ac._bro, decider, authorize)
            assert len(result["trace"]) == 1
            await ac._bro(recorder.act, "expect_text", await target("producer", "status"), value="Approved")
            exported = await ac._bro(recorder.export, str(tmp_path), "TC-GOAL-001", "bounded", "Decision-routed cross-app approval")
            workflow = Workflow.model_validate_json((Path(exported["flow_dir"]) / "workflow.json").read_text())
            assert workflow.steps[-2].record == "order"
            for record_id in ("NEW-1", "NEW-2"):
                state.update(record=record_id, created=False, approved_at=None)
                def replay():
                    with MultiAppReplay(browser) as runtime:
                        return runtime.run(workflow)
                assert (await ac._bro(replay))["status"] == "passed"
            assert decider.calls == 2
        finally:
            await ac._bro(recorder.close)
            await ac._bro(browser.close)
            await ac._bro(pw.stop)
    asyncio.run(run())


def test_batched_fills_keep_identity_and_redact_inputs(apps, tmp_path):
    _, urls = apps
    project = project_store.create_project(str(tmp_path), "Fields", "", apps=[{"id": "form", "url": urls[0], "actors": ["user"]}])
    async def run():
        def start():
            pw = sync_playwright().start()
            browser = pw.chromium.launch(channel="chrome", headless=True)
            recorder = MultiAppRecording(browser, project)
            recorder.observe("form", "user")
            recorder.pages[("form", "user")].evaluate("document.body.innerHTML='<input data-testid=first><input data-testid=second>'")
            return pw, browser, recorder
        pw, browser, recorder = await ac._bro(start)
        try:
            observation = await ac._bro(recorder.observe, "form", "user")
            actions = [GoalAction("fill", t["ref"], "private-" + str(i)) for i,t in enumerate(observation["targets"])]
            class Decider:
                def choose(self, state, criteria):
                    assert "private-" not in str(state) + str(criteria)
                    return Decision(next(key for key in criteria if key != "stop"), .99)
            async def authorize(action): return action.value
            result = await execute_recording_goal(recorder, "Fill both authorized inputs", actions, ac._bro, Decider(), authorize)
            assert result["status"] == "actions_completed", result
            assert len(result["trace"]) == 2
            assert not recorder.refs
            assert [s.value for s in recorder.steps if s.op == "fill"] == ["private-0", "private-1"]
        finally:
            await ac._bro(recorder.close)
            await ac._bro(browser.close)
            await ac._bro(pw.stop)
    asyncio.run(run())
