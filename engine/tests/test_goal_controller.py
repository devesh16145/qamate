import asyncio
import copy
import pytest
from decision import Decision, ChoiceDecider
from goal_controller import GoalAction, execute_bounded
from goal_controller import redacted_form_state
from verification import verified_junit
import decision as decision_module


def test_decision_form_state_is_boolean_only():
    rows = redacted_form_state({"username": ("sensitive", False), "password": (True, False),
                                "empty": ("", False), "remember": ("on", True)})
    assert rows == [{"ref": "username", "populated": True, "checked": False},
                    {"ref": "password", "populated": True, "checked": False},
                    {"ref": "empty", "populated": False, "checked": False},
                    {"ref": "remember", "populated": True, "checked": True}]
    assert "sensitive" not in str(rows)


@pytest.mark.parametrize("child, expected", [("", True), ("<skipped/>", False), ("<failure/>", False), ("<error/>", False)])
def test_junit(tmp_path, child, expected):
    path = tmp_path / "result.xml"
    path.write_text(f'<testsuite><testcase name="test_TC_DEMO_001[a]">{child}</testcase></testsuite>')
    assert verified_junit(path, "TC-DEMO-001") is expected
    assert not verified_junit(path, "TC-DEMO-00")


def test_junit_missing(tmp_path):
    assert not verified_junit(tmp_path / "missing", "TC-A-1")


@pytest.mark.parametrize("mode,status,executed", [("success", "actions_completed", 1), ("no_progress", "no_progress", 1), ("uncertain", "planner_handoff", 0), ("stale", "state_changed", 0), ("error", "decision_error", 0)])
def test_bounded_controller(mode, status, executed):
    state = {"document": "doc", "url": "https://example.org", "targets": {"name": {"node": "one"}}, "page": {}, "values": {"name": ""}}
    calls = []
    class Decider:
        def choose(self, state_arg, candidates):
            assert "stop" in candidates
            if mode == "error":
                raise ValueError("bad provider")
            if mode == "stale":
                state["document"] = "changed"
            return Decision("0", 0.3 if mode == "uncertain" else 0.95)
    async def observe():
        return copy.deepcopy(state)
    async def execute(action):
        calls.append(action)
        if mode != "no_progress":
            state["values"]["name"] = "changed"
        return {"ok": True}
    result = asyncio.run(execute_bounded("Fill name", [GoalAction("fill", "name", "Alice")], observe, execute, Decider()))
    assert result["status"] == status
    assert len(calls) == executed
    assert not result.get("verified")


@pytest.mark.parametrize("protocol,url", [("typesafe", "https://api.typesafe.ai/v1/systemone"),
                                          ("openrouter_decisions", "https://openrouter.ai/api/alpha/decisions")])
def test_jev_contract(monkeypatch, protocol, url):
    monkeypatch.setenv("TEST_JEV_KEY", "fake")
    cfg = {"llm": {"providers": {"decision": {"protocol": protocol, "model": "jev-latest", "api_key_env": "TEST_JEV_KEY"}}}}
    def post(actual_url, headers, payload, **kwargs):
        assert actual_url == url
        assert payload["questions"]["action"]["criteria"] == {"a": "Fill", "stop": "Stop"}
        return {"answers": {"action": {"type": "choice", "choice": "a", "confidence": 0.9, "probabilities": {"a": 0.95, "stop": 0.05}}}, "usage": {"input_tokens": 10}}
    monkeypatch.setattr(decision_module, "_http_post_json", post)
    result = ChoiceDecider(cfg, "decision").choose({}, {"a": "Fill", "stop": "Stop"})
    assert result.choice == "a" and result.confidence == 0.9
    assert result.usage == {"input_tokens": 10}


def test_authorization_uses_planners_original_node():
    async def observe():
        return {"targets": {"name": {"node": "replacement"}}}
    async def execute(action):
        pytest.fail("Stale planner target must never execute")
    result = asyncio.run(execute_bounded("Fill", [GoalAction("fill", "name", "x")], observe, execute, None,
                                        expected_targets={"name": {"node": "original"}}))
    assert result["status"] == "stale_ref"


def test_generic_decision_has_no_fabricated_probability(monkeypatch):
    monkeypatch.setenv("ATS_LLM_MOCK_RESPONSE", '{"choice":"a", "confidence":0.99}')
    events = []
    config = {"llm": {"providers": {"mock": {"protocol": "mock"}}}}
    result = ChoiceDecider(config, "mock", usage_sink=events.append).choose({}, {"a": "Fill", "stop": "Stop"})
    assert result.choice == "a" and result.confidence is None and result.probabilities is None
    assert events[-1]["input"] is None and events[-1]["output"] is None


def test_decision_auth_failure_is_terminal():
    from llm import LLMError
    class Rejected:
        def choose(self, state, candidates):
            raise LLMError("Invalid API key", status_code=401)
    async def observe():
        return {"targets": {"name": {"node": "one"}}}
    async def execute(action):
        pytest.fail("No browser action after authentication failure")
    result = asyncio.run(execute_bounded("Fill", [GoalAction("fill", "name", "x")], observe, execute, Rejected()))
    assert result["terminal"] and result["http_status"] == 401
