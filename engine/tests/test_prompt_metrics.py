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
