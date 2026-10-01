import asyncio
from copy import deepcopy
from types import SimpleNamespace

from pydantic_ai.models.test import TestModel
import agent_chat as ac
from recording_history import FIELDS, RecordingHistory, clear_recording


def populated():
    session = ac.BrowserSession()
    session.steps = [{"id": 4, "rawLine": "private recorded data", "targetDescription": "Fill"}]
    session.assertions = [{"afterStep": 4, "description": "Validation shown", "value": "private expected value"}]
    session.step_id = 4
    session.input_counter = 2
    session.assumptions = [{"value": "private assumption"}]
    session.skips = [{"reason": "user approved skip"}]
    return session


def test_asserted_recording_is_protected_from_unexplained_clear():
    session = populated()
    before = {name: deepcopy(getattr(session, name)) for name in FIELDS}
    for reason in ("", "clean it up"):
        result = clear_recording(session, reason)
        assert result["status"] == "recording_protected"
        assert {name: getattr(session, name) for name in FIELDS} == before
    assert not session.recording_history.snapshots


def test_restore_preserves_full_recording_but_not_browser_state():
    session = populated()
    before = {name: deepcopy(getattr(session, name)) for name in FIELDS}
    browser_marker = object()
    session.page = browser_marker
    result = clear_recording(session, "A genuinely different scenario requested by the user")
    assert result["snapshot_id"] == "recording-1"
    assert not session.steps and session.step_id == session.input_counter == 0
    restored = session.recording_history.restore(session, "recording-1")
    assert restored["ok"]
    assert {name: getattr(session, name) for name in FIELDS} == before
    assert session.page is browser_marker
    session.steps[0]["id"] = 99
    assert session.recording_history.snapshots["recording-1"]["steps"][0]["id"] == 4


def test_restore_archives_displaced_work_and_unknown_id_does_not_clear():
    session = populated()
    session.recording_history.save(session)
    session.steps[0]["id"] = 8
    assert not session.recording_history.restore(session, "unknown")["ok"]
    assert session.steps[0]["id"] == 8
    result = session.recording_history.restore(session, "recording-1")
    assert result["displaced_recording"]["snapshot_id"] == "recording-2"
    assert session.recording_history.snapshots["recording-2"]["steps"][0]["id"] == 8


def test_bounded_retention_reports_eviction_and_summaries_hide_values():
    session = populated()
    session.recording_history = RecordingHistory(limit=2)
    history = session.recording_history
    history.save(session)
    history.save(session)
    result = history.save(session)
    assert result["evicted_snapshot_id"] == "recording-1"
    assert len(history.snapshots) == 2
    assert "private" not in str(history.summaries())


def test_empty_clear_does_not_consume_snapshot_slots():
    session = ac.BrowserSession()
    assert clear_recording(session)["snapshot_id"] is None
    assert not session.recording_history.snapshots


def test_registered_restore_and_clear_tools_use_archive(tmp_path):
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True)
    session = populated()
    ctx = SimpleNamespace(deps=ac.Deps(session=session, ats_root=str(tmp_path), project=None))
    tools = agent._function_toolset.tools
    assert asyncio.run(tools["clear_recording"].function(ctx))["status"] == "recording_protected"
    cleared = asyncio.run(tools["clear_recording"].function(ctx, "Concrete incorrect recording needing a restart"))
    assert asyncio.run(tools["restore_recording"].function(ctx, cleared["snapshot_id"]))["ok"]
    assert session.step_id == 4 and session.input_counter == 2
