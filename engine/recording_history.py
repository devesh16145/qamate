"""Bounded, session-local recording undo. Never restores live browser/auth state."""
from copy import deepcopy

FIELDS = ("steps", "assertions", "step_id", "input_counter", "assumptions", "skips")


class RecordingHistory:
    def __init__(self, limit=5):
        self.limit = limit
        self.next_id = 1
        self.snapshots = {}

    def save(self, session):
        if not any(getattr(session, name) for name in ("steps", "assertions", "assumptions", "skips")):
            return {"snapshot_id": None, "evicted_snapshot_id": None}
        snapshot_id = f"recording-{self.next_id}"
        state = {name: deepcopy(getattr(session, name)) for name in FIELDS}
        self.next_id += 1
        evicted = None
        if len(self.snapshots) >= self.limit:
            evicted = next(iter(self.snapshots))
            self.snapshots.pop(evicted)
        self.snapshots[snapshot_id] = state
        return {"snapshot_id": snapshot_id, "evicted_snapshot_id": evicted}

    def summaries(self):
        return [{"snapshot_id": key, "steps": len(state["steps"]),
                 "checkpoints": len(state["assertions"]), "assumptions": len(state["assumptions"]),
                 "skips": len(state["skips"])} for key, state in self.snapshots.items()]

    def restore(self, session, snapshot_id):
        if snapshot_id not in self.snapshots:
            return {"ok": False, "error": "Unknown or expired recording snapshot", "snapshots": self.summaries()}
        restored = deepcopy(self.snapshots[snapshot_id])
        saved = self.save(session)  # Preserve the displaced recording before replacing it.
        for name, value in restored.items():
            setattr(session, name, value)
        return {"ok": True, "restored_snapshot_id": snapshot_id, "displaced_recording": saved,
                "note": "Recording restored, not browser state or replay verification. "
                        "Create and replay the test; observe before further browser actions."}


def clear_recording(session, reason=""):
    reason = str(reason).strip()
    if session.assertions and len(reason) < 20:
        return {"ok": False, "status": "recording_protected", "steps": len(session.steps),
                "checkpoints": len(session.assertions), "snapshots": session.recording_history.summaries(),
                "note": "Outcome checkpoints already exist. Create and run the test first. "
                        "Expected validation errors, recovery actions or untidy steps alone are not reasons "
                        "to restart. If this is a genuinely different scenario or a concrete defect, "
                        "supply a specific reason (at least 20 characters); the current recording will be archived."}
    saved = session.recording_history.save(session)
    for name in FIELDS:
        setattr(session, name, 0 if name in {"step_id", "input_counter"} else [])
    return {"ok": True, **saved,
            "note": "Recording cleared with session-local undo (latest five snapshots). "
                    "Browser state is unchanged. Re-drive the complete flow; use restore_recording to undo."}
