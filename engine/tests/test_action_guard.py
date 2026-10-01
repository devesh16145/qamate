from action_guard import ActionGuard
from action_guard import GoalHandoffGuard
from goal_controller import GoalAction


def test_repeated_failures_stop_and_changed_state_has_new_budget():
    guard = ActionGuard()
    key = guard.key("click", "node1", "secret-input", "state1")
    assert "secret-input" not in key
    assert not guard.blocked(key)
    guard.record(key, False)
    assert not guard.blocked(key)
    guard.record(key, False)
    assert guard.blocked(key)
    assert not guard.blocked(guard.key("click", "node1", "secret-input", "state2"))
    guard.record(key, True)
    assert not guard.blocked(key)


def test_goal_handoffs_cannot_retry_with_reworded_or_reordered_candidates():
    guard = GoalHandoffGuard()
    actions = [GoalAction("fill", "name", "private-value"), GoalAction("click", "save")]
    key = guard.key(actions)
    assert guard.key(actions[::-1]) == key and "private-value" not in key
    guard.record("s1", key, False)
    guard.record("s1", key, False)
    assert guard.blocked("s1", key)
    assert not guard.blocked("changed", key)
    guard.record("s1", key, True)
    assert not guard.blocked("s1", key)


def test_goal_handoffs_cap_candidate_variations_on_same_state():
    guard = GoalHandoffGuard()
    for key in ["one", "two", "three", "four"]:
        guard.record("same", key, False)
    assert guard.blocked("same", "new variant")
