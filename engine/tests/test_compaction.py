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

# -- TurnCompactingModel: within-turn context dieting --------------------------

def test_turn_compacting_model_shrinks_outgoing_only():
    from pydantic_ai.models.test import TestModel
    import agent_chat as ac

    big = "x" * 5000
    msgs = []
    for i in range(10):
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="observe", args={},
                                                      tool_call_id=f"c{i}")]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="observe", content=big,
                                                       tool_call_id=f"c{i}")]))
    model = ac.TurnCompactingModel(TestModel())
    out = model.prepare_messages(list(msgs))
    # old tool returns shrunk, recent (last 12 messages = 6 exchanges) intact
    old_returns = [p for m in out[:8] for p in getattr(m, "parts", [])
                   if isinstance(p, ToolReturnPart)]
    new_returns = [p for m in out[-12:] for p in getattr(m, "parts", [])
                   if isinstance(p, ToolReturnPart)]
    assert old_returns and all(len(str(p.content)) < 1000 for p in old_returns)
    assert new_returns and all(len(str(p.content)) >= 5000 for p in new_returns)
    # the ORIGINAL list is untouched (runner history must never mutate)
    assert all(len(str(p.content)) >= 5000 for m in msgs for p in m.parts
               if isinstance(p, ToolReturnPart))


def test_hybrid_compacts_superseded_percepts_but_keeps_latest():
    from pydantic_ai.models.test import TestModel
    messages = _history(3)
    latest = _returns(messages)[-1].content
    # Latest perception remains intact even after several non-perception messages.
    for i in range(8):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f"note {i}")]))
    output = ac.TurnCompactingModel(TestModel(), compact=True).prepare_messages(messages)
    returns = _returns(output)
    assert returns[0].content["stale_observation"]
    assert returns[-1].content == latest
    assert "elements" in _returns(messages)[0].content
    assert [p.tool_call_id for p in returns] == [p.tool_call_id for p in _returns(messages)]


def test_compaction_preserves_goal_failure_and_checkpoint_evidence():
    original = {"ok": False, "status": "checkpoint_failed", "checkpoints": [{"ok": False}],
                "trace": [{"kind": "click", "ref": "a", "ok": True}], "observation": {"elements": ["old"]}}
    messages = [ModelRequest(parts=[ToolReturnPart(tool_name="execute_goal", content=original, tool_call_id="a")]),
                ModelRequest(parts=[ToolReturnPart(tool_name="observe", content={"elements": ["new"]}, tool_call_id="b")])]
    output = ac._compact_percepts(messages)
    summary = output[0].parts[0].content
    assert summary["status"] == "checkpoint_failed" and not summary["checkpoints"][0]["ok"]
    assert "observation" not in summary and "observation" in original


def test_multi_app_compaction_keeps_current_refs_and_capture_evidence():
    from pydantic_ai.models.test import TestModel
    old = {"app": "producer", "actor": "user", "targets": [{"ref": "old"}]}
    latest = {"app": "consumer", "actor": "reviewer", "targets": [{"ref": "new"}] * 100}
    evidence = {"ok": True, "capture": "record_id", "recorded_steps": 3}
    messages = [ModelRequest(parts=[ToolReturnPart(tool_name=name, content=data, tool_call_id=str(i))])
                for i, (name, data) in enumerate([
                    ("multi_app_observe", old), ("multi_app_act", evidence), ("multi_app_observe", latest)])]
    messages += [ModelRequest(parts=[UserPromptPart(content="Continue")])] * 8
    output = ac.TurnCompactingModel(TestModel(), compact=True).prepare_messages(messages)
    returns = _returns(output)
    assert returns[0].content["stale_observation"] and "targets" not in returns[0].content
    assert returns[0].content["app"] == "producer"
    assert returns[1].content == evidence and returns[2].content == latest
    assert _returns(messages)[0].content == old


