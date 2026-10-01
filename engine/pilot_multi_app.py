"""Explicit paid decision smoke: local fixture, at most two calls, no planner LLM.

Not an autonomous authoring benchmark. Requires the repository integration-test
fixture and pytest dependencies. All project/config artifacts are isolated.
"""
import argparse
import asyncio
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import agent_chat as ac
from agent_eval import run_test_once
from decision import ChoiceDecider
from goal_controller import GoalAction
from model_profiles import resolve_profile
from multi_app_goal import execute_recording_goal
from multi_app_recording import MultiAppRecording
import project_store
from playwright.sync_api import sync_playwright, expect


async def run_pilot(root, project, state, config, profile, report):
    events = report["usage_events"]
    decider = ChoiceDecider(config, profile, usage_sink=events.append)
    def start():
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel="chrome", headless=True)
        return pw, browser, MultiAppRecording(browser, project)
    pw, browser, recorder = await ac._bro(start)
    try:
        async def target(app, test_id):
            observed = await ac._bro(recorder.observe, app, "user")
            return next(t["ref"] for t in observed["targets"] if t["test_id"] == test_id)
        async def authorize(action):
            raise ValueError("This pilot authorizes no input values")
        for app, test_id, goal in [
            ("producer", "create", "Click Create to create one synthetic test record."),
            ("consumer", "approve", "Approve the synthetic record identified by the captured order ID.")]:
            ref = await target(app, test_id)
            result = await execute_recording_goal(recorder, goal, [GoalAction("click", ref)], ac._bro, decider, authorize)
            report["goals"].append(result)
            if not result.get("trace") or not all(row.get("ok") for row in result["trace"]):
                report["status"] = "decision_handoff_or_failure"
                return
            # A no-progress handoff after an asynchronous click is retained in
            # the report. The following assertion/capture verifies its outcome.
            if app == "producer":
                await ac._bro(lambda: expect(recorder.pages[("producer", "user")].get_by_test_id("record")).not_to_have_text(""))
                await ac._bro(recorder.act, "capture", await target("producer", "record"), capture="order")
        await ac._bro(recorder.act, "expect_text", await target("producer", "status"), value="Approved")
        exported = await ac._bro(recorder.export, str(root), "TC-MULTI-001", "bench_multi_app", "Synthetic cross-app approval")
        report["export"] = exported
        env = {"ATS_ROOT": str(root), "ATS_PROJECT_ID": project["id"], "PYTHONPATH": str(root / "engine"),
               "ATS_NO_MANUAL_INPUT": "1", "ATS_RESULTS_DIR": str(root / "replay_results")}
        for attempt in range(2):
            state.update(record="ORDER-" + uuid.uuid4().hex, created=False, approved_at=None)
            passed, summary = await asyncio.to_thread(run_test_once, exported["flow_dir"], exported["tc_id"],
                                                       env=env, cwd=str(root), timeout=60)
            report["independent_replays"].append({"passed": passed, "summary": summary})
            if not passed:
                report["status"] = "replay_failed"
                return
        state.update(created=False, approved_at=None, propagate=False)
        passed, summary = await asyncio.to_thread(run_test_once, exported["flow_dir"], exported["tc_id"],
                                                  env=env, cwd=str(root), timeout=60)
        report["negative_control"] = {"passed": passed, "summary": summary}
        report["status"] = "reliable_local_smoke" if not passed and "failed" in summary.lower() else "negative_control_invalid"
    finally:
        await ac._bro(recorder.close)
        await ac._bro(browser.close)
        await ac._bro(pw.stop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--decision")
    group.add_argument("--decision-openrouter", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent
    if args.decision_openrouter:
        name = "pilot-jev"
        config = {"llm": {"providers": {name: {"protocol": "openrouter_decisions", "model": "typesafe/jev-1.13",
                    "base_url": "https://openrouter.ai/api/alpha", "api_key_env": "OPENROUTER_API_KEY", "timeout": 20}}}}
    else:
        saved = json.loads((source / "config.json").read_text(encoding="utf-8"))
        name, profile = resolve_profile(saved, args.decision)
        config = {"llm": {"providers": {name: {key: value for key, value in profile.items()
                  if key in {"protocol", "model", "base_url", "api_key_env", "timeout", "max_tokens"}}}}}
    _, profile = resolve_profile(config, name)
    if not os.environ.get(profile.get("api_key_env", "")):
        parser.error("Configure the selected profile's API key environment variable")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    root = source / "results" / "_multi_app_pilot" / stamp
    (root / "engine").mkdir(parents=True)
    (root / "tests").mkdir()
    for filename in ("smart_locator.py", "config_loader.py", "project_store.py", "project_workflow.py", "multi_app.py"):
        shutil.copy2(source / "engine" / filename, root / "engine" / filename)
    shutil.copy2(source / "tests" / "conftest.py", root / "tests" / "conftest.py")
    (root / "config.json").write_text(json.dumps({"tracing": {"mode": "off"}, "network": {"enabled": False}}))
    (root / "pytest.ini").write_text("[pytest]\naddopts = --tracing=off\n")
    sys.path.insert(0, str(source / "engine" / "integration_tests"))
    from test_multi_app_replay import apps
    fixture = apps.__wrapped__()
    state, urls = next(fixture)
    report = {"profile": name, "model": profile["model"], "scope": "scripted_local_decisions_not_autonomous_authoring",
              "goals": [], "usage_events": [], "independent_replays": [], "status": "started"}
    started = time.monotonic()
    try:
        project = project_store.create_project(str(root), "Local pilot", "", apps=[
            {"id": app, "url": url, "actors": ["user"]} for app, url in zip(("producer", "consumer"), urls)])
        asyncio.run(run_pilot(root, project, state, config, name, report))
    except Exception as exc:
        report.update(status="error", error=type(exc).__name__)
    finally:
        fixture.close()
        report["wall_seconds"] = round(time.monotonic() - started, 2)
        events = report["usage_events"]
        report["reported_tokens"] = sum(e["input"] + e["output"] for e in events) if events and all(
            e.get("input") is not None and e.get("output") is not None for e in events) else None
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(root / "report.json"),
                          "reported_tokens": report["reported_tokens"], "wall_seconds": report["wall_seconds"]}))
    return 0 if report["status"] == "reliable_local_smoke" else 1


if __name__ == "__main__":
    raise SystemExit(main())
