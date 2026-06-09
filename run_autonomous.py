"""
Autonomous agent launcher — drives agent_chat.py via its JSON IPC.
Sends: init (resuming last session) → wait for ready → send autonomous run message → stream until done.
Usage: python run_autonomous.py [--log <path>] [--new]
  --new   force a brand-new session instead of resuming the last one
"""
import sys, os, json, subprocess, threading, time, argparse

ATS_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON   = os.path.join(ATS_ROOT, "venv", "Scripts", "python.exe")
AGENT    = os.path.join(ATS_ROOT, "engine", "agent_chat.py")
LOG_PATH = os.path.join(ATS_ROOT, "results", "_autonomous", "autonomous_run.jsonl")

# Resume the session that already has the app map (7 pages, 223 elements) and partial SOR flow.
# Pass --new on the CLI to force a fresh session instead.
RESUME_SESSION_ID = "20260606-120339-session"

AUTO_MSG = (
    "You are in AUTONOMOUS MODE. Do NOT pause, ask for confirmation, or wait for user input.\n\n"
    "STATUS FROM PREVIOUS RUNS:\n"
    "  TC-SOR-001 ✅ PASSED | TC-PAY-001 ✅ PASSED | TC-RET-001 ✅ PASSED | TC-DED-001 ✅ PASSED\n"
    "  TC-ACCT-001 ❌ BLOCKED (radio button CSS IDs with spaces) | TC-DASH-001 ⏳ not started\n\n"
    "ONLY 2 FLOWS REMAIN. Complete both without stopping:\n\n"
    "FLOW 5 — Account Statement (TC-ACCT-001) at /account-statement:\n"
    "  KNOWN ISSUE: Radio button refs like 'last-7-days' fail because the CSS ID contains a space.\n"
    "  WORKAROUND: Do NOT click radio buttons. Instead:\n"
    "    a. navigate to /account-statement\n"
    "    b. inspect_page\n"
    "    c. clear_recording\n"
    "    d. add_checkpoint 'Account Statement page loads' (page_contains_text: 'Request Statement')\n"
    "    e. Click the 'request-statement' button ref directly (or whatever ref inspect gives you)\n"
    "    f. add_checkpoint 'Request Statement button is clickable'\n"
    "    g. create_test_case TC-ACCT-001 flow_id='account_statement'\n"
    "    h. run_test_case — if it fails because click opened a modal or navigated, "
    "       re-drive using only page_contains_text assertions (no button clicks after navigate)\n\n"
    "FLOW 6 — Dashboard (TC-DASH-001) at /:\n"
    "    a. navigate to https://supplier-dev.agrim.app/\n"
    "    b. inspect_page — identify nav link refs (sor, orders, payments, returns, deductions, account-statement)\n"
    "    c. clear_recording\n"
    "    d. click ref 'sor' → add_checkpoint 'SOR nav link works' (url_contains: '/sor')\n"
    "    e. click ref 'orders' → add_checkpoint 'Orders nav link works' (url_contains: '/orders')\n"
    "    f. click ref 'payments' → add_checkpoint (url_contains: '/payments')\n"
    "    g. navigate back to / after each or use the nav links which stay mounted\n"
    "    h. create_test_case TC-DASH-001 flow_id='dashboard'\n"
    "    i. run_test_case\n\n"
    "After BOTH flows are done (pass OR documented fail), print the FINAL SUMMARY TABLE:\n"
    "  | Flow | TC-ID | Result | Checkpoints | Notes |\n"
    "  (include all 6 flows: SOR ✅, Payments ✅, Returns ✅, Deductions ✅, Acct Stmt, Dashboard)\n\n"
    "Start now with Flow 5 (Account Statement). Do not stop or ask for permission."
)

