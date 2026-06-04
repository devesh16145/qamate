"""
Tests for the agent reliability eval harness (engine/agent_eval.py).

The pure scoring core (classify / summarize) is tested directly. The subprocess
driver (run_test_once / evaluate) is exercised for real against a SYNTHETIC
pass/fail pytest module in a temp dir — proving the runner integration works
offline, with no browser and no live app. That makes the harness itself trusted,
not just asserted.
"""

import os
import textwrap

import pytest

import agent_eval as ev


# ── pure core ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("passes,verdict", [
    ([True, True], "reliable"),
    ([True], "reliable"),
    ([True, False], "flaky"),
    ([False, True], "flaky"),
    ([False], "failing"),
    ([False, False], "failing"),
    ([], "unknown"),
])
def test_classify(passes, verdict):
    assert ev.classify(passes) == verdict


def test_summarize_counts_and_rates():
    results = [
        {"runs": [True, True], "verdict": "reliable"},
        {"runs": [True, True], "verdict": "reliable"},
        {"runs": [True, False], "verdict": "flaky"},
        {"runs": [False], "verdict": "failing"},
    ]
    card = ev.summarize(results)
    assert card["total"] == 4
    assert card["reliable"] == 2 and card["flaky"] == 1 and card["failing"] == 1
    assert card["reliable_pct"] == 50.0
    assert card["reliability_score"] == 50.0
    # first-pass = first run green: reliable(2) + the flaky one that passed run 1 = 3/4
    assert card["first_pass_pct"] == 75.0


def test_summarize_empty():
    card = ev.summarize([])
    assert card["total"] == 0 and card["reliability_score"] == 0.0


def test_summarize_derives_verdict_when_missing():
    # verdict omitted -> derived from runs
    card = ev.summarize([{"runs": [True, True]}, {"runs": [False]}])
    assert card["reliable"] == 1 and card["failing"] == 1


def test_summary_parses_banner():
    assert ev._summary("==== 1 passed in 0.1s ====") == "1 passed in 0.1s"
    assert ev._summary("no banner here") == ""


# ── subprocess driver (real pytest, synthetic test, no browser) ──────────────

def _make_synthetic_flow(tmp_path, flow_id="evaldemo"):
    flow_dir = tmp_path / "tests" / "flows" / flow_id
    flow_dir.mkdir(parents=True)
    (flow_dir / "test_evaldemo.py").write_text(textwrap.dedent("""
        def test_TC_EVAL_PASS():
            assert True

        def test_TC_EVAL_FAIL():
            assert 1 == 2
    """), encoding="utf-8")
    return str(flow_dir)


def test_run_test_once_pass(tmp_path):
    flow_dir = _make_synthetic_flow(tmp_path)
    ok, summary = ev.run_test_once(flow_dir, "TC-EVAL-PASS", timeout=120)
    assert ok is True
    assert "passed" in summary


def test_run_test_once_fail(tmp_path):
    flow_dir = _make_synthetic_flow(tmp_path)
    ok, _summary = ev.run_test_once(flow_dir, "TC-EVAL-FAIL", timeout=120)
    assert ok is False


def test_run_test_once_no_match_is_not_passed(tmp_path):
    flow_dir = _make_synthetic_flow(tmp_path)
    ok, _summary = ev.run_test_once(flow_dir, "TC-EVAL-NOPE", timeout=120)
    assert ok is False   # rc 5 / "no tests ran" must never read as passed


def test_evaluate_end_to_end(tmp_path):
    _make_synthetic_flow(tmp_path)
    tasks = [{"flow_id": "evaldemo", "tc_id": "TC-EVAL-PASS"},
             {"flow_id": "evaldemo", "tc_id": "TC-EVAL-FAIL"}]
    results = ev.evaluate(str(tmp_path), tasks, runs=2, timeout=120)
    by = {r["tc_id"]: r for r in results}
    assert by["TC-EVAL-PASS"]["verdict"] == "reliable"
    assert by["TC-EVAL-PASS"]["runs"] == [True, True]          # ran the full K
    assert by["TC-EVAL-FAIL"]["verdict"] == "failing"
    assert by["TC-EVAL-FAIL"]["runs"] == [False]              # stopped after first failure


def test_evaluate_missing_fields():
    results = ev.evaluate("/nonexistent", [{"flow_id": "x"}], runs=1)
    assert results[0]["verdict"] == "unknown"
