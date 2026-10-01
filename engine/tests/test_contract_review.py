import pytest
from decision import Decision
from decision_browser import BrowseContract
from contract_review import review_contract


def contract():
    return BrowseContract(start_url="https://app.test/", goal="Search Companies",
        milestones=[{"goal": "Search", "outcomes": [{"name": "Companies", "kind": "page_contains_text", "value": "Companies"}]}])


@pytest.mark.parametrize("choice,confidence,status", [
    ("accept", .99, "contract_review_accepted"), ("accept", .7, "contract_review_accepted"),
    ("invented_0", .99, "contract_review_rejected"), ("incompatible_0", .99, "contract_review_rejected"),
    ("missing", .99, "contract_review_rejected"), ("uncertain", .99, "contract_review_uncertain")])
def test_review_is_bounded_and_uses_original_requirement(choice, confidence, status):
    class Decider:
        def choose(self, state, choices):
            assert state["original_requirement"] == "Original requirement, not rewritten goal"
            assert len(choices) == 5
            return Decision(choice, confidence)
    result = review_contract("Original requirement, not rewritten goal", contract(), Decider())
    assert result["status"] == status and result["dispatched"] is False
    assert result["ok"] == (status == "contract_review_accepted")


@pytest.mark.parametrize("requirement", [None, "", " " * 5, "x" * 24001])
def test_unavailable_context_makes_no_model_call(requirement):
    class Decider:
        def choose(self, *args):
            raise AssertionError("Must not call a provider without usable original context")
    assert review_contract(requirement, contract(), Decider())["status"] == "requirement_context_unavailable"


def test_entry_equality_is_reviewed_separately_and_never_auto_rewritten():
    from decision_browser import Outcome
    task = contract()
    task.milestones[0].outcomes.append(Outcome(name="same site", kind="url_matches_start"))
    calls = []
    class Decider:
        def choose(self, state, choices):
            calls.append(state)
            assert state["registered_entry_url"] == task.start_url
            return Decision("unsupported", .91)
    result = review_contract("Open the created company's detail on this site", task, Decider())
    assert result["status"] == "contract_review_rejected" and not result["dispatched"]
    assert len(calls) == 1 and task.milestones[0].outcomes[-1].kind == "url_matches_start"


@pytest.mark.parametrize("used,rejections,complete,expected", [(True, 0, False, []), (False, 3, False, []), (True, 0, True, ["run_test_case"])])
def test_failed_contract_closes_planner_tools(used, rejections, complete, expected):
    import asyncio
    from types import SimpleNamespace
    from planner_policy import prepare_planner_tools
    deps = SimpleNamespace(config={"agent_execution": {"decision_loop_enabled": True}},
        decision_browser_used=used, decision_contract_rejections=rejections, decision_browser_complete=complete)
    definitions = [SimpleNamespace(name="run_test_case")]
    result = prepare_planner_tools(SimpleNamespace(deps=deps), definitions)
    assert [item.name for item in result] == expected


def test_registered_tool_rejects_before_browser_and_caps_recompilation(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    import agent_chat as ac
    import decision_browser_tools as bridge_tools
    class Decider:
        def choose(self, state, choices):
            return Decision("invented_0", .99)
    monkeypatch.setattr(bridge_tools, "ChoiceDecider", lambda *args, **kwargs: Decider())
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True, decision_loop=True)
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path),
        config={"llm": {"roles": {"decision": "test"}}, "agent_execution": {
            "hybrid_enabled": True, "decision_loop_enabled": True, "decision_contract_review": True}},
        project={"apps": [{"url": "https://app.test/"}]})
    deps.decision_requirement = "Search companies"
    async def run():
        fn = agent._function_toolset.tools["browse_goal"].function
        for remaining in (2, 1, 0):
            result = await fn(SimpleNamespace(deps=deps), contract())
            assert result["status"] == "contract_review_rejected"
            assert result["remaining_compilations"] == remaining and result["dispatched"] is False
            assert not getattr(deps, "decision_browser_used", False)
        assert (await fn(SimpleNamespace(deps=deps), contract()))["status"] == "contract_coverage_retry_exhausted"
    asyncio.run(run())
    assert deps.session.steps == [] and deps.session.assertions == []


def test_entry_grounding_has_no_values_refs_or_cross_origin_content(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    import agent_chat as ac
    async def inline(fn, *args):
        return fn(*args)
    monkeypatch.setattr(ac, "_bro", inline)
    session = ac.BrowserSession()
    session.page = object()
    session._url = lambda: "https://app.test/#/dashboard"
    session.by_ref = {"private-ref": {"name": "Search", "role": "textbox", "tag": "input", "value": "private-value"}}
    session.inspect = lambda *args: {"ok": True, "title": "Demo"}
    agent = ac.build_agent(TestModel(call_tools=[]), hybrid=True, decision_loop=True)
    deps = ac.Deps(session=session, ats_root=str(tmp_path),
        config={"agent_execution": {"decision_loop_enabled": True}},
        project={"apps": [{"url": "https://app.test/", "credentials": {"password": "private-password"}}]})
    async def run():
        fn = agent._function_toolset.tools["get_settings"].function
        result = await fn(SimpleNamespace(deps=deps))
        assert result["entry_observation"]["url"].endswith("#/dashboard")
        assert "private-" not in str(result)
        session._url = lambda: "https://outside.test/"
        assert (await fn(SimpleNamespace(deps=deps)))["entry_observation"] == {"status": "outside_registered_origin"}
    asyncio.run(run())