CONTINUE_MSG = (
    "Continue autonomously. Move to the next flow without stopping. "
    "Keep going until all 6 flows are done and you have printed the final summary table."
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=LOG_PATH)
    parser.add_argument("--new", action="store_true", help="Force a fresh session (ignore resume)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    log_f = open(args.log, "w", encoding="utf-8")

    def emit(obj):
        line = json.dumps(obj, ensure_ascii=True)   # ASCII-safe for cp1252 stdout
        log_f.write(line + "\n")
        log_f.flush()
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    session_id = None if args.new else RESUME_SESSION_ID
    emit({"event": "launcher_start", "message": f"Starting agent_chat.py (session={session_id or 'new'})..."})

    env = os.environ.copy()
    env["ATS_ROOT"]              = ATS_ROOT
    env["PYTHONPATH"]            = ATS_ROOT
    env["PYTHONUNBUFFERED"]      = "1"
    env["ATS_AGENT_VISION"]      = "off"   # MiMo endpoint rejects image input
    env["ATS_AGENT_TOOL_BUDGET"] = "120"   # allow enough calls for 6 flows x ~20 tools each

    proc = subprocess.Popen(
        [PYTHON, AGENT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=ATS_ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    # ── Read stdout in a thread, push lines to a queue ──────────────────────
    import queue as q_mod
    out_q = q_mod.Queue()

    def reader():
        for raw in proc.stdout:
            out_q.put(raw.decode("utf-8", "replace").rstrip())
        out_q.put(None)   # EOF sentinel

    threading.Thread(target=reader, daemon=True).start()

    def send(cmd_dict):
        line = json.dumps(cmd_dict) + "\n"
        proc.stdin.write(line.encode())
        proc.stdin.flush()

    def wait_for(event_name, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = out_q.get(timeout=2)
            except q_mod.Empty:
                continue
            if raw is None:
                return None
            try:
                obj = json.loads(raw)
            except Exception:
                emit({"event": "raw", "line": raw})
                continue
            emit(obj)
            if obj.get("event") == event_name:
                return obj
        return None  # timeout

    # ── Step 1: send init (resume or new session) ────────────────────────────
    init_cmd = {"action": "init", "project_id": "test", "env": "dev",
                "provider": "mimo", "headed": False}
    if session_id:
        init_cmd["session_id"] = session_id
    emit({"event": "launcher", "message": f"Sending init (session={session_id or 'new'})..."})
    send(init_cmd)

    ready = wait_for("ready", timeout=120)
    if not ready:
        emit({"event": "launcher_error", "message": "Timed out waiting for ready"})
        proc.kill()
        return 1
    resumed = ready.get("resumed", False)
    emit({"event": "launcher", "message": f"Agent ready at {ready.get('url')} (resumed={resumed}) — sending autonomous message"})

    # ── Step 3: send the autonomous run message ──────────────────────────────
    send({"action": "chat", "message": AUTO_MSG})

    # ── Step 4: stream, auto-continuing after each turn_complete until done ─────
    emit({"event": "launcher", "message": "Autonomous run in progress…"})
    turn = 0
    max_turns = 20   # safety cap — each turn authors ~2-3 flows
    done_keywords = ("all flows", "all tests", "complete", "finished", "no more flows",
                     "nothing left", "all done", "summary table", "final summary")
    while True:
        try:
            raw = out_q.get(timeout=5)
        except q_mod.Empty:
            continue
        if raw is None:
            emit({"event": "launcher", "message": "Agent process exited"})
            break
        try:
            obj = json.loads(raw)
        except Exception:
            emit({"event": "raw", "line": raw})
            continue
        emit(obj)
        if obj.get("event") == "turn_complete":
            turn += 1
            final_text = (obj.get("text") or "").lower()
            # Only stop when ALL target flows appear in the summary — not on mid-run tables.
            # Require explicit completion language OR all 6 TC-IDs present in one table.
            import re as _re
            # Require all 6 TC-IDs with a PASSED marker — not just present (blocked/pending don't count)
            import re as _re
            def _tc_passed(tc_id):
                # Look for "tc-xxx-001" followed within 120 chars by a pass indicator
                pat = tc_id + r'.{0,120}(passed|✅|pass)'
                return bool(_re.search(pat, final_text, _re.IGNORECASE))
            all_six_passed = (
                _tc_passed("tc-sor-001") and _tc_passed("tc-pay-001") and
                _tc_passed("tc-ret-001") and _tc_passed("tc-ded-001") and
                _tc_passed("tc-acct-001") and _tc_passed("tc-dash-001")
            )
            hard_done = (
                "all flows have been authored" in final_text or
                "all test cases have been" in final_text or
                "no more flows to test" in final_text or
                "nothing left to author" in final_text or
                all_six_passed   # all 6 TCs confirmed PASSED
            )
            if turn >= max_turns or hard_done:
                emit({"event": "launcher", "message": f"Done after {turn} turn(s) — shutting down"})
                send({"action": "shutdown"})
                time.sleep(3)
                break
            emit({"event": "launcher", "message": f"Turn {turn} complete — sending continue…"})
            time.sleep(2)
            send({"action": "chat", "message": CONTINUE_MSG})
        if obj.get("event") == "error":
            emit({"event": "launcher_error", "message": obj.get("message", "")})
            # Per-turn errors are non-fatal — keep running

    proc.stdin.close()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

    emit({"event": "launcher_done", "log": args.log})
    log_f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
