"""Bounded real-planner authoring benchmark on isolated synthetic apps.

Paid API calls require an explicit invocation. No scripted browser actions,
selectors, refs, or ready-made workflow are provided to the planner.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import agent_bench
from agent_eval import run_test_once
from model_profiles import resolve_profile
from multi_app import Workflow
from pilot_saucedemo import collect_metrics, new_metrics
import project_store


TASK = {"id": "MULTI-AUTHOR-001", "tc_id": "TC-MULTI-001", "flow_id": "bench_multi_author",
        "app": "local-two-app", "difficulty": "cross-app-smoke", "contract_version": 1,
        "prompt": "Test the two local apps registered in this project. Create a synthetic record in the record-creator app, "
                  "approve that same record in the approval-console app, and verify its status becomes Approved back in "
                  "the creator app. Author one replayable cross-app test TC-MULTI-001 in a NEW flow bench_multi_author, "
                  "then verify it. The generated record ID changes between independent runs; do not hard-code it. "
                  "Only these two local apps are in scope. No login, real data or other tests are needed. "
                  "Stop on terminal provider errors or unsupported capabilities; report uncertainty honestly."}


FIELDS = {"title": "Synthetic transfer", "amount": "125", "note": "Benchmark only"}
TASKS = {"smoke": TASK}
for _name, _negative in (("form", False), ("validation", True)):
    TASKS[_name] = {**TASK, "id": "MULTI-AUTHOR-003" if _negative else "MULTI-AUTHOR-002",
        "flow_id": "bench_multi_author_" + _name, "difficulty": "cross-app-form-validation" if _negative else "cross-app-form",
        "required_fields": FIELDS, "requires_validation": _negative,
        "prompt": ("Test only the two registered synthetic local apps. " +
            ("First submit the creator form empty and verify the exact error Name is required before correcting it. " if _negative else "") +
            "Create a request named Synthetic transfer, amount 125, notes Benchmark only. Capture its generated ID. "
            "In the approval console, verify that same record's name, amount and notes, then approve it. "
            "An unrelated existing record must not be approved. Verify Approved back in the creator app for the same record. "
            "Author replayable TC-MULTI-001 in a NEW flow bench_multi_author_" + _name +
            ", then verify it. Generated IDs change between runs; do not hard-code them. "
            "No login, real data or other apps are in scope. Stop on terminal errors; report uncertainty honestly.")}


def audit_workflow(data, task=None):
    """Require the requested business outcome, not merely any passing test."""
    try:
        steps = Workflow.model_validate(data).steps
        for capture_index, capture in enumerate(steps):
            if capture.op != "capture" or capture.app != "producer" or capture.test_id != "record":
                continue
            created = any(s.op == "click" and s.app == "producer" and s.test_id == "create" for s in steps[:capture_index])
            for approve_index, approval in enumerate(steps[capture_index + 1:], capture_index + 1):
                if approval.op != "click" or approval.app != "consumer" or approval.test_id != "approve" or approval.record != capture.capture:
                    continue
                outcome = any(s.op == "expect_text" and s.app == "producer" and s.test_id == "status"
                              and s.record == capture.capture and s.value == "Approved" for s in steps[approve_index + 1:])
                if created and outcome:
                    required = (task or {}).get("required_fields") or {}
                    create_index = max(i for i, s in enumerate(steps[:capture_index]) if s.op == "click" and s.app == "producer" and s.test_id == "create")
                    if not all(any(s.op == "fill" and s.app == "producer" and s.test_id == field and s.value == value
                                   for s in steps[:create_index]) for field, value in required.items()):
                        continue
                    if not all(any(s.op == "expect_text" and s.app == "consumer" and s.record == capture.capture
                                   and s.test_id == "request-" + field and s.value == value
                                   for s in steps[capture_index + 1:approve_index]) for field, value in required.items()):
                        continue
                    if (task or {}).get("requires_validation"):
                        first_fill = next((i for i, s in enumerate(steps) if s.op == "fill"), 0)
                        if not any(s.op == "expect_text" and s.app == "producer" and s.test_id == "error"
                                   and s.value == "Name is required" for s in steps[:first_fill]):
                            continue
                    return True
    except (ValueError, TypeError):
        pass
    return False


def classify_authoring(result, metrics):
    positives, negatives = result.get("independent_replays", []), result.get("negative_controls", [])
    reliable = bool(result.get("outcome_audit")) and len(positives) == 2 and all(r["passed"] for r in positives) and len(negatives) == 2 and all(
        not r["passed"] and "failed" in r["summary"].lower() for r in negatives)
    authored = reliable and result.get("completed") is True and result.get("created") is True and result.get("self_verified") is True and not result.get("terminal_error")
    exercised = bool((result.get("role_usage") or {}).get("decision", {}).get("requests"))
    verdict = "unverified"
    if reliable:
        verdict = ("reliable_hybrid" if exercised and not metrics["controller_fallbacks"] else "reliable_with_fallback_or_bypass") if authored else "independent_replay_only"
    return {"verdict": verdict, "workflow_reliable": reliable, "authoring_success": bool(authored)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--planner-model", help="Override only the isolated benchmark planner model; leave saved settings unchanged")
    parser.add_argument("--decision", help="Existing decision profile; defaults to Jev through OpenRouter")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--tool-budget", type=int, default=40)
    parser.add_argument("--task", choices=sorted(TASKS), default="smoke")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 600 or not 1 <= args.tool_budget <= 80:
        parser.error("Use timeout 30..600 and tool budget 1..80")
    source = Path(__file__).resolve().parent.parent
    saved = json.loads((source / "config.json").read_text(encoding="utf-8"))
    decision = args.decision or "bench-jev"
    if not args.decision:
        saved.setdefault("llm", {}).setdefault("providers", {})[decision] = {
            "protocol": "openrouter_decisions", "model": "typesafe/jev-1.13",
            "base_url": "https://openrouter.ai/api/alpha", "api_key_env": "OPENROUTER_API_KEY"}
    profiles = {}
    for name in (args.provider, decision):
        _, profile = resolve_profile(saved, name)
        if name == args.provider and args.planner_model:
            if name == decision:
                parser.error("Planner model override requires separate planner and decision profiles")
            profile["model"] = args.planner_model
        if profile["protocol"] not in {"mock", "ollama"} and not os.environ.get(profile.get("api_key_env", "")):
            parser.error("Selected profile requires its API key environment variable")
        profiles[name] = {key: value for key, value in profile.items() if key in {
            "protocol", "model", "base_url", "api_key_env", "token_parameter", "supports_temperature", "capabilities"}}
        profiles[name].update(timeout=30, max_tokens=4096)
    root = source / "results" / "_multi_app_author" / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    (root / "tests").mkdir(parents=True)
    (root / "engine").mkdir()
    for filename in ("smart_locator.py", "config_loader.py", "project_store.py", "project_workflow.py", "multi_app.py"):
        shutil.copy2(source / "engine" / filename, root / "engine" / filename)
    shutil.copy2(source / "tests" / "conftest.py", root / "tests" / "conftest.py")
    (root / "pytest.ini").write_text("[pytest]\naddopts = --tracing=off --browser-channel=chrome\n")
    config = {"llm": {"default_provider": args.provider, "providers": profiles, "roles": {"decision": decision}},
              "agent_execution": {"hybrid_enabled": True, "multi_app_enabled": True}, "browser_backend": "native",
              "pass_criteria": {"mode": "all", "treat_skipped_as": "fail"}, "tracing": {"mode": "off"}, "network": {"enabled": False}}
    (root / "config.json").write_text(json.dumps(config, indent=2))
    source_hashes = {name: hashlib.sha256((source / "engine" / name).read_bytes()).hexdigest() for name in
        ("agent_chat.py", "multi_app_goal.py", "multi_app_tools.py", "multi_app_recording.py", "planner_policy.py", "benchmark_apps.py", "bench_multi_app.py")}
    (root / "implementation_manifest.json").write_text(json.dumps(source_hashes, indent=2))
    task = dict(TASKS[args.task])
    task["contract_sha256"] = hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()
    (root / "task_contract.json").write_text(json.dumps(task, indent=2))
    sys.path.insert(0, str(source / "engine" / "integration_tests"))
    if args.task == "smoke":
        from test_multi_app_replay import apps
    else:
        from benchmark_apps import request_apps as apps
    fixture = apps.__wrapped__()
    state, urls = next(fixture)
    metrics = new_metrics()
    def progress(event):
        collect_metrics(metrics, event)
        if event.get("event") == "goal_execution":
            result = event.get("result") or {}
            print(json.dumps({"event": "goal_execution", **{key: result.get(key) for key in
                ("status", "execution_state", "attempted_actions", "completed_actions")}}), flush=True)
        if event.get("event") in {"ready", "tool_call", "turn_complete", "model_usage", "controller_fallback"}:
            print(json.dumps({key: event[key] for key in ("event", "tool", "_ts", "role", "status", "reason") if key in event}), flush=True)
    print(f"Artifacts: {root}", flush=True)
    result = {"verdict": "unverified"}
    started = time.monotonic()
    agent_bench.keep_awake(True)
    try:
        project = project_store.create_project(str(root), "Local authoring", "", apps=[
            {"id": app, "label": label, "url": url, "actors": ["user"]} for app, label, url in
            zip(("producer", "consumer"), ("Record creator", "Approval console"), urls)])
        result.update(agent_bench.run_task(task, str(root), project["id"], "dev", provider=args.provider,
            tool_budget=args.tool_budget, ready_timeout=60, turn_timeout=args.timeout,
            reply="Only the stated synthetic cross-app task is authorized. No additional information or permissions are available.",
            agent_script=str(source / "engine" / "agent_chat.py"), events_path=str(root / "events.jsonl"),
            stderr_path=str(root / "stderr.log"), on_event=progress))
        flow = Path(project_store.tests_root(str(root), project["id"])) / "flows" / task["flow_id"]
        workflow = flow / "workflow.json"
        result["outcome_audit"] = workflow.exists() and audit_workflow(json.loads(workflow.read_text(encoding="utf-8")), task)
        result["independent_replays"], result["negative_controls"] = [], []
        if result["outcome_audit"] and not result.get("terminal_error"):
            for name, wrong, propagate in [("fresh-1", False, True), ("fresh-2", False, True),
                                             ("missing-propagation", False, False), ("wrong-record", True, True)]:
                state.update(record="ORDER-" + uuid.uuid4().hex, created=False, approved_at=None, wrong=wrong, propagate=propagate, fields={})
                env = {"ATS_ROOT": str(root), "ATS_PROJECT_ID": project["id"], "ATS_NO_MANUAL_INPUT": "1",
                       "PYTHONPATH": str(root / "engine"), "ATS_RESULTS_DIR": str(root / "verification" / name)}
                passed, summary = run_test_once(str(flow), task["tc_id"], env=env, cwd=str(root), timeout=60)
                result["independent_replays" if name.startswith("fresh") else "negative_controls"].append({"name": name, "passed": passed, "summary": summary})
        result.update(classify_authoring(result, metrics))
    except Exception as exc:
        result.update(verdict="error", error=type(exc).__name__)
    finally:
        fixture.close()
        agent_bench.keep_awake(False)
        result.update(metrics=metrics, end_to_end_s=round(time.monotonic() - started, 2),
                      limits={"tool_budget": args.tool_budget, "authoring_timeout_s": args.timeout},
                      profiles={name: {"model": p["model"], "protocol": p["protocol"]} for name, p in profiles.items()},
                      task_contract_sha256=task["contract_sha256"])
        result["source_sha256"] = source_hashes
        (root / "scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({key: result.get(key) for key in ("verdict", "created", "self_verified", "outcome_audit", "independent_replays", "negative_controls")}), flush=True)
    return 0 if result["verdict"] == "reliable_hybrid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
