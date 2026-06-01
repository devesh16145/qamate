"""
Agrim ATS — Canonical Run Results
=================================

Builds ONE authoritative, self-contained record for a run by treating the
machine-readable JUnit XML pytest emits as the source of truth for
pass/fail/skip/error + duration + error text — instead of scraping stdout — and
merging in the per-checkpoint detail (with severity + the criteria verdict),
self-heal events, and artifact presence.

The result is embedded into run_metadata.json so history is complete and
correct even if the live UI state is stale or the run was stopped mid-way, and
so the UI can render a run without re-deriving anything.

Statuses are normalized to the UI's vocabulary: "pass" | "fail" | "skip".
"""

import os
import re
import json
import xml.etree.ElementTree as ET

SCHEMA_VERSION = 2
_BROWSER_NAMES = {"chromium", "firefox", "webkit"}


def tc_id_and_key(test_name):
    """From a (possibly parametrized) pytest test name derive (tc_id, key).

    key carries the data-variant suffix (browser-name params stripped) and must
    match conftest._tc_id_with_variant so checkpoint/heal/trace files line up:
        test_TC_CATALOG_010_x[chromium-negative] -> ("TC-CATALOG-010", "TC-CATALOG-010_negative")
        test_TC_ORDERS_063_po[chromium]          -> ("TC-ORDERS-063",  "TC-ORDERS-063")
    """
    base = test_name.split("[")[0]
    m = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', base)
    tc_id = m.group(1).replace("_", "-") if m else base
    key = tc_id
    bm = re.search(r'\[(.+?)\]', test_name)
    if bm:
        tokens = [t for t in bm.group(1).split("-") if t and t.lower() not in _BROWSER_NAMES]
        if tokens:
            key = tc_id + "_" + "-".join(tokens)
    return tc_id, key


def _status(testcase):
    if testcase.find("failure") is not None:
        return "fail"
    if testcase.find("error") is not None:
        return "fail"
    if testcase.find("skipped") is not None:
        return "skip"
    return "pass"


def _error_text(testcase):
    for tag in ("failure", "error"):
        el = testcase.find(tag)
        if el is not None:
            msg = (el.get("message") or el.text or "").strip()
            return msg[:1000]
    return ""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _first_existing(d, names, ext):
    """First existing file in dir `d` named <name><ext> for name in names."""
    if not os.path.isdir(d):
        return None
    for name in names:
        p = os.path.join(d, name + ext)
        if os.path.exists(p):
            return p
    # prefix fallback (variant files we couldn't name exactly)
    for name in names:
        for f in sorted(os.listdir(d)):
            if f.endswith(ext) and f[:-len(ext)].startswith(name):
                return os.path.join(d, f)
    return None


def load_run_results(results_dir, flaky_ids=None):
    """Return the canonical results dict for a run directory, or None if there's
    no JUnit XML to read."""
    flaky_ids = set(flaky_ids or [])
    junit = os.path.join(results_dir, "junit_results.xml")
    if not os.path.exists(junit):
        return None
    try:
        root = ET.parse(junit).getroot()
    except Exception:
        return None

    cp_dir = os.path.join(results_dir, "checkpoints")
    heal_dir = os.path.join(results_dir, "heals")
    trace_dir = os.path.join(results_dir, "traces")
    net_dir = os.path.join(results_dir, "network")

    tests = []
    counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0,
              "flaky": 0, "warnings": 0}
    total_duration = 0.0

    for tcase in root.iter("testcase"):
        name = tcase.get("name", "")
        tc_id, key = tc_id_and_key(name)
        status = _status(tcase)
        duration = float(tcase.get("time", 0) or 0)
        total_duration += duration

        # Merge checkpoint detail (variant key first, then bare tc_id).
        cp_names = [key.replace("-", "_"), tc_id.replace("-", "_")]
        cp = _read_json(_first_existing(cp_dir, cp_names, ".json") or "")
        checkpoints = cp.get("checkpoints", [])
        verdict = cp.get("verdict")
        criteria = cp.get("criteria")
        warnings = sum(1 for c in checkpoints
                       if c.get("status") == "FAIL" and c.get("severity") in ("normal", "minor"))

        # Self-heals + trace presence.
        heal = _read_json(_first_existing(heal_dir, [key, tc_id], ".json") or "")
        heal_count = len(heal.get("heals", []))
        trace_path = _first_existing(trace_dir, [key, tc_id], ".zip")
        net = _read_json(_first_existing(net_dir, [key, tc_id], ".json") or "")
        network_count = len(net.get("calls", []))

        flaky = status == "pass" and (key in flaky_ids or tc_id in flaky_ids)

        counts["total"] += 1
        if status == "pass":
            counts["passed"] += 1
        elif status == "fail":
            counts["failed"] += 1
        elif status == "skip":
            counts["skipped"] += 1
        if flaky:
            counts["flaky"] += 1
        counts["warnings"] += warnings

        tests.append({
            "tc_id": tc_id,
            "key": key,
            "name": name,
            "status": status,
            "verdict": verdict or ("PASS" if status == "pass" else status.upper()),
            "duration": round(duration, 2),
            "error": _error_text(tcase),
            "flaky": flaky,
            "warnings": warnings,
            "heal_count": heal_count,
            "has_trace": bool(trace_path),
            "network_count": network_count,
            "checkpoints": checkpoints,
            "criteria": criteria,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "duration": round(total_duration, 2),
        "tests": tests,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: results_loader.py <results_dir>"}))
        sys.exit(1)
    res = load_run_results(sys.argv[1])
    print(json.dumps(res, indent=2) if res else json.dumps({"error": "no junit_results.xml"}))
