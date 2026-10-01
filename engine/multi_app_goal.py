"""Bounded decision routing over the typed recorder, independent of provider."""
from goal_controller import execute_bounded
from multi_app_recording import UnsupportedFill


def goal_feedback(result):
    """Separate no dispatch from failed/partial dispatch; neither proves an outcome."""
    result = dict(result)
    trace = result.get("trace") or []
    completed = sum(row.get("ok") is True for row in trace)
    uncertain = bool(result.get("tainted") or result.get("status") == "controller_error"
                     or any(row.get("ok") is not True for row in trace))
    result.update(attempted_actions=None if uncertain and not trace else len(trace),
                  completed_actions=completed, verified=False)
    if uncertain:
        result["execution_state"] = "uncertain"
        result["note"] = "An action may have partially executed. Do not blindly repeat it or assume success. Observe actual state; tainted recordings cannot be exported. Independent replay is required."
    elif not trace:
        result["execution_state"] = "not_attempted"
        result["note"] = "No browser action was dispatched by this goal. Do not capture an expected new record or assert its outcome as if it ran. Observe fresh refs and replan within the existing retry budget, or report a blocker. This is not an asynchronous update to wait for."
        if result.get("status") == "unsupported_fill":
            result["note"] = "No action was dispatched. Remove the unsupported fill candidate; refreshing refs will not make an output editable. Capture generated IDs only after creation. Replan using observed control types."
    else:
        result["execution_state"] = "completed" if result.get("ok") else "partial"
        if result.get("status") != "no_progress":
            result["note"] = "Only the successful traced actions executed. Do not repeat them. Observe fresh refs, capture only observed IDs and assert outcomes before independent replay."
    return result


async def execute_recording_goal(recorder, goal, actions, bro, decider, authorize):
    if recorder.decision_disabled:
        return {"ok": False, "status": "decision_disabled", "fallback": True}
    if not goal.strip() or not 1 <= len(actions) <= 8 or any(a.kind not in {"click", "fill"} for a in actions):
        return {"ok": False, "status": "invalid_goal"}
    if len({(a.kind, a.ref) for a in actions}) != len(actions):
        return {"ok": False, "status": "duplicate_action"}
    try:
        binding, key = await bro(recorder.goal_identity, actions)
        initial = await bro(recorder.goal_snapshot, binding)
    except UnsupportedFill as exc:
        return goal_feedback({"ok": False, "status": "unsupported_fill", "detail": str(exc)})
    except Exception:
        return {"ok": False, "status": "stale_ref"}
    # Expected identities come from the original refs, not newly rebound nodes.
    expected = {a.ref: initial["targets"].get(a.ref) for a in actions}
    if any(value is None for value in expected.values()):
        return {"ok": False, "status": "stale_ref"}
    if recorder.goal_handoffs.blocked(initial["state_hash"], key):
        recorder.decision_disabled = True
        return {"ok": False, "status": "controller_retry_exhausted", "fallback": True}

    async def observe():
        return await bro(recorder.goal_snapshot, binding)

    async def perform(action):
        before_approval = await observe()
        value = await authorize(action) if action.kind == "fill" else None
        after_approval = await observe()
        if before_approval != after_approval:
            return {"ok": False, "status": "state_changed_during_approval"}
        try:
            return await bro(recorder.act, action.kind, action.ref, value, preserve_refs=True)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    try:
        result = await execute_bounded(goal, actions, observe, perform, decider,
                                       max_steps=8, timeout=60, expected_targets=expected)
        after = await observe()
        recorder.goal_handoffs.record(initial["state_hash"], key, after["state_hash"] != initial["state_hash"])
        recorder.recovery_actions = 0 if result.get("ok") else 2
        if result.get("terminal"):
            recorder.decision_disabled = True
            result["fallback"] = True
        result["verified"] = False
        result["tainted"] = recorder.tainted
        result["app"], result["actor"] = binding.key
        result["note"] = "Observe fresh refs, capture IDs/assert outcomes, and independently replay before delivery."
        if result.get("status") == "no_progress":
            result["note"] = ("The traced action executed, but no immediate DOM change was observed. "
                              "Observe and assert the expected delayed outcome; do not repeat the mutation "
                              "solely because its update is asynchronous. Independent replay is still required.")
        return goal_feedback(result)
    finally:
        await bro(recorder._clear_refs)
