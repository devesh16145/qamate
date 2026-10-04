import json
from pydantic_ai.messages import InstructionPart, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
import agent_chat as ac
from prompt_metrics import context_metrics


def test_context_metrics_counts_without_content():
    messages = [ModelRequest(parts=[UserPromptPart(content="private-value")])]
    params = ModelRequestParameters(function_tools=[ToolDefinition(name="observe", description="private-description")],
                                    instruction_parts=[InstructionPart("private-instruction")])
    result = context_metrics(messages, params)
    assert result["message_chars_by_kind"]["user-prompt"] == 13
    assert result["instruction_chars"] == 19
    assert result["total_chars"] == 13 + 19 + result["tool_chars_by_name"]["observe"]
    assert "private" not in json.dumps(result)


def test_live_wrapper_emits_one_context_event_per_request(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(ac, "emit", events.append)
    model = ac.TurnCompactingModel(TestModel(call_tools=[]), compact=True)
    agent = ac.build_agent(model, hybrid=True)
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None,
                   config={"agent_execution": {"hybrid_enabled": True}})
    agent.run_sync("Report ready", deps=deps)
    reports = [e for e in events if e["event"] == "planner_context"]
    assert len(reports) == 1 and reports[0]["total_chars"] > 0
    assert "execute_goal" in reports[0]["tool_chars_by_name"]


def test_live_wrapper_times_each_planner_request(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(ac, "emit", events.append)
    model = ac.TurnCompactingModel(TestModel(call_tools=[]), compact=True)
    agent = ac.build_agent(model, hybrid=True)
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None,
                   config={"agent_execution": {"hybrid_enabled": True}})
    agent.run_sync("Report ready", deps=deps)
    timings = [e for e in events if e["event"] == "model_usage" and e["role"] == "planner"]
    assert len(timings) == 1
    t = timings[0]
    assert t["duration_ms"] >= 0 and t["input"] > 0 and t["output"] > 0
    assert t["tool_calls"] == 0 and t["thinking_chars"] == 0


def test_planner_metrics_count_reasoning(monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
    from pydantic_ai.usage import RequestUsage
    response = ModelResponse(parts=[ThinkingPart(content="x" * 120), TextPart(content="ok")],
                             usage=RequestUsage(input_tokens=900, output_tokens=60, details={"reasoning_tokens": 45}))
    m = ac.planner_request_metrics(response)
    assert (m["input"], m["output"], m["reasoning_tokens"], m["thinking_chars"]) == (900, 60, 45, 120)


def test_bench_totals_planner_time_and_reasoning():
    import agent_bench
    s = agent_bench.score_events([
        {"event": "model_usage", "role": "planner", "input": 1000, "output": 50, "duration_ms": 4000, "reasoning_tokens": 30, "thinking_chars": 400},
        {"event": "model_usage", "role": "planner", "input": 1200, "output": 70, "duration_ms": 6000, "reasoning_tokens": 40},
        {"event": "model_usage", "role": "decision", "input": 500, "output": 5, "duration_ms": 600},
    ])
    planner = s["role_usage"]["planner"]
    assert (planner["requests"], planner["duration_ms"], planner["reasoning_tokens"], planner["thinking_chars"]) == (2, 10000, 70, 400)
