import asyncio
from types import SimpleNamespace
import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
import agent_chat as ac
from decision_workflow import WorkflowContract, WorkflowMilestone, options, matched_outcomes, input_readiness
from planner_policy import prepare_planner_tools


def test_planner_cannot_provide_action_refs_or_undefined_relationship():
    milestone = {'app': 'a', 'actor': 'user', 'goal': 'Saved', 'outcomes': [{'value': 'Saved'}]}
    data = {'goal': 'Cross-app', 'milestones': [milestone, milestone], 'actions': []}
    with pytest.raises(ValueError): WorkflowContract.model_validate(data)
    data.pop('actions')
    data['milestones'] = [milestone, {**milestone, 'outcomes': [{'value': 'Approved', 'record': 'missing'}]}]
    with pytest.raises(ValueError): WorkflowContract.model_validate(data)


def test_options_and_outcomes_require_active_actor_and_exact_captured_record():
    milestone = WorkflowMilestone(app='review', actor='manager', goal='Approved',
        outcomes=[{'value': 'Approved', 'record': 'request'}])
    obs = {'app': 'create', 'actor': 'requester', 'targets': [
        {'ref': 'wrong', 'test_id': 'status', 'text': 'Approved', 'record': 'other', 'tag': 'span'}]}
    choices = options(obs, milestone, {}, {}, [('create', 'requester'), ('review', 'manager')])
    assert {a['kind'] for a in choices.values()} == {'stop', 'wait', 'switch_app'}
    assert matched_outcomes(obs, milestone, {'request': {}}) is None
    obs.update(app='review', actor='manager')
    assert matched_outcomes(obs, milestone, {'request': {}}) is None
    obs['targets'][0]['record'] = 'request'
    assert matched_outcomes(obs, milestone, {'request': {}}) == [('wrong', 'Approved')]
    choices = options(obs, milestone, {}, {'request': {}}, [('create', 'requester'), ('review', 'manager')])
    assert {a['kind'] for a in choices.values()} == {'stop', 'wait', 'check'}
    obs['targets'].append({'ref': 'approve', 'test_id': 'approve', 'text': 'Approve', 'record': 'request', 'tag': 'button'})
    assert {a['kind'] for a in options(obs, milestone, {}, {'request': {}}, [('review', 'manager')]).values()} == {'stop', 'wait', 'check'}


def test_new_mode_disables_legacy_actions_at_runtime_and_export_before_outcomes(tmp_path):
    agent = ac.build_agent(TestModel(call_tools=[]), decision_workflow=True)
    deps = ac.Deps(session=ac.BrowserSession(), ats_root=str(tmp_path), project=None, config={
        'agent_execution': {'multi_app_enabled': True, 'multi_app_decision_loop_enabled': True}})
    ctx = SimpleNamespace(deps=deps)
    definitions = [ToolDefinition(name=n) for n in ('browse_goal', 'browse_workflow', 'multi_app_catalog',
        'multi_app_observe', 'multi_app_act', 'multi_app_create_test', 'run_test_case')]
    assert {d.name for d in prepare_planner_tools(ctx, definitions)} == {'browse_workflow', 'multi_app_catalog'}
    async def run():
        tool = lambda n: agent._function_toolset.tools[n].function
        assert (await tool('multi_app_observe')(ctx, 'a', 'b'))['status'] == 'decision_workflow_required'
        assert (await tool('multi_app_act')(ctx, 'click', 'ref'))['status'] == 'decision_workflow_required'
        assert (await tool('multi_app_execute_goal')(ctx, 'Click', []))['status'] == 'decision_workflow_required'
        assert (await tool('multi_app_create_test')(ctx, 'TC-X-001', 'new', 'Incomplete'))['status'] == 'outcomes_incomplete'
    asyncio.run(run())


def test_simultaneous_inputs_cannot_cycle_through_each_others_fields():
    milestone = WorkflowMilestone(app='a', actor='user', goal='Enter two fields',
        inputs=['title', 'amount'], outcomes=[{'value': 'Submitted'}])
    obs = {'app': 'a', 'actor': 'user', 'targets': [
        {'ref': 'one', 'test_id': 'name', 'tag': 'input', 'fillable': True, 'value': 'Request'},
        {'ref': 'two', 'test_id': 'amount', 'tag': 'input', 'fillable': True, 'value': ''}]}
    choices = options(obs, milestone, {'title': 'Request', 'amount': '125'}, {}, [('a', 'user')],
                      {'title': ('name', None)})
    fills = [a for a in choices.values() if a['kind'] == 'fill']
    assert len(fills) == 1 and fills[0]['test_id'] == 'amount' and fills[0]['slot'] == 'amount'
    readiness = input_readiness(obs, milestone, {'title': 'Request', 'amount': '125'}, {'title': ('name', None)})
    assert readiness['matched_slots'] == ['title'] and readiness['pending_slots'] == ['amount']
    obs['targets'][0]['value'] = 'Lost value'
    assert input_readiness(obs, milestone, {'title': 'Request', 'amount': '125'}, {'title': ('name', None)})['matched_slots'] == []
