"""Fixture/controller integrity, not live model authoring acceptance."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from playwright.sync_api import sync_playwright
from agent_chat import _bro
from decision import Decision
from decision_workflow import WorkflowContract, execute_workflow
from integrated_apps import integrated_apps
from multi_app_recording import MultiAppRecording
from project_workflow import ProjectReplay


@pytest.mark.parametrize('mode', ['request', 'return'])
def test_decision_owned_roundtrip_and_auth_isolation(tmp_path, mode):
    with integrated_apps(mode) as (state, urls, provision):
        auth = provision(tmp_path / '.auth')
        project = {'id': 'fixture', 'apps': [
            {'id': app, 'label': app, 'url': url, 'actors': [app], 'requires_auth': True,
             'auth_states': {app: auth[app]}} for app, url in urls.items()]}
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(urls['consumer'])
        assert unauthorized.value.code == 401
        producer_token = json.loads(open(auth['producer']).read())['cookies'][0]['value']
        with pytest.raises(HTTPError) as cross_actor:
            urlopen(Request(urls['consumer'], headers={'Cookie': 'qamate_session=' + producer_token}))
        assert cross_actor.value.code == 401
        values = {'title': 'Fixture transfer', 'amount': '125', 'note': 'Disposable only'} if mode == 'request' else {
            'sku': 'SEED-042', 'quantity': '3', 'reason': 'Damaged packaging'}
        final = 'Approved' if mode == 'request' else 'Return accepted'
        contract = WorkflowContract.model_validate({'goal': 'Create, independently review, then verify propagated outcome',
            'inputs': [{'name': key, 'value': value} for key, value in values.items()], 'milestones': [
                {'app': 'producer', 'actor': 'producer', 'goal': 'Submit request and capture its generated identity',
                 'inputs': list(values), 'captures': ['created'], 'outcomes': [{'value': 'Submitted'}]},
                {'app': 'consumer', 'actor': 'consumer', 'goal': 'Review this same request before processing',
                 'outcomes': [{'value': value, 'record': 'created'} for value in [*values.values(), 'Pending']]},
                {'app': 'consumer', 'actor': 'consumer', 'goal': 'Process the reviewed request',
                 'outcomes': [{'value': final, 'record': 'created'}]},
                {'app': 'producer', 'actor': 'producer', 'goal': 'Verify propagated result for original record',
                 'outcomes': [{'value': final, 'record': 'created'}]}]})
        class FixtureDecider:
            def choose(self, state, choices):
                actions = {k: json.loads(v) for k, v in choices.items()}
                def find(predicate): return next((key for key, action in actions.items() if predicate(action)), None)
                if state['outcomes_ready']:
                    return Decision('check', .99)
                target = state['milestone']['app']
                if state['current_app'] != target:
                    return Decision(find(lambda a: a['kind'] == 'switch_app' and a['app'] == target), .99)
                selected = find(lambda a: a['kind'] == 'fill' and a['slot'] == a['test_id'])
                selected = selected or find(lambda a: a['kind'] == 'capture')
                selected = selected or find(lambda a: a['kind'] == 'click')
                if selected and actions[selected]['kind'] == 'click':
                    if not state['pending_confirmation']:
                        return Decision(selected, .7)
                    assert 'No action was dispatched' in state['pending_confirmation']['reason']
                return Decision(selected or 'wait', .99)
        def start():
            pw = sync_playwright().start()
            browser = pw.chromium.launch(channel='chrome', headless=True)
            return pw, browser, MultiAppRecording(browser, project)
        async def run():
            pw, browser, recorder = await _bro(start)
            async def authorize(ref, value): return value
            try:
                result = await execute_workflow(contract, recorder, _bro, FixtureDecider(), authorize)
                assert result['ok'], json.dumps(result, indent=2)
                from multi_app import Workflow
                workflow = Workflow(bindings=list(recorder.bindings.values()), steps=recorder.steps)
                context_count = len(recorder.contexts)
                def replay():
                    with ProjectReplay(browser, project) as independent:
                        return independent.run(workflow)
                replay_result = await _bro(replay)
                return workflow.model_dump(), context_count, replay_result
            finally:
                await _bro(recorder.close)
                await _bro(browser.close)
                await _bro(pw.stop)
        workflow, context_count, replay_result = asyncio.run(run())
        assert context_count == 2 and replay_result['status'] == 'passed'
        assert len([e for e in state['events'] if e['op'] == 'create']) == 2
        assert len([e for e in state['events'] if e['op'] == 'approve']) == 2
        assert all(not e['id'].startswith('OTHER-') for e in state['events'])
        assert producer_token not in json.dumps(workflow)
