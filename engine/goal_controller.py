"""Small opt-in inner loop. Existing action tools remain the authority for execution."""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Literal
from llm import LLMNotConfigured


@dataclass
class GoalAction:
    kind: Literal["click", "fill", "select"]
    ref: str
    value: str = ""


def redacted_form_state(values):
    rows = []
    for ref, state in values.items():
        value, checked = state if isinstance(state, (tuple, list)) and len(state) == 2 else (state, False)
        rows.append({"ref": ref, "populated": bool(value), "checked": bool(checked)})
    return rows


async def execute_bounded(goal, actions, observe, execute, decider, *, max_steps=8, timeout=60, min_confidence=0.8, expected_targets=None):
    if not goal.strip() or not 1 <= len(actions) <= 20 or any(action.kind not in {"click", "fill", "select"} for action in actions):
        return {"ok": False, "status": "invalid_goal"}
    deadline = time.monotonic() + timeout
    remaining = dict(enumerate(actions))
    trace = []
    initial = await observe()
    def signature(state):
        return json.dumps(state, sort_keys=True, default=str)
    # Freeze the grounding identity of the planner-authorized candidates.
    identities = expected_targets if expected_targets is not None else {a.ref: initial.get("targets", {}).get(a.ref) for a in actions}
    if any(identity is None for identity in identities.values()):
        return {"ok": False, "status": "stale_ref"}
    if any(initial.get("targets", {}).get(a.ref) != identities.get(a.ref) for a in actions):
        return {"ok": False, "status": "stale_ref"}
    state = initial
    for _ in range(min(max_steps, 20)):
        if time.monotonic() >= deadline:
            return {"ok": False, "status": "budget_exhausted", "trace": trace}
        if state.get("document") != initial.get("document") or state.get("url") != initial.get("url"):
            return {"ok": False, "status": "navigation_handoff", "trace": trace}
        criteria = {"stop": "Stop and return control to the planner; no safe useful action remains"}
        available = {}
        for index, action in remaining.items():
            if state.get("targets", {}).get(action.ref) == identities[action.ref]:
                available[str(index)] = action
                # Input values remain local; decision model selects authorized slots only.
                criteria[str(index)] = f"{action.kind} {action.ref}" + (" using the supplied value" if action.kind != "click" else "")
        if not available:
            return {"ok": False, "status": "stale_ref", "trace": trace}
        try:
            completed = [{key: item[key] for key in ("kind", "ref", "ok")} for item in trace]
            decision = await asyncio.wait_for(asyncio.to_thread(decider.choose,
                {"goal": goal, "page": state.get("page"), "completed": completed,
                 "form_state": redacted_form_state(state.get("values") or {})}, criteria),
                timeout=max(0.01, deadline-time.monotonic()))
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            return {"ok": False, "status": "decision_error", "error": type(exc).__name__, "http_status": status,
                    "terminal": isinstance(exc, LLMNotConfigured) or status in {400, 401, 402, 403, 404, 422}, "trace": trace}
        if decision.choice == "stop" or (decision.confidence is not None and decision.confidence < min_confidence):
            return {"ok": False, "status": "planner_handoff", "trace": trace,
                    "decision": {"choice": decision.choice, "confidence": decision.confidence, "probabilities": decision.probabilities}}
        current = await observe()
        if signature(current) != signature(state):
            return {"ok": False, "status": "state_changed", "trace": trace}
        if decision.choice not in available:
            return {"ok": False, "status": "invalid_decision", "trace": trace}
        if time.monotonic() >= deadline:
            return {"ok": False, "status": "budget_exhausted", "trace": trace}
        action = available[decision.choice]
        result = await execute(action)
        trace.append({"kind": action.kind, "ref": action.ref, "ok": result.get("ok", False), "usage": decision.usage,
                      "confidence": decision.confidence, "probabilities": decision.probabilities})
        if not result.get("ok"):
            return {"ok": False, "status": "action_failed", "trace": trace}
        remaining.pop(int(decision.choice))  # Never retry an equivalent action in this goal.
        after = await observe()
        if signature(after) == signature(state):
            return {"ok": False, "status": "no_progress", "trace": trace}
        state = after
        if not remaining:
            return {"ok": True, "status": "actions_completed", "verified": False, "trace": trace,
                    "note": "Actions executed; goal assertions and independent test replay are still required."}
    return {"ok": False, "status": "budget_exhausted", "trace": trace}
