import json

from pilot_saucedemo import collect_metrics, new_metrics


def test_benchmark_preserves_configured_model_settings_without_mutating_source():
    from pilot_saucedemo import benchmark_profile
    profile = {"protocol": "openai", "model": "replaceable", "api_key_env": "TEST_KEY",
               "api_key": "not-copied", "model_settings": {"extra_body": {"thinking": {"type": "disabled"}}}}
    copied = benchmark_profile(profile)
    assert copied["model_settings"] == profile["model_settings"]
    assert "api_key" not in copied
    copied["model_settings"]["extra_body"]["thinking"]["type"] = "enabled"
    assert profile["model_settings"]["extra_body"]["thinking"]["type"] == "disabled"
    assert benchmark_profile(profile, {"parallel_tool_calls": False})["model_settings"]["parallel_tool_calls"] is False
    import pytest
    with pytest.raises(ValueError):
        benchmark_profile(profile, [])


def test_rejected_calls_are_failures_not_successes():
    metrics = new_metrics()
    errors = [{"type": "list_type", "loc": ["checkpoints"], "msg": "Expected array"}] * 2
    for result in [errors, {"ok": False}, {"ok": True}, [{"name": "ordinary list"}], []]:
        collect_metrics(metrics, {"event": "tool_result", "summary": json.dumps(result)})
    assert metrics["failed_tool_results"] == 2
    assert metrics["validation_failed_calls"] == 1  # Calls, not individual field errors.
    assert metrics["unparsed_tool_results"] == 0


def test_truncated_result_is_unknown_not_a_pass():
    metrics = new_metrics()
    collect_metrics(metrics, {"event": "tool_result", "summary": '{"ok": true, ...'})
    assert metrics["unparsed_tool_results"] == 1
    assert metrics["failed_tool_results"] == 0


def test_goal_event_metrics_preserve_full_result_status():
    metrics = new_metrics()
    collect_metrics(metrics, {"event": "tool_call", "tool": "execute_goal"})
    collect_metrics(metrics, {"event": "goal_execution", "result": {
        "ok": False, "status": "checkpoint_failed", "trace": [{"ok": True}]}})
    collect_metrics(metrics, {"event": "turn_complete", "_ts": 12})
    assert metrics["tool_counts"] == {"execute_goal": 1}
    assert metrics["goal_results"] == [{"status": "checkpoint_failed", "ok": False, "actions": 1}]
    assert metrics["turn_complete_s"] == 12


def test_structured_outcome_survives_summary_truncation():
    metrics = new_metrics()
    collect_metrics(metrics, {"event": "tool_result", "summary": "truncated...",
                             "outcome": {"ok": False, "retry": True, "validation_failed": True}})
    assert metrics["failed_tool_results"] == 1
    assert metrics["validation_failed_calls"] == 1
    assert metrics["unparsed_tool_results"] == 0


def test_controller_fallback_is_counted():
    metrics = new_metrics()
    collect_metrics(metrics, {"event": "controller_fallback", "reason": "repeated_handoff"})
    assert metrics["controller_fallbacks"] == 1
