r"""Bounded public-demo authoring smoke. Every artifact stays in an isolated run root.

Run explicitly: venv\Scripts\python engine\pilot_saucedemo.py --provider PROFILE
This spends model API tokens. Does not modify existing projects, tests, or settings.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid
from copy import deepcopy

import agent_bench
from model_profiles import resolve_profile
from public_bench_tasks import TASKS, get_task, required_outcome_groups
from public_outcome_audit import audit_flow, coverage_verdict


def parse_model_settings(value):
    """Accept JSON or @file, avoiding shell-dependent JSON escaping on Windows."""
    data = json.loads(Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value)
    if not isinstance(data, dict):
        raise ValueError("Planner model settings must be a JSON object")
    return data


def benchmark_profile(profile, model_settings=None):
    """Preserve provider behaviour; never silently drop configured SDK settings."""
    copied = {key: deepcopy(value) for key, value in profile.items() if key in {
        "protocol", "model", "base_url", "api_key_env", "token_parameter",
        "supports_temperature", "capabilities", "model_settings"}}
    copied.update(timeout=45, max_tokens=16384)
    if model_settings is not None:
        if not isinstance(model_settings, dict):
            raise ValueError("Planner model settings must be a JSON object")
        copied["model_settings"] = {**(copied.get("model_settings") or {}), **deepcopy(model_settings)}
    return copied


def apply_outcome_gate(result, flow_dir, task):
    """Non-smoke tasks need assessed coverage, not merely green replay.

    An unsupported audit is explicitly unverified until an outcome recognizer
    exists. This deliberately prevents CRM replay from being task success.
    """
    result["outcome_audit"] = audit_flow(flow_dir, task)
    result["replay_verdict"] = result["verdict"]
    result["verdict"] = coverage_verdict(result["verdict"], result["outcome_audit"])


def collect_metrics(metrics, event):
    """Count rejected calls as well as executed failures, without saving arguments."""
    kind = event.get("event")
    if kind == "tool_call":
        tool = event.get("tool", "unknown")
        metrics["tool_counts"][tool] = metrics["tool_counts"].get(tool, 0) + 1
    elif kind == "turn_complete":
        metrics["turn_complete_s"] = event.get("_ts")
    elif kind == "controller_fallback":
        metrics["controller_fallbacks"] += 1
    elif kind == "decision_browser_step":
        metrics.setdefault("decision_browser_steps", []).append({k: event.get(k) for k in
            ("step", "kind", "ok", "milestone", "candidates")})
    elif kind == "decision_browser_complete":
        metrics["decision_browser_status"] = (event.get("result") or {}).get("status")
    elif kind == "goal_execution":
        summary = event.get("result") or {}
        metrics["goal_results"].append({"status": summary.get("status"), "ok": summary.get("ok"),
                                       "actions": len(summary.get("trace") or [])})
    elif kind == "tool_result":
        # New engine events report status before UI summary truncation. Keep the
        # fallback for historical event streams so their metrics can be audited.
        outcome = event.get("outcome")
        if isinstance(outcome, dict):
            if outcome.get("validation_failed"):
                metrics["validation_failed_calls"] += 1
            if outcome.get("ok") is False or outcome.get("retry"):
                metrics["failed_tool_results"] += 1
            return
        try:
            summary = json.loads(event.get("summary", "{}"))
        except (ValueError, TypeError):
            metrics["unparsed_tool_results"] += 1
            return
        validation_failure = isinstance(summary, list) and any(
            isinstance(row, dict) and "loc" in row and "msg" in row and "type" in row
            for row in summary)
        if validation_failure:
            metrics["validation_failed_calls"] += 1
        if validation_failure or (isinstance(summary, dict) and summary.get("ok") is False):
            metrics["failed_tool_results"] += 1


def new_metrics():
    return {"tool_counts": {}, "goal_results": [], "failed_tool_results": 0,
            "validation_failed_calls": 0, "unparsed_tool_results": 0, "controller_fallbacks": 0}


def main(argv=None, result_sink=None, _app_url=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--planner-model-settings", type=parse_model_settings, help="Explicit SDK settings JSON or @file for this isolated planner run; saved configuration is unchanged")
    parser.add_argument("--task", choices=sorted(TASKS), default="smoke")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--tool-budget", type=int, default=24)
    parser.add_argument("--no-contract-hints", action="store_true", help="Disable benchmark-specific compiler hints; retain independent outcome auditing")
    parser.add_argument("--compact-decisions", action="store_true", help="Use lossless column-encoded page evidence for the decision provider")
    parser.add_argument("--review-contract", action="store_true", help="Jev reviews contract faithfulness against original user requirements before browser work")
    parser.add_argument("--decision", help="Enable experimental hybrid mode with this configured profile")
    parser.add_argument("--decision-openrouter", action="store_true", help="Use Jev 1.13 with OPENROUTER_API_KEY; no saved config changes")
    parser.add_argument("--decision-loop", action="store_true", help="Decision-owned read-only browser loop, not the legacy planner-selected action pilot")
    parser.add_argument("--require-hybrid", action="store_true", help="Require a live controller call for this diagnostic run")
    args = parser.parse_args(argv)
    if args.planner_model_settings is not None and not isinstance(args.planner_model_settings, dict):
        parser.error("Planner model settings must be a JSON object")
    if not 30 <= args.timeout <= 600:
        parser.error("timeout must be 30..600 seconds")
    if not 1 <= args.tool_budget <= 100:
        parser.error("tool-budget must be 1..100")
    if get_task(args.task).get('disposable_operations') and _app_url is None:
        from disposable_operations import operations_app
        with operations_app() as url:
            return main(argv, result_sink, _app_url=url)
    source = Path(__file__).resolve().parent.parent
    source_config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    if args.decision_openrouter:
        if args.decision:
            parser.error("Choose --decision or --decision-openrouter, not both")
        args.decision = "pilot-jev-openrouter"
        source_config.setdefault("llm", {}).setdefault("providers", {})[args.decision] = {
            "protocol": "openrouter_decisions", "model": "typesafe/jev-1.13",
            "base_url": "https://openrouter.ai/api/alpha", "api_key_env": "OPENROUTER_API_KEY"}
    if args.require_hybrid and not args.decision:
        parser.error("--require-hybrid needs a decision profile")
    if args.decision_loop and not args.decision:
        parser.error("Decision-loop pilot requires a decision profile")
    if TASKS[args.task].get("session_local_forms") and not args.decision_loop:
        parser.error("The isolated lifecycle pilot currently requires decision-loop enforcement")
    if args.require_hybrid and args.task == "crm-search":
        parser.error("The forced login diagnostic is specific to SauceDemo, not the CRM task")
    names = [args.provider] + ([args.decision] if args.decision else [])
    profiles = {}
    for name in names:
        _, profile = resolve_profile(source_config, name)
        key_env = profile.get("api_key_env")
        if profile["protocol"] != "ollama" and not (key_env and os.environ.get(key_env)):
            parser.error(f"Profile {name!r}: required environment key is not configured")
        profiles[name] = benchmark_profile(profile, args.planner_model_settings if name == args.provider else None)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    root = source / "results" / "_public_pilot" / stamp
    (root / "tests" / "flows").mkdir(parents=True)
    (root / "engine").mkdir()
    # Reuse Qamate's actual runner fixtures, without importing any existing project data.
    shutil.copy2(source / "tests" / "conftest.py", root / "tests" / "conftest.py")
    for name in ("smart_locator.py", "project_store.py", "config_loader.py"):
        shutil.copy2(source / "engine" / name, root / "engine" / name)
    (root / "pytest.ini").write_text("[pytest]\naddopts = --tracing=off --browser-channel=chrome\n", encoding="utf-8")
    config = {"llm": {"default_provider": args.provider, "providers": profiles,
                       "roles": {"decision": args.decision} if args.decision else {}},
              "agent_execution": {"hybrid_enabled": bool(args.decision), "decision_loop_enabled": args.decision_loop},
              "browser_backend": "native", "pass_criteria": {"mode": "all", "treat_skipped_as": "fail"},
              "tracing": {"mode": "off"}, "video": {"enabled": False}, "network": {"enabled": False}}
    task = get_task(args.task)
    if _app_url:
        task['base_url'] = _app_url
    config['agent_execution']['required_outcome_groups'] = [] if args.no_contract_hints else required_outcome_groups(args.task)
    config['agent_execution']['decision_context_format'] = 'columns' if args.compact_decisions else 'objects'
    config['agent_execution']['decision_contract_review'] = args.review_contract
    if task.get("session_local_forms"):
        config["agent_execution"].update(decision_loop_policy="session_local_forms",
                                         session_local_app_urls=[task["base_url"]])
    if task.get('app') == 'public-demo':
        config['agent_execution']['public_demo_auth'] = True
    (root / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    source_hashes = {name: hashlib.sha256((source / "engine" / name).read_bytes()).hexdigest() for name in
                     ("agent_chat.py", "agent_recorder.py", "goal_controller.py", "planner_policy.py", "model_profiles.py",
                      "decision.py", "decision_browser.py", "decision_browser_tools.py", "contract_review.py", "app_explorer.py", "normalizer.py", "interaction_context.py", "recorder_parser.py", "agent_eval.py",
                      "agent_bench.py", "public_outcome_audit.py", "public_bench_tasks.py", "pilot_saucedemo.py",
                      "disposable_operations.py", "fixtures/operations.html")}
    (root / "implementation_manifest.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
    project_id = "public-demo"
    task = get_task(args.task)
    if _app_url:
        task['base_url'] = _app_url
    project_dir = root / "projects" / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(json.dumps({"id": project_id, "name": "Public demo pilot",
        "apps": [{"label": task.get("target_label", "SauceDemo"),
                  "url": task.get("base_url", "https://www.saucedemo.com/")}], "auth": {"type": "none"}}), encoding="utf-8")
    if args.require_hybrid:
        task["prompt"] += (" This is an explicit hybrid-controller experiment: after observing the login form, "
                           "call execute_goal with the username fill, password fill, and login click as one "
                           "candidate group. Keep those recorded steps. Use regular tools only after controller "
                           "handoff or for operations it does not support; do not bypass this experiment.")
    print(f"Pilot artifacts: {root}", flush=True)
    (root / "task_contract.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
    metrics = new_metrics()
    def progress(event):
        collect_metrics(metrics, event)
        # Never print tool arguments, page contents, credentials or provider bodies.
        if event.get("event") in {"ready", "tool_call", "turn_complete", "usage_est", "model_usage", "decision_browser_step"}:
            print(json.dumps({key: event[key] for key in ("_ts", "event", "tool", "requests", "status", "step", "kind", "ok", "milestone", "candidates") if key in event}), flush=True)
    agent_bench.keep_awake(True)
    started = time.monotonic()
    try:
        result = agent_bench.run_task(task, str(root), project_id, "dev", provider=args.provider,
            tool_budget=args.tool_budget, ready_timeout=60, turn_timeout=args.timeout,
            agent_script=str(source / "engine" / "agent_chat.py"),
            events_path=str(root / "events.jsonl"), stderr_path=str(root / "stderr.log"), on_event=progress)
        if result.get("created") and not result.get("terminal_error"):
            result["independent"] = agent_bench.verify_independent(str(root), str(root / "tests"), task,
                "dev", project_id, runs=2, timeout=120)
        result["profiles"] = {name: {"model": profile["model"], "protocol": profile["protocol"]} for name, profile in profiles.items()}
        result["source_sha256"] = source_hashes
        result["task_contract"] = {"name": args.task, "version": task["contract_version"], "sha256": task["contract_sha256"]}
        result["artifact_root"] = str(root)
        result["hybrid"] = bool(args.decision)
        result["decision_loop"] = args.decision_loop
        result["contract_hints_enabled"] = not args.no_contract_hints
        result["decision_context_format"] = config['agent_execution']['decision_context_format']
        result["contract_review_enabled"] = args.review_contract
        result["planner_settings_sha256"] = hashlib.sha256(json.dumps(profiles[args.provider].get("model_settings", {}), sort_keys=True).encode()).hexdigest()
        result["hybrid_required"] = args.require_hybrid
        result["hybrid_exercised"] = bool((result.get("role_usage") or {}).get("decision", {}).get("requests"))
        result["hybrid_fallback"] = bool(metrics["controller_fallbacks"])
        result["metrics"] = metrics
        result["end_to_end_s"] = round(time.monotonic() - started, 2)
        result["limits"] = {"tool_budget": args.tool_budget, "authoring_timeout_s": args.timeout}
        result["verdict"] = agent_bench.task_verdict(result)
        apply_outcome_gate(result, root / "tests" / "flows" / task["flow_id"], task)
        (root / "scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        if result_sink is not None:
            result_sink(result)
        print(json.dumps({key: result.get(key) for key in ("verdict", "wall_s", "tool_calls", "created", "self_verified", "independent")}), flush=True)
        return 0 if result["verdict"] == "reliable" and (not args.require_hybrid or result["hybrid_exercised"]) else 1
    finally:
        agent_bench.keep_awake(False)


if __name__ == "__main__":
    raise SystemExit(main())