def test_obsolete_plans_compact_without_losing_failures_latest_or_pairing():
    from pydantic_ai.models.test import TestModel
    contents = [
        {"ok": True, "plan": ["1. [active] " + "old" * 200]},
        {"ok": True, "plan": ["1. [failed] " + "blocker" * 200]},
        {"ok": False, "error": "important error" * 100},
        {"ok": True, "plan": ["1. [skipped] " + "skip" * 200]},
        {"ok": True, "plan": ["1. [done] " + "latest" * 200]},
    ]
    messages = []
    for i, content in enumerate(contents):
        messages.extend([
            ModelResponse(parts=[ToolCallPart(tool_name="update_plan", args={}, tool_call_id=str(i))]),
            ModelRequest(parts=[ToolReturnPart(tool_name="update_plan", content=content, tool_call_id=str(i))])])
    messages += [ModelRequest(parts=[UserPromptPart(content="Continue")])] * 8
    model = ac.TurnCompactingModel(TestModel(), compact=True)
    output = model.prepare_messages(messages)
    assert _returns(output)[0].content == {"ok": True, "superseded_plan": True}
    assert [p.content for p in _returns(output)[1:]] == contents[1:]
    assert [p.tool_call_id for p in _returns(output)] == [str(i) for i in range(5)]
    assert [p.content for p in _returns(messages)] == contents
    assert model.prepare_messages(output) == output
    assert len(str(output)) < len(str(messages))


def test_new_plan_invalidates_prior_successful_snapshot():
    messages = [ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=str(i))])
                for i, (name, content) in enumerate([
                    ("update_plan", {"ok": True, "plan": ["1. [done] Old task"]}),
                    ("set_plan", {"ok": True, "steps": 2, "note": "New plan"})])]
    output = ac._compact_plans(messages)
    assert _returns(output)[0].content.get("superseded_plan")
    assert _returns(output)[1].content == _returns(messages)[1].content


def test_active_prompt_replacement_is_scoped_and_preserves_custom_instructions():
    from pydantic_ai.messages import SystemPromptPart
    from pydantic_ai.models.test import TestModel
    from planner_policy import HYBRID_SYSTEM, MULTI_APP_SYSTEM
    messages = [ModelRequest(parts=[SystemPromptPart(HYBRID_SYSTEM), SystemPromptPart("Custom safety rule")])]
    model = ac.TurnCompactingModel(TestModel(), compact=True, multi_app_active=lambda: True)
    output = model.prepare_messages(messages)
    assert output[0].parts[0].content == MULTI_APP_SYSTEM
    assert output[0].parts[1].content == "Custom safety rule"
    assert messages[0].parts[0].content == HYBRID_SYSTEM
    assert len(MULTI_APP_SYSTEM) < len(HYBRID_SYSTEM) * .65
    model.multi_app_active = lambda: False
    assert model.prepare_messages(messages)[0].parts[0].content == HYBRID_SYSTEM


def test_combined_outcomes_keep_latest_refs_and_stale_execution_evidence():
    from pydantic_ai.models.test import TestModel
    old = {"ok": False, "execution_state": "uncertain", "tainted": True, "note": "important " * 100,
           "trace": [{"ok": False}], "observation": {"targets": [{"ref": "old"}]}}
    latest = {"ok": True, "op": "capture", "capture": "order", "observation": {"targets": [{"ref": "fresh"}]}}
    messages = [ModelRequest(parts=[ToolReturnPart(tool_name=name, content=data, tool_call_id=str(i))])
                for i, (name, data) in enumerate([("multi_app_execute_goal", old), ("multi_app_act", latest)])]
    messages += [ModelRequest(parts=[UserPromptPart(content="Continue")])] * 8
    output = ac.TurnCompactingModel(TestModel(), compact=True).prepare_messages(messages)
    assert _returns(output)[1].content == latest
    summary = _returns(output)[0].content
    assert "observation" not in summary and summary["tainted"] and summary["trace"] == old["trace"]
    assert summary["note"] == old["note"]
