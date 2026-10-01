"""Fresh authoring of authenticated, decision-owned integrated workflows."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import agent_bench
from agent_eval import run_test_once
from integrated_apps import integrated_apps
from model_profiles import resolve_profile
from multi_app import Workflow
from pilot_saucedemo import collect_metrics, new_metrics
import project_store


def task_for(mode):
    fields = {'title': 'Synthetic transfer Cedar', 'amount': '125', 'note': 'Benchmark only'} if mode == 'request' else {
        'sku': 'SEED-042', 'quantity': '3', 'reason': 'Damaged packaging'}
    final = 'Approved' if mode == 'request' else 'Return accepted'
    return {'id': 'INTEGRATED-' + mode.upper(), 'tc_id': 'TC-INTEGRATED-001',
        'flow_id': 'bench_integrated_' + mode, 'app': 'authenticated-integrated', 'difficulty': 'cross-app',
        'required_fields': fields, 'final_status': final, 'contract_version': 2,
        'prompt': f"Test only the two registered disposable integrated apps. Both actors already have isolated authentication configured by the host. "
        f"Create one {'request' if mode == 'request' else 'return'} in producer as actor producer using these exact field values: {json.dumps(fields)}. "
        "Verify the page-wide Submitted confirmation (outside the record row) and capture the actual generated record ID, which changes every run. In consumer as actor consumer, "
        "verify every field value and Pending on that SAME captured record before approving/accepting it. "
        f"Verify {final} on that record in consumer, then verify {final} propagates back to that same record in producer. "
        "Do not operate on the unrelated existing record. The decision provider must own all browsing and app switches via browse_workflow. "
        f"Author TC-INTEGRATED-001 in NEW flow bench_integrated_{mode}, export and run_test_case. "
        "Never hard-code the generated ID, expose auth state, weaken outcomes or retry terminal model errors. "
        "Only synthetic records in these two apps are authorized."}


def audit(data, task):
    try:
        workflow = Workflow.model_validate(data)
    except (ValueError, TypeError):
        return False
    steps = workflow.steps
    for capture_index, capture in enumerate(steps):
        if capture.op != 'capture' or capture.app != 'producer' or capture.actor != 'producer':
            continue
        creates = [i for i, s in enumerate(steps[:capture_index]) if s.op == 'click' and
                   s.app == 'producer' and s.test_id == 'create']
        if len(creates) != 1:
            continue
        create_index = creates[0]
        if not all(any(s.op == 'fill' and s.app == 'producer' and s.test_id == key and s.value == value
                       for s in steps[:create_index]) for key, value in task['required_fields'].items()):
            continue
        approvals = [(i, s) for i, s in enumerate(steps[capture_index+1:], capture_index+1) if
                     s.op == 'click' and s.app == 'consumer' and s.actor == 'consumer' and
                     s.test_id == 'approve' and s.record == capture.capture]
        if len(approvals) != 1:
            continue
        approval_index = approvals[0][0]
        expected = {'request-' + k: v for k, v in task['required_fields'].items()} | {'status': 'Pending'}
        if not all(any(s.op == 'expect_text' and s.app == 'consumer' and s.actor == 'consumer' and
                       s.record == capture.capture and s.test_id == key and s.value == value
                       for s in steps[capture_index+1:approval_index]) for key, value in expected.items()):
            continue
        consumer_done = next((i for i, s in enumerate(steps[approval_index+1:], approval_index+1) if
            s.op == 'expect_text' and s.app == 'consumer' and s.record == capture.capture and
            s.test_id == 'status' and s.value == task['final_status']), None)
        if consumer_done is not None and any(s.op == 'expect_text' and s.app == 'producer' and
            s.actor == 'producer' and s.record == capture.capture and s.test_id == 'status' and
            s.value == task['final_status'] for s in steps[consumer_done+1:]):
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--task', choices=['request', 'return'], required=True)
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent.parent
    saved = json.loads((source / 'config.json').read_text(encoding='utf-8'))
    _, planner = resolve_profile(saved, args.provider)
    profiles = {args.provider: {k: v for k, v in planner.items() if k in {
        'model', 'protocol', 'base_url', 'api_key_env', 'token_parameter', 'supports_temperature', 'capabilities'}}}
    profiles[args.provider].update(max_tokens=16384, timeout=45)
    if not os.environ.get(planner.get('api_key_env', '')) or not os.environ.get('OPENROUTER_API_KEY'):
        parser.error('Configured planner and decision API keys are required')
    profiles['workflow-jev'] = {'model': 'typesafe/jev-1.13', 'protocol': 'openrouter_decisions',
        'base_url': 'https://openrouter.ai/api/alpha', 'api_key_env': 'OPENROUTER_API_KEY', 'timeout': 45}
    root = source / 'results/_integrated_author' / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    (root / 'engine').mkdir(parents=True)
    (root / 'tests').mkdir()
    for filename in ('smart_locator.py', 'config_loader.py', 'project_store.py', 'project_workflow.py', 'multi_app.py'):
        shutil.copy2(source / 'engine' / filename, root / 'engine' / filename)
    shutil.copy2(source / 'tests/conftest.py', root / 'tests/conftest.py')
    (root / 'pytest.ini').write_text('[pytest]\naddopts = --tracing=off --browser-channel=chrome\n')
    config = {'llm': {'default_provider': args.provider, 'providers': profiles, 'roles': {'decision': 'workflow-jev'}},
        'agent_execution': {'hybrid_enabled': True, 'multi_app_enabled': True, 'multi_app_decision_loop_enabled': True},
        'browser_backend': 'native', 'pass_criteria': {'mode': 'all', 'treat_skipped_as': 'fail'},
        'tracing': {'mode': 'off'}, 'video': {'enabled': False}, 'network': {'enabled': False}}
    (root / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source / 'engine').glob('*.py')}
    (root / 'implementation_manifest.json').write_text(json.dumps(hashes, indent=2))
    task = task_for(args.task)
    (root / 'task_contract.json').write_text(json.dumps(task, indent=2))
    metrics = new_metrics()
    def progress(event):
        collect_metrics(metrics, event)
        if event.get('event') in {'tool_call', 'decision_browser_step', 'decision_browser_complete', 'turn_complete'}:
            print(json.dumps({k: event[k] for k in ('event', 'tool', '_ts', 'step', 'kind', 'ok', 'milestone', 'app') if k in event}), flush=True)
    print('Integrated author artifacts:', root, flush=True)
    started = time.monotonic()
    agent_bench.keep_awake(True)
    try:
        with integrated_apps(args.task) as (state, urls, provision):
            auth = provision(root / '.auth')
            project = project_store.create_project(str(root), 'Isolated integrated workflow', '', apps=[
                {'id': app, 'label': app, 'url': url, 'actors': [app], 'requires_auth': True,
                 'auth_states': {app: auth[app]}} for app, url in urls.items()])
            result = agent_bench.run_task(task, str(root), project['id'], 'dev', provider=args.provider,
                tool_budget=24, ready_timeout=60, turn_timeout=600,
                reply='Only the stated synthetic workflow is authorized. No additional information is available.',
                agent_script=str(source / 'engine/agent_chat.py'), events_path=str(root / 'events.jsonl'),
                stderr_path=str(root / 'stderr.log'), on_event=progress)
            flow = Path(project_store.tests_root(str(root), project['id'])) / 'flows' / task['flow_id']
            workflow = flow / 'workflow.json'
            result['outcome_audit'] = workflow.exists() and audit(json.loads(workflow.read_text(encoding='utf-8')), task)
            result['independent_replays'] = []
            if result['outcome_audit'] and not result.get('terminal_error'):
                for index in range(2):
                    env = {'ATS_ROOT': str(root), 'ATS_PROJECT_ID': project['id'], 'ATS_NO_MANUAL_INPUT': '1',
                        'PYTHONPATH': str(root / 'engine'), 'ATS_RESULTS_DIR': str(root / 'independent' / str(index + 1))}
                    passed, summary = run_test_once(str(flow), task['tc_id'], env=env, cwd=str(root), timeout=120)
                    result['independent_replays'].append({'passed': passed, 'summary': summary})
            result['server_events'] = state['events']
            result['verdict'] = 'reliable' if (result.get('completed') and result.get('created') and
                result.get('self_verified') and result['outcome_audit'] and
                len(result['independent_replays']) == 2 and all(r['passed'] for r in result['independent_replays']) and
                (result.get('role_usage') or {}).get('decision', {}).get('requests') and not metrics['controller_fallbacks']) else 'unverified'
        result.update(metrics=metrics, source_sha256=hashes, end_to_end_s=round(time.monotonic()-started, 2))
        (root / 'scorecard.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({k: result.get(k) for k in ('verdict', 'created', 'self_verified', 'outcome_audit', 'independent_replays')}), flush=True)
        return 0 if result['verdict'] == 'reliable' else 1
    finally:
        agent_bench.keep_awake(False)


if __name__ == '__main__':
    raise SystemExit(main())
