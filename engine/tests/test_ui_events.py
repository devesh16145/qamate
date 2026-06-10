"""
Unit tests for the agentic-UI engine pieces: thinking-part persistence,
the set_plan/update_plan checklist tools, and the trimmed tool surface.
No browser, no LLM.
"""

import asyncio
import types

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from pydantic_ai.usage import RequestUsage

import agent_chat as ac
import agent_sessions


# ──────────────────────────────────────────────────────────────────────────────
# _add_usage — the token counter must move on BOTH turn endings (the budget-stop
# path used to skip it entirely, keeping the UI counter at 0)
# ──────────────────────────────────────────────────────────────────────────────

def _rt():
    rt = ac.AgentRuntime.__new__(ac.AgentRuntime)
    rt.session_tokens = {"input": 0, "output": 0, "total": 0}
    return rt


def test_add_usage_counts_provider_reported_tokens():
    rt = _rt()
    msgs = [ModelResponse(parts=[TextPart(content="x")], usage=RequestUsage(input_tokens=100, output_tokens=20)),
            ModelResponse(parts=[TextPart(content="y")], usage=RequestUsage(input_tokens=50, output_tokens=5))]
    rt._add_usage(msgs=msgs)
    assert rt.session_tokens["input"] == 150
    assert rt.session_tokens["output"] == 25
    assert rt.session_tokens["total"] == 175
    assert rt.session_tokens["estimated"] is False


def test_add_usage_estimates_when_provider_omits():
    rt = _rt()
    rt._add_usage(msgs=[ModelResponse(parts=[TextPart(content="z" * 400)])])
    assert rt.session_tokens["total"] >= 100      # ~400 chars / 4
    assert rt.session_tokens["estimated"] is True


def test_add_usage_accumulates_across_turns():
    rt = _rt()
    m = [ModelResponse(parts=[TextPart(content="a")], usage=RequestUsage(input_tokens=10, output_tokens=1))]
    rt._add_usage(msgs=m)
    rt._add_usage(msgs=m)
    assert rt.session_tokens["total"] == 22


# ──────────────────────────────────────────────────────────────────────────────
# Thinking parts persist into the transcript as 'thinking' bubbles
# ──────────────────────────────────────────────────────────────────────────────

def test_thinking_part_becomes_transcript_bubble():
    msgs = [ModelResponse(parts=[ThinkingPart(content="let me reason about the page"),
                                 TextPart(content="Here is the answer.")])]
    bubbles = agent_sessions.bubbles_from_messages(msgs)
    roles = [b["role"] for b in bubbles]
    assert roles == ["thinking", "assistant"]
    assert bubbles[0]["text"].startswith("let me reason")


def test_empty_thinking_part_skipped():
    msgs = [ModelResponse(parts=[ThinkingPart(content="   "), TextPart(content="x")])]
    assert [b["role"] for b in agent_sessions.bubbles_from_messages(msgs)] == ["assistant"]


def test_thinking_bubble_truncated():
    msgs = [ModelResponse(parts=[ThinkingPart(content="y" * 9000)])]
    bubbles = agent_sessions.bubbles_from_messages(msgs)
    assert len(bubbles[0]["text"]) == 4000


# ──────────────────────────────────────────────────────────────────────────────
# Tool surface: plan tools present, demoted tools absent
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def agent():
    return ac.build_agent(TestModel())


def _tools(agent):
    return set(agent._function_toolset.tools.keys())


def test_plan_and_mode_tools_registered(agent):
    t = _tools(agent)
    assert {"set_plan", "update_plan", "skip_step", "observe", "select_option",
            "fill", "click", "find_on_screen", "ask_user"} <= t


def test_demoted_tools_not_model_facing(agent):
    t = _tools(agent)
    assert not ({"list_options", "click_option", "click_by_text",
                 "aria_snapshot", "force_click", "inspect_page"} & t)


# ──────────────────────────────────────────────────────────────────────────────
# set_plan / update_plan behavior (direct invocation with a fake ctx)
# ──────────────────────────────────────────────────────────────────────────────

def _ctx():
    return types.SimpleNamespace(deps=types.SimpleNamespace(plan=[]))


def _call(agent, name, ctx, *args, **kw):
    fn = agent._function_toolset.tools[name].function
    return asyncio.run(fn(ctx, *args, **kw))


def test_set_plan_publishes_pending_steps(agent):
    ctx = _ctx()
    res = _call(agent, "set_plan", ctx, ["Login", "Create cart", "Verify summary"])
    assert res["ok"] and res["steps"] == 3
    assert [p["status"] for p in ctx.deps.plan] == ["pending"] * 3
    assert ctx.deps.plan[1]["step"] == "Create cart"


def test_set_plan_rejects_empty(agent):
    res = _call(agent, "set_plan", _ctx(), [])
    assert not res["ok"]


def test_update_plan_full_lifecycle(agent):
    ctx = _ctx()
    _call(agent, "set_plan", ctx, ["a", "b"])
    r1 = _call(agent, "update_plan", ctx, 1, "active")
    assert r1["ok"] and ctx.deps.plan[0]["status"] == "active"
    r2 = _call(agent, "update_plan", ctx, 1, "done", "all good")
    assert ctx.deps.plan[0]["status"] == "done"
    assert ctx.deps.plan[0]["note"] == "all good"


def test_update_plan_validates_input(agent):
    ctx = _ctx()
    assert not _call(agent, "update_plan", ctx, 1, "done")["ok"]      # no plan yet
    _call(agent, "set_plan", ctx, ["only step"])
    assert not _call(agent, "update_plan", ctx, 5, "done")["ok"]      # out of range
    assert not _call(agent, "update_plan", ctx, 1, "bogus")["ok"]     # bad status
    assert ctx.deps.plan[0]["status"] == "pending"                    # untouched


def test_set_plan_replaces_previous(agent):
    ctx = _ctx()
    _call(agent, "set_plan", ctx, ["old 1", "old 2"])
    _call(agent, "set_plan", ctx, ["new"])
    assert len(ctx.deps.plan) == 1 and ctx.deps.plan[0]["step"] == "new"
