#!/usr/bin/env python
"""
Agrim ATS - Agent AUTHORING Benchmark
=====================================

agent_eval.py answers "are the tests the agent ALREADY delivered reliable?".
This harness answers the question above it: "how good is the agent AT AUTHORING?"
- the number that must not regress when the agent surface changes (observe/refs,
normalizer detectors, playbook edits, model/provider swaps).

It drives the REAL agent process (engine/agent_chat.py) over the same
newline-delimited-JSON stdin/stdout protocol Electron uses - no parallel
reimplementation - through a FIXED list of authoring tasks
(engine/agent_bench_tasks.json). For each task it:

  1. spawns a fresh agent process (no cross-task contamination),
  2. sends init (AUTO mode, headless) + the task prompt as one chat turn,
  3. records the full event stream (results/_agent_bench/<ts>/events/*.jsonl),
  4. scores the authoring from the events:
        delivered      - create_test_case succeeded
        self_verified  - the agent's own run_test_case gate ended green
        run_attempts   - verify-and-fix iterations it needed
        assumed_values - values it invented (AUTO-mode provenance gate)
        asks           - times it stopped to ask the user
        tool_calls / tokens / wall_s
  5. INDEPENDENTLY verifies the delivered test K times through the real pytest
     runner (reusing agent_eval.run_test_once/classify) - the agent's own
     "passed" claim is never trusted for the headline number.

Per-task verdict: reliable | flaky | failing | not_delivered | timeout |
infra_error | skipped. Headline benchmark_score = % of tasks whose delivered
test is independently reliable.

Safety: tasks dictate tc_id/flow_id and every bench flow is prefixed "bench_".
Cleanup ONLY ever deletes flows/bench_* directories (asserted in
engine/tests/test_agent_bench.py), and runs BEFORE the benchmark so the agent
can never shortcut by reading a previous run's test; authored tests are KEPT
after the run for inspection.

Usage
-----
    venv\\Scripts\\python engine\\agent_bench.py                      # full benchmark
    venv\\Scripts\\python engine\\agent_bench.py --only BENCH-001     # one task
    venv\\Scripts\\python engine\\agent_bench.py --provider anthropic --verify-runs 3
    venv\\Scripts\\python engine\\agent_bench.py --strict             # CI: non-zero exit unless all reliable

The target env must be reachable and the project must have a captured login.
On env=dev the seller account defaults to index 1 (index 0 is stale) unless
ATS_SELLER_USER_INDEX is already set.
"""

import os
import re
import sys
import json
import time
import queue
import shutil
import argparse
import datetime
import threading
import subprocess

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ATS_ROOT_DEFAULT = os.path.dirname(ENGINE_DIR)
DEFAULT_TASKS = os.path.join(ENGINE_DIR, "agent_bench_tasks.json")
AGENT_SCRIPT = os.path.join(ENGINE_DIR, "agent_chat.py")

DEFAULT_REPLY = ("Use your best judgment and continue. Pick reasonable test-prefixed "
                 "values where needed, and do not ask again.")

if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
import agent_eval  # run_test_once / classify - the independent verification leg


# ── Pure core (unit-tested in engine/tests/test_agent_bench.py) ───────────────

def parse_tool_summary(summary):
    """tool_result summaries are JSON (ensure_ascii) but TRUNCATED at 1500 chars -
    run_test_case failures (2500-char tail) won't parse. Fall back to key probes so
    scoring still works on truncated payloads."""
    if isinstance(summary, dict):
        return summary
    if not isinstance(summary, str) or not summary:
        return {}
    try:
        d = json.loads(summary)
        return d if isinstance(d, dict) else {}
    except Exception:
        pass
    out = {}
    if re.search(r'"passed":\s*true', summary):
        out["passed"] = True
    elif re.search(r'"passed":\s*false', summary):
        out["passed"] = False
    if re.search(r'"status":\s*"success"', summary):
        out["status"] = "success"
    if re.search(r'"manual_input_gate":\s*true', summary):
        out["manual_input_gate"] = True
    if re.search(r'"flaky":\s*true', summary):
        out["flaky"] = True
    m = re.search(r'"assumed_values":\s*\[(.*?)\]', summary, re.DOTALL)
    if m:
        out["assumed_values"] = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return out


