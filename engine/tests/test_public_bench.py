import json

import public_bench
from public_bench_tasks import TASKS, get_task


def test_schedule_alternates_paired_order():
    rows = list(public_bench.schedule(["smoke", "cart-edit"], 2))
    assert len(rows) == 8
    assert [r["arm"] for r in rows] == ["regular", "hybrid", "hybrid", "regular",
                                        "hybrid", "regular", "regular", "hybrid"]
    for start in range(0, len(rows), 2):
        assert rows[start]["task"] == rows[start + 1]["task"]
        assert rows[start]["repeat"] == rows[start + 1]["repeat"]


def test_task_contracts_are_stable_and_copies():
    for name in TASKS:
        a = get_task(name)
        b = get_task(name)
        assert a == b and len(a["contract_sha256"]) == 64
        a["prompt"] = "changed"
        assert get_task(name) == b
        assert b["flow_id"].startswith("bench_")
        assert "Do not checkout" in b["prompt"] or "Do not use invalid passwords, checkout" in b["prompt"]


def test_summary_retains_failure_usage_and_does_not_credit_hybrid_bypass():
    rows = [
        {"arm": "regular", "verdict": "reliable", "tokens": {"total": 100}, "end_to_end_s": 10},
        {"arm": "regular", "verdict": "failed", "tokens": {"total": 30}},
        {"arm": "hybrid", "verdict": "reliable", "hybrid_exercised": False, "tokens": {"total": 1}},
        {"arm": "hybrid", "verdict": "reliable", "hybrid_exercised": True,
         "tokens": {"total": 40}, "role_usage": {"decision": {"input": 10, "output": 2}}, "end_to_end_s": 8},
        {"arm": "hybrid", "verdict": "failed", "tokens": {"total": 999}, "usage_incomplete": True},
    ]
    summary = public_bench.summarize(rows)
    assert summary["regular"]["attempts"] == 2
    assert summary["regular"]["known_total_tokens_all_attempts"] == 130
    assert summary["hybrid"]["reliable"] == 1
    assert summary["hybrid"]["known_total_tokens_all_attempts"] == 53
    assert summary["hybrid"]["usage_known_attempts"] == 2


def test_suite_stops_and_persists_terminal_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(public_bench, "__file__", str(tmp_path / "engine" / "public_bench.py"))
    called = []
    def run(args, result_sink):
        called.append(args)
        result_sink({"verdict": "failed", "terminal_error": "provider_error"})
        return 1
    monkeypatch.setattr(public_bench.pilot_saucedemo, "main", run)
    assert public_bench.main(["--provider", "test", "--decision", "test-choice", "--tasks", "smoke"]) == 1
    assert len(called) == 1
    score = json.loads(next(tmp_path.glob("results/_public_bench/*/scorecard.json")).read_text())
    assert score["planned_attempts"] == 2 and len(score["attempts"]) == 1
    assert score["summary"]["regular"]["reliable"] == 0


def test_fallback_completion_is_not_pure_hybrid_success():
    summary = public_bench.summarize([{"arm": "hybrid", "verdict": "reliable",
        "hybrid_exercised": True, "hybrid_fallback": True, "end_to_end_s": 5}])
    assert summary["hybrid"]["attempts"] == 1
    assert summary["hybrid"]["reliable"] == 0
    assert summary["hybrid"]["median_success_end_to_end_s"] is None


def test_replay_without_required_outcomes_is_not_task_success():
    summary = public_bench.summarize([{"arm": "regular", "verdict": "reliable",
        "outcome_audit": {"passed": False}, "end_to_end_s": 5}])
    assert summary["regular"]["reliable"] == 0


def test_crm_contract_is_read_only_and_has_explicit_target():
    task = get_task("crm-search")
    assert task["base_url"] == "https://marmelab.com/atomic-crm-demo/"
    assert "read-only" in task["prompt"]
    assert "QAMATE-NOMATCH-9f58c327" in task["prompt"]
    assert "do not hard-code" in task["prompt"]


def test_crm_green_replay_requires_assessed_outcomes(tmp_path):
    from pilot_saucedemo import apply_outcome_gate
    (tmp_path / "test_crm.py").write_text('def test_TC_CRM_001():\n    pass\n')
    result = {"verdict": "reliable"}
    apply_outcome_gate(result, tmp_path, get_task("crm-search"))
    assert result["replay_verdict"] == "reliable"
    assert result["verdict"] == "coverage_unverified"
    assert result["outcome_audit"]["passed"] is False


def test_outcome_gate_does_not_upgrade_failed_crm_replay(tmp_path):
    from pilot_saucedemo import apply_outcome_gate
    result = {"verdict": "timeout"}
    apply_outcome_gate(result, tmp_path, get_task("crm-search"))
    assert result["verdict"] == "timeout"
