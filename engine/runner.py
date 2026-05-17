"""
Agrim ATS — Test Runner Engine

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

def emit(event_data):
    """Send a JSON event to the Electron main process via stdout."""
    print(json.dumps(event_data), flush=True)

def log(message):
    """Shorthand for emitting a log event."""
    emit({"event": "log", "message": str(message)})


def run_tests(tc_ids, env, mode, parallel, ats_root, zoom="", user_index=0, exec_mode="sequential",
              seller_user_index=None, admin_user_index=None, variant=None):
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

    tests_dir = os.path.join(ats_root, "tests")
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

    if mode != "headless":
        cmd.append("--headed")

    # Parallel only in parallel mode with multiple tests
    if exec_mode == "parallel" and parallel > 1 and len(tc_ids) > 1:
        cmd.extend(["-n", str(min(parallel, len(tc_ids)))])

    log(f"Command: {' '.join(cmd)}")

    # ── Environment variables for conftest.py ──
    test_env = os.environ.copy()
    test_env["ATS_ENV"] = env
    test_env["ATS_RESULTS_DIR"] = results_dir
    test_env["ATS_RUN_TIMESTAMP"] = timestamp
    test_env["ATS_ROOT"] = ats_root
    test_env["PYTHONPATH"] = ats_root
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

    # ── Parse pytest output line by line ──
    passed = 0
    failed = 0
    skipped = 0
    total = len(tc_ids)

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

        # Parse for test results
        if " PASSED" in line:
            passed += 1
            tc_id = extract_tc_id(line)
            if tc_id:
                cps = _read_checkpoints(results_dir, tc_id)
                emit({"event": "tc_result", "tc_id": tc_id, "status": "PASS", "duration": 0, "checkpoints": cps})
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

        elif " FAILED" in line or " ERROR" in line:
            failed += 1
            tc_id = extract_tc_id(line)
            if tc_id:
                cps = _read_checkpoints(results_dir, tc_id)
                # Find trace and screenshot artifacts for this test
                artifacts = _find_test_artifacts(ats_root, tc_id)
                error_msg = error_details.get(tc_id, "")
                # Extract the core error message (last E line)
                short_error = ""
                for err_line in error_msg.split("\n"):
                    if err_line.strip().startswith("E "):
                        short_error = err_line.strip()[2:].strip()
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
                })
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

        elif " SKIPPED" in line:
            skipped += 1
            emit({"event": "progress", "passed": passed, "failed": failed, "skipped": skipped, "total": total})

    process.wait()
    duration_s = round(time.time() - start_time, 1)
    log(f"Pytest exited with code {process.returncode}")

    # ── Generate report ──
    report_path = None
    try:
        engine_dir = os.path.join(ats_root, "engine")
        sys.path.insert(0, engine_dir)
        from report_generator import generate_report
        report_path = generate_report(results_dir)
        log(f"Report generated: {report_path}")
    except Exception as e:
        log(f"Warning: Report generation failed: {e}")

    # ── Finalize metadata ──
    duration_str = f"{int(duration_s // 60)}m {int(duration_s % 60)}s" if duration_s >= 60 else f"{duration_s}s"
    metadata.update({
        "status": "Completed",
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration": duration_str,
        "report_path": report_path,
        "folder_path": results_dir,
    })
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    emit({"event": "run_complete", "summary": metadata})


def extract_tc_id(line):
    """Extract TC ID from a pytest output line.

    Example line:
      tests/flows/catalog/test_catalog.py::test_TC_CATALOG_001_page_loads[chromium] PASSED
    Returns: TC-CATALOG-001
    """
    match = re.search(r'(TC_[A-Z]+_\d+)', line)
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


def _find_test_artifacts(ats_root, tc_id):
    """Find trace.zip and screenshot.png for a failed test in test-results/.

    pytest-playwright saves artifacts in directories like:
      test-results/test-TC-SIGNUP-001-chromium/trace.zip
      test-results/test-TC-SIGNUP-001-chromium/test-failed-1.png
    """
    artifacts = {}
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
            t = threading.Thread(target=run_tests, args=(tc_ids, env, mode, parallel, ats_root, zoom, user_index, exec_mode),
                                 kwargs={"seller_user_index": seller_user_index, "admin_user_index": admin_user_index, "variant": variant},
                                 daemon=True)
            t.start()

        elif action == "stop":
            log("Stop command received (not yet implemented — close the engine instead)")

        else:
            log(f"Unknown action: {action}")


if __name__ == "__main__":
    main()