def score_events(events):
    """Reduce one task's event stream to the authoring metrics. Pure."""
    s = {"completed": False, "created": False, "self_verified": False,
         "run_attempts": 0, "tool_calls": 0, "asks": 0, "assumed_values": 0,
         "manual_gate": False, "flaky_seen": False,
         "tokens": {"input": 0, "output": 0, "total": 0},
         "errors": [], "final_text": ""}
    for ev in events or []:
        kind = ev.get("event")
        if kind == "tool_call":
            s["tool_calls"] += 1
            if ev.get("tool") == "run_test_case":
                s["run_attempts"] += 1
        elif kind == "tool_result":
            r = parse_tool_summary(ev.get("summary"))
            tool = ev.get("tool")
            if tool == "create_test_case":
                if r.get("status") == "success" or "path" in r:
                    s["created"] = True
                    s["assumed_values"] = len(r.get("assumed_values") or [])
            elif tool == "run_test_case":
                # last verify result wins - that's the state the test was delivered in
                s["self_verified"] = r.get("passed") is True
                if r.get("manual_input_gate"):
                    s["manual_gate"] = True
                if r.get("flaky"):
                    s["flaky_seen"] = True
        elif kind == "input_required":
            s["asks"] += 1
        elif kind == "usage":
            # cumulative per session - keep the latest snapshot
            s["tokens"] = {k: int(ev.get(k, 0) or 0) for k in ("input", "output", "total")}
        elif kind == "error":
            s["errors"].append(str(ev.get("message", ""))[:200])
        elif kind == "turn_complete":
            s["completed"] = True
            s["final_text"] = str(ev.get("text", ""))[:500]
    return s


def task_verdict(result):
    """Collapse one task result to its verdict string. Pure.
    Order matters: infra problems mask agent quality, so they're reported as such
    and EXCLUDED from the agent's score by the caller only for 'skipped'."""
    if result.get("skipped"):
        return "skipped"
    if result.get("infra_error"):
        return "infra_error"
    if not result.get("completed"):
        return "timeout"
    if not result.get("created"):
        return "not_delivered"
    ind = result.get("independent") or {}
    v = ind.get("verdict")
    if v in ("reliable", "flaky", "failing"):
        return v
    return "delivered_unverified"


def summarize_bench(results):
    """Aggregate the run scorecard. Tasks skipped for missing prerequisites
    (e.g. no admin login captured) don't count against the agent."""
    counted = [r for r in results if not r.get("skipped")]
    total = len(counted)
    by = {}
    for r in counted:
        v = task_verdict(r)
        by[v] = by.get(v, 0) + 1

    def pct(n):
        return round(100.0 * n / total, 1) if total else 0.0

    delivered = sum(1 for r in counted if r.get("created"))
    self_ok = sum(1 for r in counted if r.get("self_verified"))
    first_try = sum(1 for r in counted
                    if r.get("created") and r.get("self_verified")
                    and (r.get("run_attempts") or 0) <= 1)
    reliable = by.get("reliable", 0)
    walls = [r.get("wall_s") for r in counted if r.get("wall_s") is not None]
    calls = [r.get("tool_calls") for r in counted if r.get("tool_calls") is not None]
    return {
        "total": total,
        "skipped": len(results) - total,
        "verdicts": by,
        "delivered": delivered, "delivered_pct": pct(delivered),
        "self_verified": self_ok, "self_verified_pct": pct(self_ok),
        "first_try": first_try, "first_try_pct": pct(first_try),
        "independent_reliable": reliable,
        # Headline number: % of tasks whose delivered test passed EVERY
        # independent run. This is the regression gate for agent changes.
        "benchmark_score": pct(reliable),
        "asks_total": sum(r.get("asks") or 0 for r in counted),
        "assumed_values_total": sum(r.get("assumed_values") or 0 for r in counted),
        "avg_tool_calls": round(sum(calls) / len(calls), 1) if calls else 0,
        "avg_wall_s": round(sum(walls) / len(walls), 1) if walls else 0,
        "tokens_total": sum((r.get("tokens") or {}).get("total", 0) for r in counted),
    }


