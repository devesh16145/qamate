"""
QAmate — Test Runner Engine

This script is spawned by the Electron main process as a child process.
It receives JSON commands on stdin and emits JSON events on stdout.

Communication protocol:
  stdin  → {"action": "run", "tc_ids": [...], "env": "dev", "mode": "headless", "parallel": 4, "ats_root": "..."}
  stdout ← {"event": "log",          "message": "..."}
  stdout ← {"event": "tc_start",     "tc_id": "...", "description": "..."}
  stdout ← {"event": "tc_result",    "tc_id": "...", "status": "PASS|FAIL", "duration": 1.23}
  stdout ← {"event": "progress",     "passed": N, "failed": N, "total": N}
  stdout ← {"event": "run_complete", "summary": {...}}
"""

import sys
import json
import subprocess
import os
import re
import time
import datetime
import threading

# Code root (engine/ + tests/conftest.py). ATS_ROOT is the data root; they differ only
# in a packaged macOS app, where the code is read-only (see bootstrap.js).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def emit(event_data):
    """Send a JSON event to the Electron main process via stdout."""
    print(json.dumps(event_data), flush=True)

def log(message):
    """Shorthand for emitting a log event."""
    emit({"event": "log", "message": str(message)})


def _load_pass_criteria(ats_root):
    """Read pass_criteria from config.json (best-effort)."""
    try:
        with open(os.path.join(ats_root, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("pass_criteria") or {}
    except Exception:
        return {}


def _has_rerunfailures():
    """True if pytest-rerunfailures is installed (enables flaky retry)."""
    import importlib.util
    return importlib.util.find_spec("pytest_rerunfailures") is not None


def run_tests(tc_ids, env, mode, parallel, ats_root, zoom="", user_index=0, exec_mode="sequential",
              seller_user_index=None, admin_user_index=None, variant=None, project_id=None):
    """Execute selected test cases via pytest subprocess."""
    start_time = time.time()

    # Resolve absolute paths
    ats_root = os.path.abspath(ats_root)
    results_base = os.path.join(ats_root, "results")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    results_dir = os.path.join(results_base, timestamp)
    os.makedirs(results_dir, exist_ok=True)

    log(f"Run started: {timestamp}")
    log(f"Selected {len(tc_ids)} test cases")
    log(f"Environment: {env} | Mode: {mode} | Workers: {parallel}")
    log(f"Results dir: {results_dir}")

    # Save initial metadata
    metadata = {
        "timestamp": timestamp,
        "env": env,
        "mode": mode,
        "parallel": parallel,
        "tc_ids": tc_ids,
        "project_id": project_id or None,
        "status": "Running",
    }
    meta_path = os.path.join(results_dir, "run_metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # ── Build pytest command ──
    # pytest -k interprets hyphens as subtraction. TC IDs use hyphens (TC-CATALOG-001).
    # We converted function names to use underscores (test_TC_CATALOG_001_...), so we
    # need to convert the GUI's hyphenated IDs to underscored versions for -k filtering.
    underscored_ids = [tc_id.replace("-", "_") for tc_id in tc_ids]
    filter_expr = " or ".join(underscored_ids)

    # Project mode: a project with its OWN suite (projects/<id>/tests) runs that;
    # otherwise the legacy shared suite — existing runs are unchanged.
    import project_store as _ps
    tests_dir = _ps.resolve_tests_root(ats_root, project_id)
    junit_path = os.path.join(results_dir, "junit_results.xml")

    cmd = [
        sys.executable,
        "-m", "pytest",
        tests_dir,
        f"--junitxml={junit_path}",
        "-v",
        "--tb=short",
        "-s",  # Prevent pytest from capturing stdout, which can cause double-buffering hangs
        "-k", filter_expr,
    ]
    # Separate data root (packaged macOS app): the suite is no longer under the
    # code checkout, so point pytest at the shipped ini and root it at the data dir.
    app_ini = os.path.join(_APP_ROOT, "pytest.ini")
    if os.path.abspath(ats_root) != _APP_ROOT and os.path.isfile(app_ini):
        cmd += ["-c", app_ini, "--rootdir", ats_root]

    if mode != "headless":
        cmd.append("--headed")

    # Parallel only in parallel mode with multiple tests
    if exec_mode == "parallel" and parallel > 1 and len(tc_ids) > 1:
        cmd.extend(["-n", str(min(parallel, len(tc_ids)))])

    # ── Flaky retry (pytest-rerunfailures) per pass_criteria ──
    pass_criteria = _load_pass_criteria(ats_root)
    try:
        retries = int(pass_criteria.get("retries", 0) or 0)
    except (TypeError, ValueError):
        retries = 0
    if retries > 0 and _has_rerunfailures():
        cmd.extend(["--reruns", str(retries), "--reruns-delay", "1"])
        log(f"Flaky retry enabled: up to {retries} rerun(s) of a failed test")
    elif retries > 0:
        log("pass_criteria.retries set but pytest-rerunfailures is not installed — retry skipped")

    log(f"Command: {' '.join(cmd)}")

    # ── Environment variables for conftest.py ──
    test_env = os.environ.copy()
    test_env["ATS_ENV"] = env
    # Project mode: when set, conftest targets this project's URL + captured auth
    # instead of the legacy platforms config.
    if project_id:
        test_env["ATS_PROJECT_ID"] = project_id
    test_env["ATS_RESULTS_DIR"] = results_dir
    test_env["ATS_RUN_TIMESTAMP"] = timestamp
    test_env["ATS_ROOT"] = ats_root
    test_env["ATS_APP_ROOT"] = _APP_ROOT
    test_env["PYTHONPATH"] = _APP_ROOT
    test_env["PYTHONUNBUFFERED"] = "1"

    # Browser zoom override from UI selector
    if zoom:
        test_env["ATS_ZOOM"] = zoom

    # User index for test credentials (backward compat)
    test_env["ATS_USER_INDEX"] = str(user_index)
    # Platform-specific user indices
    test_env["ATS_SELLER_USER_INDEX"] = str(seller_user_index if seller_user_index is not None else user_index)
    test_env["ATS_ADMIN_USER_INDEX"] = str(admin_user_index if admin_user_index is not None else 0)

    # Execution mode (sequential / parallel)
    test_env["ATS_EXEC_MODE"] = exec_mode

    # Target variant (if single TC and single variant selected)
    if variant:
        test_env["ATS_VARIANT"] = variant

    # ── Run pytest ──
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffering
            universal_newlines=True,
            env=test_env,
            cwd=ats_root,
        )
    except Exception as e:
        log(f"ERROR: Failed to start pytest: {e}")
        emit({"event": "run_complete", "summary": {**metadata, "status": "Error", "error": str(e)}})
        return

    # ── Manual-input gate (OTP etc.) ─────────────────────────────────────────
    # A test's manual_input() fixture writes <results>/manual_input/<id>.request.json
    # and polls for the matching .response.json. This watcher surfaces requests to
    # the UI as events; main.js writes the response file when the user answers.
    # File-based (not stdout) so it also works under pytest-xdist workers.
    import threading
    _mi_dir = os.path.join(results_dir, "manual_input")
    _mi_stop = threading.Event()

    def _watch_manual_input():
        seen = set()
        while not _mi_stop.is_set():
            try:
                for name in (os.listdir(_mi_dir) if os.path.isdir(_mi_dir) else []):
                    if not name.endswith(".request.json") or name in seen:
                        continue
                    seen.add(name)
                    try:
                        with open(os.path.join(_mi_dir, name), "r", encoding="utf-8") as f:
                            req = json.load(f)
                        emit({"event": "manual_input_required", **req})
                        log(f"PAUSED for manual input: {req.get('prompt', '')[:80]}")
                    except Exception:
                        pass
            except Exception:
                pass
            _mi_stop.wait(1.0)

    _mi_thread = threading.Thread(target=_watch_manual_input, daemon=True)
    _mi_thread.start()

    # ── Parse pytest output line by line ──
    passed = 0
    failed = 0
    skipped = 0
    total = len(tc_ids)
    rerun_seen = set()  # tc_ids that pytest-rerunfailures retried (flaky candidates)

    # Regex to match pytest result lines like:
    # tests/flows/catalog/test_catalog.py::test_TC_CATALOG_001_page_loads[chromium] PASSED  [33%]
    result_re = re.compile(
        r'(?:PASSED|FAILED|ERROR|SKIPPED)',
        re.IGNORECASE,
    )

    # Buffer to capture error details between FAILURES header and next test/summary
    error_buffer = []
    capturing_error = False
    current_error_tc = None

    # Store error details per TC
    error_details = {}

    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue

        # Always forward to GUI log
        log(line)

        # Detect start of a failure block: "_______ test_TC_SIGNUP_001[chromium] _______"
        failure_header = re.search(r'_{3,}\s+test_(TC_\w+)', line)
        if failure_header:
            # Save previous error if any
            if current_error_tc and error_buffer:
                error_details[current_error_tc] = "\n".join(error_buffer)
            current_error_tc = failure_header.group(1).replace("_", "-")
            error_buffer = []
            capturing_error = True
            continue

        if capturing_error:
            # Stop capturing at the next test result or summary section
            if "PASSED" in line or "FAILED" in line or "short test summary" in line or "=====" in line:
                if current_error_tc and error_buffer:
                    error_details[current_error_tc] = "\n".join(error_buffer)
                capturing_error = False
                current_error_tc = None
                error_buffer = []
            else:
                error_buffer.append(line)

        # pytest-rerunfailures retried this test (failed attempt → will rerun).
        # The final attempt still prints PASSED/FAILED and is counted there; here
        # we only note that a retry happened so we can label the test FLAKY.
        if " RERUN" in line:
            rtc = extract_tc_id(line)
            if rtc:
                rerun_seen.add(rtc)
            continue

        # Parse for test results
        if " PASSED" in line:
            passed += 1
            tc_id = extract_tc_id(line)
            if tc_id:
                cps = _read_checkpoints(results_dir, tc_id)
                # A trace exists for a passing test only when tracing mode is "on"
                artifacts = _find_test_artifacts(ats_root, tc_id, results_dir)
                heals = _read_heals(results_dir, tc_id)
                emit({"event": "tc_result", "tc_id": tc_id, "status": "PASS", "duration": 0,
                      "checkpoints": cps, "trace_path": artifacts.get("trace"),
                      "heals": heals, "heal_count": len(heals)})
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

        elif " FAILED" in line or " ERROR" in line:
            failed += 1
            tc_id = extract_tc_id(line)
            if tc_id:
                cps = _read_checkpoints(results_dir, tc_id)
                # Find trace and screenshot artifacts for this test
                artifacts = _find_test_artifacts(ats_root, tc_id, results_dir)
                error_msg = error_details.get(tc_id, "")
                # Extract the core error message (last E line)
                short_error = ""
                for err_line in error_msg.split("\n"):
                    if err_line.strip().startswith("E "):
                        short_error = err_line.strip()[2:].strip()
                heals = _read_heals(results_dir, tc_id)
                emit({
                    "event": "tc_result",
                    "tc_id": tc_id,
                    "status": "FAIL",
                    "duration": 0,
                    "checkpoints": cps,
                    "error_message": short_error,
                    "error_details": error_msg,
                    "trace_path": artifacts.get("trace"),
                    "screenshot_path": artifacts.get("screenshot"),
                    "heals": heals,
                    "heal_count": len(heals),
                })
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

        elif " SKIPPED" in line:
            skipped += 1
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

    process.wait()
    _mi_stop.set()
    duration_s = round(time.time() - start_time, 1)
    log(f"Pytest exited with code {process.returncode}")

    # ── Generate report ──
    report_path = None
    try:
        engine_dir = os.path.join(_APP_ROOT, "engine")
        sys.path.insert(0, engine_dir)
        from report_generator import generate_report
        report_path = generate_report(results_dir)
        log(f"Report generated: {report_path}")
    except Exception as e:
        log(f"Warning: Report generation failed: {e}")

    # ── Finalize metadata — authoritative counts + per-test record from JUnit ──
    # (not stdout scraping). This makes history correct and self-contained even
    # when the live UI state is stale. Written defensively so a loader hiccup
    # can never leave the run stuck on "Running".
    duration_str = f"{int(duration_s // 60)}m {int(duration_s % 60)}s" if duration_s >= 60 else f"{duration_s}s"
    canonical = None
    try:
        from results_loader import load_run_results
        canonical = load_run_results(results_dir, flaky_ids=rerun_seen)
    except Exception as e:
        log(f"Warning: results loader failed, falling back to live counts: {e}")

    if canonical:
        c = canonical["counts"]
        metadata.update({
            "status": "Completed",
            "schema_version": canonical["schema_version"],
            "total": c["total"],
            "passed": c["passed"],
            "failed": c["failed"],
            "skipped": c["skipped"],
            "flaky": c["flaky"],
            "warnings": c["warnings"],
            "duration": duration_str,
            "duration_s": duration_s,
            "report_path": report_path,
            "folder_path": results_dir,
            "results": canonical["tests"],
        })
        log(f"Results: {c['passed']} passed, {c['failed']} failed, {c['skipped']} skipped"
            + (f", {c['flaky']} flaky" if c['flaky'] else "")
            + (f", {c['warnings']} warning(s)" if c['warnings'] else ""))
    else:
        # Fallback: stdout-scraped counts (only if JUnit was unreadable)
        metadata.update({
            "status": "Completed",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration": duration_str,
            "duration_s": duration_s,
            "report_path": report_path,
            "folder_path": results_dir,
        })

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        log(f"ERROR: could not write run_metadata.json: {e}")

    emit({"event": "run_complete", "summary": metadata})


def extract_tc_id(line):
    """Extract TC ID from a pytest output line.

    Example line:
      tests/flows/catalog/test_catalog.py::test_TC_CATALOG_001_page_loads[chromium] PASSED
    Returns: TC-CATALOG-001
    """
    match = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', line)
    if match:
        # Convert TC_CATALOG_001 back to TC-CATALOG-001
        return match.group(1).replace("_", "-")
    return None


def _read_checkpoints(results_dir, tc_id):
    """Read checkpoint results written by conftest.py during the test run."""
    cp_dir = os.path.join(results_dir, "checkpoints")
    safe_id = tc_id.replace("-", "_")
    cp_file = os.path.join(cp_dir, f"{safe_id}.json")
    if os.path.exists(cp_file):
        try:
            with open(cp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("checkpoints", [])
        except Exception:
            pass
    return []


def _read_heals(results_dir, tc_id):
    """Read self-heal events written by conftest's healer fixture, if any."""
    heal_file = os.path.join(results_dir, "heals", f"{tc_id}.json")
    if os.path.exists(heal_file):
        try:
            with open(heal_file, "r", encoding="utf-8") as f:
                return json.load(f).get("heals", [])
        except Exception:
            pass
    return []


def _find_test_artifacts(ats_root, tc_id, results_dir=None):
    """Find trace.zip and screenshot.png for a test.

    Preferred source is the per-run folder written by conftest.py:
      results/<timestamp>/traces/<tc_id>.zip
      results/<timestamp>/screenshots/<tc_name>_FAILED.png
    Falls back to the pytest-playwright layout:
      test-results/test-TC-SIGNUP-001-chromium/trace.zip
    """
    artifacts = {}

    # ── Preferred: this run's traces/ and screenshots/ folders ──
    if results_dir:
        traces_dir = os.path.join(results_dir, "traces")
        if os.path.isdir(traces_dir):
            exact = os.path.join(traces_dir, f"{tc_id}.zip")
            if os.path.exists(exact):
                artifacts["trace"] = exact
            else:
                # variant traces are named <tc_id>_<variant>.zip
                for f in sorted(os.listdir(traces_dir)):
                    if f.endswith(".zip") and f.startswith(tc_id):
                        artifacts["trace"] = os.path.join(traces_dir, f)
                        break
        ss_dir = os.path.join(results_dir, "screenshots")
        if os.path.isdir(ss_dir):
            underscored = tc_id.replace("-", "_")
            for f in sorted(os.listdir(ss_dir)):
                if f.endswith(".png") and (tc_id in f or underscored in f):
                    artifacts["screenshot"] = os.path.join(ss_dir, f)
                    break

    if "trace" in artifacts:
        return artifacts

    # ── Fallback: pytest-playwright test-results/ layout ──
    test_results_dir = os.path.join(ats_root, "test-results")
    if not os.path.exists(test_results_dir):
        return artifacts

    # TC-SIGNUP-001 -> TC-SIGNUP-001 (search in folder names)
    underscored = tc_id.replace("-", "_")
    for entry in os.listdir(test_results_dir):
        entry_path = os.path.join(test_results_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        # Match folder name containing the TC ID (underscored or hyphenated)
        if underscored.lower() in entry.lower() or tc_id.lower() in entry.lower():
            # Look for trace
            trace_path = os.path.join(entry_path, "trace.zip")
            if os.path.exists(trace_path):
                artifacts["trace"] = trace_path
            # Look for screenshots (test-failed-1.png pattern)
            for f in os.listdir(entry_path):
                if f.endswith(".png"):
                    artifacts["screenshot"] = os.path.join(entry_path, f)
                    break
            break

    return artifacts


def main():
    log("Engine started. Waiting for commands...")

    while True:
        raw_line = sys.stdin.readline()
        if not raw_line:
            # EOF reached
            break
            
        line = raw_line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"ERROR: Invalid JSON received: {e}")
            continue

        action = data.get("action")

        if action == "run":
            tc_ids = data.get("tc_ids", [])
            env = data.get("env", "dev")
            mode = data.get("mode", "headless")
            parallel = data.get("parallel", 1)
            ats_root = data.get("ats_root", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            zoom = data.get("zoom", "")
            user_index = data.get("userIndex", 0)
            seller_user_index = data.get("sellerUserIndex", user_index)
            admin_user_index = data.get("adminUserIndex", 0)
            variant = data.get("variant", None)
            exec_mode = data.get("execMode", "sequential")
            project_id = data.get("project_id") or data.get("projectId")

            if not tc_ids:
                log("ERROR: No test cases provided")
                continue

            log(f"Received run command: {len(tc_ids)} tests")
            log(f"Execution mode: {exec_mode}")
            if zoom:
                log(f"Browser zoom: {zoom}%")
            log(f"Seller user: {seller_user_index}, Admin user: {admin_user_index}")
            # Run in a thread so stdin remains readable (for stop commands)
            if variant:
                log(f"Target variant: {variant}")
            if project_id:
                log(f"Project: {project_id}")
            t = threading.Thread(target=run_tests, args=(tc_ids, env, mode, parallel, ats_root, zoom, user_index, exec_mode),
                                 kwargs={"seller_user_index": seller_user_index, "admin_user_index": admin_user_index,
                                         "variant": variant, "project_id": project_id},
                                 daemon=True)
            t.start()

        elif action == "stop":
            log("Stop command received (not yet implemented — close the engine instead)")

        else:
            log(f"Unknown action: {action}")


if __name__ == "__main__":
    main()

