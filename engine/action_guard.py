"""Session-local repeated failure/no-progress guard; never stores plaintext inputs."""
import hashlib
import json


class ActionGuard:
    def __init__(self, limit=2):
        self.limit = limit
        self.attempts = {}

    def key(self, kind, target, value, state):
        return hashlib.sha256(json.dumps([kind, target, value, state], default=str).encode()).hexdigest()

    def blocked(self, key):
        return self.attempts.get(key, 0) >= self.limit

    def record(self, key, progressed):
        if progressed:
            self.attempts.clear()
        else:
            if len(self.attempts) >= 64:
                self.attempts.pop(next(iter(self.attempts)))
            self.attempts[key] = self.attempts.get(key, 0) + 1


class GoalHandoffGuard:
    """Bound repeated uncertainty before it reaches the browser action guard."""
    def __init__(self):
        self.states = {}

    def key(self, actions):
        # Ignore reworded goals and candidate order; never retain input values.
        rows = sorted((a.kind, a.ref, a.value) for a in actions)
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

    def blocked(self, state, key):
        row = self.states.get(state, {})
        return row.get(key, 0) >= 2 or sum(row.values()) >= 4

    def record(self, state, key, progressed):
        if progressed:
            self.states.pop(state, None)
            return
        if state not in self.states and len(self.states) >= 64:
            self.states.pop(next(iter(self.states)))
        row = self.states.setdefault(state, {})
        row[key] = row.get(key, 0) + 1