def load_bench_tasks(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise SystemExit(f"could not read bench tasks file '{path}': {e}")
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    tasks = data.get("tasks", data if isinstance(data, list) else [])
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        missing = [k for k in ("id", "tc_id", "flow_id", "prompt") if not t.get(k)]
        if missing:
            raise SystemExit(f"bench task {t.get('id') or '?'} is missing {missing}")
        if not str(t["flow_id"]).startswith("bench_"):
            raise SystemExit(f"bench task {t['id']}: flow_id must start with 'bench_' "
                             "(cleanup safety relies on the prefix)")
        out.append(t)
    return out, defaults


def clean_bench_flows(tests_root):
    """Delete ONLY flows/bench_* directories under the resolved tests root.
    Never touches real flows - the prefix check is the safety contract."""
    flows = os.path.join(tests_root, "flows")
    removed = []
    if not os.path.isdir(flows):
        return removed
    for name in sorted(os.listdir(flows)):
        p = os.path.join(flows, name)
        if name.startswith("bench_") and os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
    return removed


# ── Agent process driver (same NDJSON protocol Electron speaks) ───────────────

class AgentProc:
    """One agent_chat.py subprocess: send actions on stdin, read events off a
    queue fed by a reader thread (stdout readline has no timeout on Windows)."""

    def __init__(self, ats_root, env=None, agent_script=None, stderr_path=None):
        cmd = [sys.executable, agent_script or AGENT_SCRIPT]
        e = dict(os.environ)
        e.update(env or {})
        if stderr_path:
            os.makedirs(os.path.dirname(stderr_path) or ".", exist_ok=True)
            self._stderr_f = open(stderr_path, "w", encoding="utf-8")
        else:
            self._stderr_f = subprocess.DEVNULL
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.proc = subprocess.Popen(
            cmd, cwd=ats_root, env=e,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr_f,
            text=True, encoding="utf-8", errors="replace", **kw)
        self.q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                self.q.put(line)
        except Exception:
            pass
        self.q.put(None)  # EOF sentinel

    def send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=True) + "\n")
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def next_event(self, timeout):
        """Next parsed event dict; None on process EOF; raises TimeoutError.
        Non-JSON stdout lines are preserved as {'event':'_raw'}."""
        line = self.q.get(timeout=timeout)   # queue.Empty propagates as the timeout
        if line is None:
            return None
        line = line.strip()
        if not line:
            return {"event": "_blank"}
        try:
            ev = json.loads(line)
            return ev if isinstance(ev, dict) else {"event": "_raw", "line": line[:500]}
        except Exception:
            return {"event": "_raw", "line": line[:500]}

    def shutdown(self, grace=30):
        self.send({"action": "shutdown"})
        try:
            self.proc.wait(timeout=grace)
        except Exception:
            self.kill()
        if self._stderr_f is not subprocess.DEVNULL:
            try:
                self._stderr_f.close()
            except Exception:
                pass

    def kill(self):
        """Hard stop, including the Chrome tree the agent spawned."""
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True, timeout=30)
            else:
                self.proc.kill()
            self.proc.wait(timeout=10)
        except Exception:
            pass


def _child_env(ats_root, env_name):
    e = {"ATS_ROOT": ats_root, "PYTHONPATH": ats_root,
         "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
         "ATS_ENV": env_name}
    # dev seller index 0 is a stale account - default to 1 unless the caller chose
    if env_name == "dev" and "ATS_SELLER_USER_INDEX" not in os.environ:
        e["ATS_SELLER_USER_INDEX"] = "1"
    return e


