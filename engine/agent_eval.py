#!/usr/bin/env python
"""
QAmate - Agent Reliability Eval Harness
==========================================

Turns "the agent authors reliable tests" from a CLAIM into a NUMBER.

The conversational agent (agent_chat.py) self-verifies every test it delivers
(run twice, only "passed" if both are green - the flakiness gate). That protects
a single delivery. This harness measures the agent's output ACROSS a suite: take
the tests it has delivered, run each one K times through the REAL pytest runner,
and classify it:

    reliable  - passed every run            (what we want)
    flaky     - passed then failed          (a timing race; not production-safe)
    failing   - failed the first run        (red)

It then prints a scorecard with the reliability % and first-pass %, and writes it
to results/_agent_eval/scorecard_<ts>.json. Run it after an authoring session, or
on a schedule, to track whether the agent's quality is holding.

This is the missing third leg of "production grade":
  * engine/tests/        - offline unit net (logic can't silently rot)
  * agent_chat verify    - per-delivery gate (no unverified test is handed over)
  * agent_eval.py        - suite-wide reliability MEASUREMENT (this file)

The pure scoring core (classify / summarize) is unit-tested in
engine/tests/test_agent_eval.py; the subprocess driver is exercised there too
(against a synthetic pass/fail test - no browser needed).

Usage
-----
    # measure specific tests
    venv\\Scripts\\python engine\\agent_eval.py --flow catalog --tc TC-CATALOG-001 --tc TC-CATALOG-002 --runs 3

    # measure a task list (defaults to engine/agent_eval_tasks.json)
    venv\\Scripts\\python engine\\agent_eval.py --tasks engine\\agent_eval_tasks.json

    # CI-style: non-zero exit if any test is not reliable
    venv\\Scripts\\python engine\\agent_eval.py --tasks ... --strict
"""

import os
import re
import sys
import json
import argparse
import datetime
import subprocess

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ATS_ROOT_DEFAULT = os.path.dirname(ENGINE_DIR)
DEFAULT_TASKS = os.path.join(ENGINE_DIR, "agent_eval_tasks.json")

_BANNER = re.compile(r"=+\s*(.*?(?:passed|failed|error|skipped|no tests ran).*?)\s*=+", re.I)


# -- Pure scoring core (unit-tested) ------------------------------------------

def classify(passes):
    """Classify a test from its per-run outcomes (list of bools).
    reliable = all green; failing = never green; flaky = some-but-not-all; unknown = no runs."""
    if not passes:
        return "unknown"
    if all(passes):
        return "reliable"
    if not any(passes):
        return "failing"
    return "flaky"


def summarize(results):
    """Aggregate a scorecard from per-test results (each: {runs:[bool], verdict})."""
    total = len(results)
    by = {"reliable": 0, "flaky": 0, "failing": 0, "unknown": 0}
    first_pass = 0
    for r in results:
        v = r.get("verdict") or classify(r.get("runs") or [])
        by[v] = by.get(v, 0) + 1
        runs = r.get("runs") or []
        if runs and runs[0]:
            first_pass += 1

    def pct(n):
        return round(100.0 * n / total, 1) if total else 0.0

    return {
        "total": total,
        "reliable": by["reliable"], "flaky": by["flaky"],
        "failing": by["failing"], "unknown": by["unknown"],
        "reliable_pct": pct(by["reliable"]),
        "flaky_pct": pct(by["flaky"]),
        "failing_pct": pct(by["failing"]),
        "first_pass_pct": pct(first_pass),
        # Headline number: share of delivered tests that pass every run.
        "reliability_score": pct(by["reliable"]),
    }


def _summary(text):
    """Final pytest summary line. Prefers the '==== N passed ... ====' banner, but
    falls back to a bare 'N passed in ...' line (printed without '=' borders in -q mode)."""
    text = text or ""
    hits = _BANNER.findall(text)
    if hits:
        return hits[-1].strip()[:200]
    for line in reversed(text.strip().splitlines()):
        low = line.lower()
        if any(k in low for k in ("passed", "failed", "error", "skipped", "no tests ran")):
            return line.strip()[:200]
    return ""


# -- Subprocess driver (mirrors agent_chat.run_test_case; integration-tested) --

def run_test_once(flow_dir, tc_id, env=None, cwd=None, timeout=300):
    """Run ONE test once through pytest. Returns (passed: bool, summary: str).
    passed is True only on a clean exit 0 with a test actually collected (rc 5 / 'no
    tests ran' => not passed)."""
    underscored = tc_id.replace("-", "_")
    cmd = [sys.executable, "-m", "pytest", flow_dir, "-k", underscored,
           "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line"]
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except Exception as ex:
        return False, f"could not start pytest: {str(ex)[:160]}"
    out = (p.stdout or "") + (p.stderr or "")
    no_tests = (p.returncode == 5) or ("no tests ran" in out.lower())
    return (p.returncode == 0 and not no_tests), _summary(out)


