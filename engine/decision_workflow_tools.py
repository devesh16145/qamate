"""Planner boundary for decision-owned cross-app recording."""
from pydantic_ai import RunContext
from decision import ChoiceDecider
from decision_workflow import WorkflowContract, execute_workflow


def register_decision_workflow(agent, deps_type, bro, value_gate, emit):
    context_type = RunContext[deps_type]

    @agent.tool(sequential=True)
    async def browse_workflow(ctx: context_type, contract: WorkflowContract) -> dict:
        """Execute ONE cross-app outcome contract. Use registered app/actor IDs,
        input slots, named generated-ID captures and ordered exact-text outcomes.
        Outcome.record scopes it to a captured relationship. Do not supply actions,
        refs, test IDs or selectors. The decision provider chooses targets and app
        switches. Host-configured actor auth stays local; never submit credentials.
        Export and independently replay only after all outcomes are observed.
        """
        execution = ctx.deps.config.get('agent_execution') or {}
        if not (execution.get('multi_app_enabled') and execution.get('multi_app_decision_loop_enabled')):
            return {'ok': False, 'status': 'disabled'}
        if getattr(ctx.deps, 'decision_workflow_used', False):
            return {'ok': False, 'status': 'contract_already_attempted'}
        name = ((ctx.deps.config.get('llm') or {}).get('roles') or {}).get('decision')
        if not name:
            return {'ok': False, 'status': 'decision_not_configured'}
        def setup():
            from multi_app_recording import MultiAppRecording
            recorder = MultiAppRecording(ctx.deps.session.browser, ctx.deps.project)
            for milestone in contract.milestones:
                recorder._binding(milestone.app, milestone.actor)
            ctx.deps.session._multi_app_recording = recorder
            return recorder
        try:
            recorder = await bro(setup)
        except ValueError:
            return {'ok': False, 'status': 'invalid_binding', 'dispatched': False}
        ctx.deps.decision_workflow_used = True
        async def authorize(ref, value):
            return await value_gate(ctx, ref, value, 'fill')
        emit({'event': 'decision_workflow_contract', 'contract': contract.model_dump()})
        result = await execute_workflow(contract, recorder, bro,
            ChoiceDecider(ctx.deps.config, name, usage_sink=emit), authorize, emit)
        ctx.deps.decision_workflow_complete = result.get('ok') is True
        emit({'event': 'decision_browser_complete', 'scope': 'multi_app', 'result': result})
        return result
