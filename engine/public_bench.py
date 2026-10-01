"""Explicit, bounded paid paired benchmark. Every attempt is retained, including failures."""
import argparse
import datetime
import json
from pathlib import Path
import statistics
import uuid

import pilot_saucedemo
from public_bench_tasks import TASKS, get_task


def schedule(tasks, repeats):
    for repeat in range(repeats):
        for index, task in enumerate(tasks):
            arms = ("regular", "hybrid") if (repeat + index) % 2 == 0 else ("hybrid", "regular")
            for arm in arms:
                yield {"task": task, "repeat": repeat + 1, "arm": arm}


def summarize(attempts):
    summary = {}
    for arm in ("regular", "hybrid"):
        rows = [a for a in attempts if a["arm"] == arm]
        successful = [a for a in rows if a.get("verdict") == "reliable" and
                      ("outcome_audit" not in a or a["outcome_audit"].get("passed") is True) and
                      (arm != "hybrid" or (a.get("hybrid_exercised") and not a.get("hybrid_fallback")))]
        tokens = []
        for row in rows:
            decision = (row.get("role_usage") or {}).get("decision", {})
            if row.get("tokens") and not row.get("usage_incomplete") and not decision.get("incomplete"):
                tokens.append(row["tokens"]["total"] + decision.get("input", 0) + decision.get("output", 0))
        times = [a["end_to_end_s"] for a in successful if "end_to_end_s" in a]
        summary[arm] = {"attempts": len(rows), "reliable": len(successful),
                        "known_total_tokens_all_attempts": sum(tokens), "usage_known_attempts": len(tokens),
                        "median_success_end_to_end_s": statistics.median(times) if times else None}
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--decision")
    decision.add_argument("--decision-openrouter", action="store_true")
    # Local-form tasks require the enforced decision-loop policy, so must not
    # silently enter this older regular/legacy-hybrid paired suite.
    paired_tasks = sorted(name for name, task in TASKS.items() if not task.get("session_local_forms") or task.get('app') == 'public-demo')
    parser.add_argument("--tasks", nargs="+", choices=paired_tasks, default=paired_tasks)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--tool-budget", type=int, default=48)
    args = parser.parse_args(argv)
    if len(args.tasks) != len(set(args.tasks)):
        parser.error("Duplicate tasks are not allowed; use --repeats")
    if not 30 <= args.timeout <= 600 or not 1 <= args.tool_budget <= 100:
        parser.error("timeout must be 30..600 and tool-budget 1..100")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    root = Path(__file__).resolve().parent.parent / "results" / "_public_bench" / stamp
    root.mkdir(parents=True)
    planned = list(schedule(args.tasks, args.repeats))
    manifest = {"provider": args.provider, "schedule": planned,
                "contracts": {name: get_task(name) for name in args.tasks},
                "limits": {"timeout": args.timeout, "tool_budget": args.tool_budget, "replays": 2}}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    attempts = []
    print(f"Paired benchmark: {len(planned)} paid authoring attempts; artifacts: {root}", flush=True)
    for entry in planned:
        command = ["--provider", args.provider, "--task", entry["task"], "--timeout", str(args.timeout),
                   "--tool-budget", str(args.tool_budget)]
        if entry["arm"] == "hybrid":
            command += ["--decision-openrouter"] if args.decision_openrouter else ["--decision", args.decision]
        captured = []
        try:
            pilot_saucedemo.main(command, result_sink=captured.append)
        except (Exception, SystemExit) as exc:
            # Do not leak provider exception bodies or credentials into a suite report.
            captured.append({"verdict": "infrastructure_error", "error_type": type(exc).__name__})
        result = {**entry, **(captured[-1] if captured else {"verdict": "missing_result"})}
        attempts.append(result)
        report = {"attempts": attempts, "summary": summarize(attempts), "planned_attempts": len(planned)}
        (root / "scorecard.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if result.get("terminal_error") or result["verdict"] in {"infrastructure_error", "missing_result"}:
            print("Stopped suite on terminal/infrastructure error; unrun attempts are not successes.", flush=True)
            break
    return 0 if len(attempts) == len(planned) and all(a.get("verdict") == "reliable" and
        (a["arm"] != "hybrid" or (a.get("hybrid_exercised") and not a.get("hybrid_fallback"))) for a in attempts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