def run_task(task, ats_root, project_id, env_name, provider=None, tool_budget=80,
             ready_timeout=240, turn_timeout=1500, reply=DEFAULT_REPLY,
             agent_script=None, events_path=None, stderr_path=None, video_dir=None):
    """Run ONE bench task in a FRESH agent process. Returns the task result dict
    (verdict-ready except for the independent verification leg). When video_dir
    is set, the agent's OWN navigation is recorded there (.webm, finalized when
    the agent closes its browser) — the raw material for analyzing where long
    tasks actually spend their time."""
    t0 = time.monotonic()
    events = []
    result = {"task_id": task["id"], "tc_id": task["tc_id"], "flow_id": task["flow_id"],
              "difficulty": task.get("difficulty", ""), "app": task.get("app", ""),
              "session_id": None, "infra_error": None}

    def record(ev):
        ev["_ts"] = round(time.monotonic() - t0, 2)
        events.append(ev)

    child_env = _child_env(ats_root, env_name)
    if video_dir:
        child_env["ATS_AGENT_VIDEO_DIR"] = video_dir
    try:
        ap = AgentProc(ats_root, env=child_env,
                       agent_script=agent_script, stderr_path=stderr_path)
    except Exception as e:
        result["infra_error"] = f"could not spawn agent: {str(e)[:200]}"
        result.update(score_events(events))
        result["wall_s"] = round(time.monotonic() - t0, 1)
        return result

    try:
        init = {"action": "init", "env": env_name, "headed": False,
                "agentMode": "auto", "tool_budget": tool_budget}
        if project_id:
            init["project_id"] = project_id
        if provider:
            init["provider"] = provider
        ap.send(init)

        # Phase 1: wait for ready (browser start + auth). An error here is
        # infrastructure, not agent quality.
        # Deadlines use monotonic time and the queue wait is SLICED (<=30s): a
        # machine suspend froze a single long q.get past its deadline once
        # (BENCH-009 run 2: 2400s budget, 7073s wall) - short slices re-check
        # the deadline right after wake instead.
        deadline = time.monotonic() + ready_timeout
        ready = False
        while time.monotonic() < deadline:
            try:
                ev = ap.next_event(timeout=min(30, max(0.5, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if ev is None:
                result["infra_error"] = "agent process exited before ready"
                break
            record(ev)
            if ev.get("event") == "ready":
                ready = True
                result["session_id"] = ev.get("session_id")
                break
            if ev.get("event") == "error":
                result["infra_error"] = f"init failed: {str(ev.get('message', ''))[:200]}"
                break
        if not ready and not result["infra_error"]:
            result["infra_error"] = f"agent not ready within {ready_timeout}s"

        # Phase 2: the task itself - one chat turn; auto-answer any ask_user
        # pause so the run is unattended (each answer is counted by scoring).
        if ready:
            ap.send({"action": "chat", "message": task["prompt"]})
            deadline = time.monotonic() + (task.get("timeout_s") or turn_timeout)
            while time.monotonic() < deadline:
                try:
                    ev = ap.next_event(timeout=min(30, max(0.5, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if ev is None:
                    result["infra_error"] = "agent process died mid-turn"
                    break
                record(ev)
                if ev.get("event") == "input_required":
                    ap.send({"action": "chat", "message": reply})
                elif ev.get("event") == "turn_complete":
                    break
    finally:
        ap.shutdown()

    result.update(score_events(events))
    result["wall_s"] = round(time.monotonic() - t0, 1)
    if video_dir and os.path.isdir(video_dir):
        vids = sorted(f for f in os.listdir(video_dir) if f.endswith(".webm"))
        if vids:
            result["videos"] = [os.path.join(video_dir, v) for v in vids]
    if events_path:
        try:
            os.makedirs(os.path.dirname(events_path), exist_ok=True)
            with open(events_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=True, default=str) + "\n")
        except Exception as e:
            result.setdefault("errors", []).append(f"could not write events: {e}")
    return result


def verify_independent(ats_root, tests_root, task, env_name, project_id,
                       runs=2, timeout=300):
    """The leg the score hangs on: run the delivered test K times through the
    REAL pytest runner (agent_eval.run_test_once). Stops at the first failure -
    one red run already means not reliable."""
    flow_dir = os.path.join(tests_root, "flows", task["flow_id"])
    benv = _child_env(ats_root, env_name)
    benv["ATS_NO_MANUAL_INPUT"] = "1"   # unattended: an OTP gate must skip, not hang
    if project_id:
        benv["ATS_PROJECT_ID"] = project_id
    passes, last = [], ""
    for _ in range(max(1, int(runs))):
        ok, summ = agent_eval.run_test_once(flow_dir, task["tc_id"], env=benv,
                                            cwd=ats_root, timeout=timeout)
        passes.append(ok)
        last = summ or last
        if not ok:
            break
    return {"runs": passes, "verdict": agent_eval.classify(passes), "summary": last}


def keep_awake(on):
    """Hold the SYSTEM out of sleep for the duration of the run (display may
    still turn off). A machine suspend mid-run freezes the live agent's LLM
    connection and corrupts task verdicts (observed: BENCH-009 run 2, 98 min
    asleep -> ModelAPIError Connection error)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


# ── Prerequisites ──────────────────────────────────────────────────────────────

def has_admin_auth(ats_root, project_id):
    """Admin-tagged tasks need a captured admin login on the project (mirrors the
    init-time admin storage_state lookup in agent_chat)."""
    if not project_id:
        return False
    try:
        pj = os.path.join(ats_root, "projects", project_id, "project.json")
        with open(pj, "r", encoding="utf-8") as f:
            project = json.load(f)
        rel = ((project.get("admin") or {}).get("storage_state")) or ""
        return bool(rel) and os.path.exists(os.path.join(ats_root, "projects", project_id, rel))
    except Exception:
        return False


# ── CLI ────────────────────────────────────────────────────────────────────────

_VERDICT_MARK = {"reliable": "OK   ", "flaky": "FLAKY", "failing": "FAIL ",
                 "not_delivered": "NODEL", "timeout": "TIME ", "infra_error": "INFRA",
                 "delivered_unverified": "UNVER", "skipped": "SKIP "}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Benchmark the agent's test-authoring ability.")
    ap.add_argument("--ats-root", default=os.environ.get("ATS_ROOT") or ATS_ROOT_DEFAULT)
    ap.add_argument("--tasks", default=DEFAULT_TASKS)
    ap.add_argument("--only", action="append", default=[],
                    help="run only tasks whose id contains this (repeatable)")
    ap.add_argument("--project", help="project id (default from tasks file: 'test')")
    ap.add_argument("--env", help="ATS env (default from tasks file: 'dev')")
    ap.add_argument("--provider", help="LLM provider name from config.json (default: config default)")
    # Budget must sit clearly ABOVE the convergence cliff or the score measures the
    # budget, not the agent (baseline run 1: 9/10 tasks budget-stopped at 80).
    # Efficiency is still tracked separately via avg_tool_calls.
    ap.add_argument("--tool-budget", type=int, default=200,
                    help="max tool calls per task turn (0 = unlimited)")
    ap.add_argument("--turn-timeout", type=int, default=2400,
                    help="per-task wall clock for the authoring turn (s)")
    ap.add_argument("--ready-timeout", type=int, default=240)
    ap.add_argument("--verify-runs", type=int,
                    default=int(os.environ.get("ATS_VERIFY_RUNS") or 2))
    ap.add_argument("--verify-timeout", type=int, default=300)
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the independent K-run verification leg")
    ap.add_argument("--no-clean", action="store_true",
                    help="keep pre-existing bench_* flows (agent may shortcut!)")
    ap.add_argument("--no-video", action="store_true",
                    help="skip recording the agent's navigation video (saves disk; "
                         "a long task can produce 100-300MB)")
    ap.add_argument("--reply", default=DEFAULT_REPLY,
                    help="canned answer for any ask_user pause")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless every counted task is reliable (CI)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)   # progress visible when redirected
    except Exception:
        pass

    tasks, defaults = load_bench_tasks(args.tasks)
    project_id = args.project or defaults.get("project") or "test"
    env_name = args.env or defaults.get("env") or "dev"
    if args.only:
        tasks = [t for t in tasks if any(o.lower() in t["id"].lower() for o in args.only)]
    if not tasks:
        ap.error("no bench tasks selected")

    try:
        import project_store
        tests_root = project_store.resolve_tests_root(args.ats_root, project_id)
    except Exception:
        tests_root = os.path.join(args.ats_root, "tests")

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = os.path.join(args.ats_root, "results", "_agent_bench", ts)
    os.makedirs(out_dir, exist_ok=True)

    keep_awake(True)
    import atexit
    atexit.register(keep_awake, False)   # release on every exit path
    print(f"Agent authoring benchmark - {len(tasks)} task(s), project={project_id}, "
          f"env={env_name}, provider={args.provider or '(config default)'}")
    print(f"tests root: {tests_root}")
    if not args.no_clean:
        removed = clean_bench_flows(tests_root)
        print(f"cleaned bench flows: {removed or 'none'}")
    print("Each task drives the LIVE app through a fresh agent process; this takes "
          "minutes per task.\n")

    admin_ok = has_admin_auth(args.ats_root, project_id)
    results = []
    for i, task in enumerate(tasks, 1):
        label = f"[{task['id']}] ({task.get('difficulty','?')}, {task.get('app','?')})"
        if "admin" in (task.get("tags") or []) and not admin_ok:
            print(f"{i}/{len(tasks)} {label} SKIP - no captured admin login on project "
                  f"'{project_id}'")
            results.append({"task_id": task["id"], "tc_id": task["tc_id"],
                            "flow_id": task["flow_id"], "skipped": True,
                            "skip_reason": "no admin auth"})
            continue
        print(f"{i}/{len(tasks)} {label} authoring...")
        r = run_task(task, args.ats_root, project_id, env_name,
                     provider=args.provider, tool_budget=args.tool_budget,
                     ready_timeout=args.ready_timeout, turn_timeout=args.turn_timeout,
                     reply=args.reply,
                     events_path=os.path.join(out_dir, "events", f"{task['id']}.jsonl"),
                     stderr_path=os.path.join(out_dir, "stderr", f"{task['id']}.log"),
                     video_dir=None if args.no_video
                               else os.path.join(out_dir, "videos", task["id"]))
        if r.get("created") and not args.no_verify:
            print(f"    delivered (self-verified={r.get('self_verified')}, "
                  f"attempts={r.get('run_attempts')}) - verifying x{args.verify_runs}...")
            r["independent"] = verify_independent(
                args.ats_root, tests_root, task, env_name, project_id,
                runs=args.verify_runs, timeout=args.verify_timeout)
        v = task_verdict(r)
        r["verdict"] = v
        print(f"    -> {v}  (wall {r.get('wall_s', '?')}s, tools {r.get('tool_calls', '?')}, "
              f"assumed {r.get('assumed_values', 0)}, asks {r.get('asks', 0)})")
        results.append(r)

    card = summarize_bench(results)
    print("\n-- Benchmark scorecard -------------------------------------------")
    for r in results:
        mark = _VERDICT_MARK.get(r.get("verdict") or task_verdict(r), "?    ")
        ind = r.get("independent") or {}
        runs = "".join("." if p else "x" for p in ind.get("runs", [])) or "-"
        print(f"  {mark} {r['task_id']:<11} {r.get('tc_id',''):<14} "
              f"attempts={r.get('run_attempts','-')} assumed={r.get('assumed_values','-')} "
              f"verify[{runs}]")
    print("------------------------------------------------------------------")
    print(f"  benchmark score : {card['benchmark_score']}%  "
          f"({card['independent_reliable']}/{card['total']} independently reliable)")
    print(f"  delivered       : {card['delivered_pct']}%   "
          f"self-verified: {card['self_verified_pct']}%   "
          f"first-try: {card['first_try_pct']}%")
    print(f"  cost            : avg {card['avg_tool_calls']} tool calls, "
          f"avg {card['avg_wall_s']}s/task, {card['tokens_total']} tokens, "
          f"{card['asks_total']} asks, {card['assumed_values_total']} assumed values")

    payload = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
               "env": env_name, "project": project_id,
               "provider": args.provider or "(config default)",
               "verify_runs": args.verify_runs, "tool_budget": args.tool_budget,
               "scorecard": card, "tasks": results}
    out_path = os.path.join(out_dir, "scorecard.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"  scorecard -> {out_path}")
    except Exception as e:
        print(f"  (could not write scorecard: {e})")

    if args.strict and card["independent_reliable"] != card["total"]:
        return 1
    return 0


def _selftest():
    """Offline sanity for the pure core (full coverage in engine/tests)."""
    assert parse_tool_summary('{"passed": true}')["passed"] is True
    assert parse_tool_summary('{"status": "success", "tail": "' + "x" * 2000)["status"] == "success"
    evs = [{"event": "tool_call", "tool": "create_test_case", "args": {}},
           {"event": "tool_result", "tool": "create_test_case",
            "summary": '{"status": "success", "assumed_values": ["a", "b"]}'},
           {"event": "tool_call", "tool": "run_test_case", "args": {}},
           {"event": "tool_result", "tool": "run_test_case", "summary": '{"passed": true}'},
           {"event": "usage", "input": 10, "output": 5, "total": 15},
           {"event": "turn_complete", "text": "done"}]
    s = score_events(evs)
    assert s["created"] and s["self_verified"] and s["completed"]
    assert s["run_attempts"] == 1 and s["assumed_values"] == 2 and s["tokens"]["total"] == 15
    r = {"completed": True, "created": True, "independent": {"verdict": "reliable"}}
    assert task_verdict(r) == "reliable"
    card = summarize_bench([dict(r, wall_s=10, tool_calls=5, self_verified=True, run_attempts=1),
                            {"completed": True, "created": False, "wall_s": 5, "tool_calls": 3},
                            {"skipped": True}])
    assert card["total"] == 2 and card["benchmark_score"] == 50.0 and card["skipped"] == 1
    print("agent_bench selftest OK:", card)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
