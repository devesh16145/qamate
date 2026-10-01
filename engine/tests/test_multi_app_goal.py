import asyncio
import copy
from types import SimpleNamespace

import pytest
from action_guard import GoalHandoffGuard
from decision import Decision
from goal_controller import GoalAction
from multi_app_goal import execute_recording_goal, goal_feedback
from llm import LLMNotConfigured


class Recorder:
    def __init__(self):
        self.decision_disabled = False
        self.recovery_actions = 0
        self.goal_handoffs = GoalHandoffGuard()
        self.tainted = False
        self.calls = []
        self.cleared = 0
        self.state = {"url": "https://local.test", "document": "doc", "state_hash": "empty",
                      "targets": {"ref": {"node": "node"}}, "page": {"app": "producer"}, "values": {}}

    def goal_identity(self, actions):
        return SimpleNamespace(key=("producer", "user")), "key"

    def goal_snapshot(self, binding):
        return copy.deepcopy(self.state)

    def act(self, kind, ref, value, preserve_refs=False):
        assert preserve_refs
        self.calls.append((kind, ref, value))
        self.state["state_hash"] = "changed"
        return {"ok": True}

    def _clear_refs(self):
        self.cleared += 1


async def bro(fn, *args, **kwargs):
    return fn(*args, **kwargs)


@pytest.mark.parametrize("mode,status,calls", [
    ("ok", "actions_completed", 1), ("uncertain", "planner_handoff", 0),
    ("changed", "state_changed", 0), ("approval_changed", "action_failed", 0),
    ("terminal", "decision_error", 0), ("stale", "stale_ref", 0)])
def test_goal_handoffs_and_value_gate(mode, status, calls):
    recorder = Recorder()
    if mode == "stale": recorder.state["targets"] = {}
    class Decider:
        def choose(self, state, criteria):
            assert "private-input" not in str(state) + str(criteria)
            if mode == "terminal": raise LLMNotConfigured("No key")
            if mode == "changed": recorder.state["document"] = "new"
            return Decision("0", .2 if mode == "uncertain" else .99)
    async def authorize(action):
        if mode == "approval_changed": recorder.state["state_hash"] = "changed-during-user-wait"
        return "approved-input"
    result = asyncio.run(execute_recording_goal(recorder, "Fill authorized field", [GoalAction("fill", "ref", "private-input")], bro, Decider(), authorize))
    assert result["status"] == status
    assert len(recorder.calls) == calls
    if calls: assert recorder.calls[0][2] == "approved-input"
    if mode == "terminal": assert recorder.decision_disabled and result["fallback"]
    assert not result.get("verified")


def test_repeated_handoffs_disable_decisions():
    recorder = Recorder()
    class Decider:
        calls = 0
        def choose(self, state, criteria):
            self.calls += 1
            return Decision("stop", .99)
    decider = Decider()
    async def authorize(action): return action.value
    for index in range(3):
        result = asyncio.run(execute_recording_goal(recorder, f"Reworded goal {index}", [GoalAction("click", "ref")], bro, decider, authorize))
    assert decider.calls == 2
    assert result["status"] == "controller_retry_exhausted" and recorder.decision_disabled


@pytest.mark.parametrize("actions", [[], [GoalAction("select", "ref", "x")], [GoalAction("click", "ref")] * 2])
def test_invalid_candidates_never_call_provider(actions):
    async def authorize(action): pytest.fail("No inputs expected")
    result = asyncio.run(execute_recording_goal(Recorder(), "Goal", actions, bro, None, authorize))
    assert result["status"] in {"invalid_goal", "duplicate_action"}


@pytest.mark.parametrize("result,state,completed", [
    ({"status": "planner_handoff", "trace": []}, "not_attempted", 0),
    ({"status": "stale_ref"}, "not_attempted", 0),
    ({"status": "action_failed", "trace": [{"ok": False}]}, "uncertain", 0),
    ({"status": "controller_error"}, "uncertain", 0),
    ({"status": "planner_handoff", "trace": [{"ok": True}]}, "partial", 1),
    ({"status": "actions_completed", "ok": True, "trace": [{"ok": True}]}, "completed", 1),
    ({"status": "no_progress", "trace": [{"ok": True}], "note": "Do not repeat; assert delayed outcome"}, "partial", 1),
])
def test_feedback_distinguishes_no_action_partial_and_uncertain(result, state, completed):
    original = copy.deepcopy(result)
    feedback = goal_feedback(result)
    assert feedback["execution_state"] == state and feedback["completed_actions"] == completed
    assert feedback["attempted_actions"] == (None if state == "uncertain" and not result.get("trace") else len(result.get("trace", [])))
    assert feedback["verified"] is False and result == original
    assert goal_feedback(feedback) == feedback
    if state == "not_attempted": assert "Do not capture" in feedback["note"]
    if result["status"] == "no_progress": assert feedback["note"] == result["note"]


def test_unsupported_fill_is_not_reported_as_stale_or_sent_to_provider():
    from multi_app_recording import UnsupportedFill
    recorder = Recorder()
    def reject(actions): raise UnsupportedFill("Not editable")
    recorder.goal_identity = reject
    async def authorize(action): pytest.fail("Do not authorize invalid fills")
    result = asyncio.run(execute_recording_goal(recorder, "Fill", [GoalAction("fill", "ref", "value")], bro, None, authorize))
    assert result["status"] == "unsupported_fill" and result["attempted_actions"] == 0
    assert "Remove the unsupported fill" in result["note"]
