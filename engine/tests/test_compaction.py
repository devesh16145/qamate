"""
Unit tests for context compaction (_compact_history): old browser percepts are
shrunk, recent context and message structure (tool_call_id pairing) survive.
This is what keeps long agent sessions from replaying 4M-token histories.
"""

import json

from pydantic_ai.messages import (
    ModelRequest, ModelResponse, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart,
)

import agent_chat as ac


def _turn(i, result_content):
    """One user->toolcall->toolreturn->answer exchange."""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"msg {i}")]),
        ModelResponse(parts=[ToolCallPart(tool_name="observe", args={}, tool_call_id=f"c{i}")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="observe", content=result_content,
                                           tool_call_id=f"c{i}")]),
        ModelResponse(parts=[TextPart(content=f"answer {i}")]),
    ]


def _big(i):
    return {"ok": True, "elements": [{"ref": f"el-{i}-{j}", "name": "x" * 40} for j in range(80)]}


def _history(turns=8):
    msgs = []
    for i in range(turns):
        msgs += _turn(i, _big(i))
    return msgs


def _returns(msgs):
    return [p for m in msgs for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]


def test_old_tool_results_are_compacted():
    msgs = _history(8)            # 32 messages; keep_recent=12 -> first 20 eligible
    out = ac._compact_history(msgs)
    rets = _returns(out)
    old, recent = rets[0], rets[-1]
    assert isinstance(old.content, str) and "compacted" in old.content
    assert len(old.content) < 2000
    assert not isinstance(recent.content, str) or "compacted" not in str(recent.content)


def test_recent_messages_untouched():
    msgs = _history(8)
    out = ac._compact_history(msgs)
    assert out[-12:] == msgs[-12:]            # the recent window is identical objects
    assert len(out) == len(msgs)              # nothing dropped — only shrunk


def test_tool_call_pairing_preserved():
    out = ac._compact_history(_history(8))
    call_ids = {p.tool_call_id for m in out for p in getattr(m, "parts", [])
                if isinstance(p, ToolCallPart)}
    ret_ids = {p.tool_call_id for p in _returns(out)}
    assert call_ids == ret_ids                # no orphaned calls/returns


def test_small_results_left_alone():
    msgs = []
    for i in range(8):
        msgs += _turn(i, {"ok": True})        # tiny results
    out = ac._compact_history(msgs)
    assert all("compacted" not in str(p.content) for p in _returns(out))


def test_short_history_returned_verbatim():
    msgs = _turn(0, _big(0))
    assert ac._compact_history(msgs) == msgs


def test_idempotent():
    once = ac._compact_history(_history(8))
    twice = ac._compact_history(once)
    assert [str(p.content)[:200] for p in _returns(twice)] == \
           [str(p.content)[:200] for p in _returns(once)]


def test_compaction_actually_saves_tokens():
    msgs = _history(8)
    def size(ms):
        total = 0
        for p in _returns(ms):
            c = p.content
            total += len(c) if isinstance(c, str) else len(json.dumps(c, default=str))
        return total
    # 5 of 8 percepts are old enough to compact -> roughly halves the payload
    assert size(ac._compact_history(msgs)) < size(msgs) * 0.55