def evaluate(ats_root, tasks, runs=2, env_name="dev", timeout=300, progress=None):
    """Run each task's test up to `runs` times (stop early once a run fails - a test
    that fails any run is already not reliable, exactly like the agent's own gate).
    Returns a list of per-test results."""
    base_env = {"ATS_ROOT": ats_root, "PYTHONPATH": ats_root,
                "PYTHONUNBUFFERED": "1", "ATS_ENV": env_name}
    out = []
    for t in tasks:
        flow = t.get("flow_id") or t.get("flow")
        tc = t.get("tc_id") or t.get("tc")
        if not flow or not tc:
            out.append({"tc_id": tc, "flow": flow, "runs": [], "verdict": "unknown",
                        "summary": "task missing flow_id/tc_id"})
            continue
        flow_dir = os.path.join(ats_root, "tests", "flows", flow)
        passes, last = [], ""
        for i in range(max(1, int(runs))):
            ok, summ = run_test_once(flow_dir, tc, env=base_env, cwd=ats_root, timeout=timeout)
            passes.append(ok)
            last = summ or last
            if progress:
                progress(tc, i + 1, ok)
            if not ok:
                break  # not reliable; don't burn the remaining runs
        out.append({"tc_id": tc, "flow": flow, "runs": passes,
                    "verdict": classify(passes), "summary": last})
    return out


# -- Task loading + CLI -------------------------------------------------------

def load_tasks(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise SystemExit(f"could not read tasks file '{path}': {e}")
    if isinstance(data, dict):
        data = data.get("tasks", [])
    return [t for t in data if isinstance(t, dict)]


_VERDICT_MARK = {"reliable": "OK  ", "flaky": "FLAKY", "failing": "FAIL", "unknown": "?   "}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure the reliability of agent-authored tests.")
    ap.add_argument("--ats-root", default=os.environ.get("ATS_ROOT") or ATS_ROOT_DEFAULT)
    ap.add_argument("--tasks", help="JSON task list (list or {tasks:[...]}); "
                                     "default engine/agent_eval_tasks.json")
    ap.add_argument("--flow", help="flow id (use with --tc to skip a tasks file)")
    ap.add_argument("--tc", action="append", default=[], help="tc id (repeatable)")
    ap.add_argument("--runs", type=int, default=int(os.environ.get("ATS_VERIFY_RUNS") or 2))
    ap.add_argument("--env", default=os.environ.get("ATS_ENV") or "dev")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", help="scorecard output path")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless every test is reliable (for CI)")
    args = ap.parse_args(argv)

    if args.flow and args.tc:
        tasks = [{"flow_id": args.flow, "tc_id": tc} for tc in args.tc]
    else:
        path = args.tasks or DEFAULT_TASKS
        if not os.path.isfile(path):
            ap.error("no tests to evaluate: pass --flow F --tc ID [--tc ID...] "
                     f"or a --tasks file (looked for {path}).")
        tasks = load_tasks(path)
    if not tasks:
        ap.error("the task list is empty.")

    print(f"Agent reliability eval - {len(tasks)} test(s), {args.runs} run(s) each, env={args.env}")
    print("Note: each run drives the live app; the target environment must be reachable "
          "and the project must have a captured login.\n")

    def progress(tc, i, ok):
        print(f"  [{tc}] run {i}: {'pass' if ok else 'FAIL'}")

    results = evaluate(args.ats_root, tasks, runs=args.runs, env_name=args.env,
                       timeout=args.timeout, progress=progress)
    card = summarize(results)

    print("\n-- Scorecard ---------------------------------------------")
    for r in results:
        mark = _VERDICT_MARK.get(r["verdict"], "?")
        runs = "".join("." if p else "x" for p in r["runs"]) or "-"
        print(f"  {mark}  {r['flow']}/{r['tc_id']:<22} runs[{runs}]  {r.get('summary','')}")
    print("----------------------------------------------------------")
    print(f"  reliability: {card['reliability_score']}%  "
          f"(reliable {card['reliable']}, flaky {card['flaky']}, "
          f"failing {card['failing']}, unknown {card['unknown']} / {card['total']})")
    print(f"  first-pass : {card['first_pass_pct']}%")

    out_path = args.out or os.path.join(
        args.ats_root, "results", "_agent_eval",
        "scorecard_" + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                       "env": args.env, "runs": args.runs,
                       "scorecard": card, "results": results}, f, indent=2)
        print(f"  scorecard -> {out_path}")
    except Exception as e:
        print(f"  (could not write scorecard: {e})")

    if args.strict and (card["reliable"] != card["total"]):
        return 1
    return 0


def _selftest():
    """Offline sanity: pure core + a real subprocess run of a synthetic pass test."""
    assert classify([True, True]) == "reliable"
    assert classify([True, False]) == "flaky"
    assert classify([False]) == "failing"
    assert classify([]) == "unknown"
    s = summarize([{"runs": [True, True], "verdict": "reliable"},
                   {"runs": [True, False], "verdict": "flaky"},
                   {"runs": [False], "verdict": "failing"}])
    assert s["total"] == 3 and s["reliable"] == 1 and s["reliability_score"] == round(100 / 3, 1)
    print("agent_eval selftest OK:", s)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
