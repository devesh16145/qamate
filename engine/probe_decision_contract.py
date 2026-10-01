"""Paid controller diagnostic with a previously compiled, unchanged contract.

Uses the production browse_goal bridge and recorder. This removes compiler
variance during debugging; it is NOT a fresh end-to-end authoring benchmark.
Only accepts an isolated public pilot's config/project, never saved user settings.
"""
import argparse
import asyncio
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
import uuid

import agent_chat as ac
import agent_bench
from decision_browser import BrowseContract
from decision_browser_tools import register_decision_browser
from public_outcome_audit import audit_flow


async def run(args):
    source = Path(__file__).resolve().parent.parent
    old = (source / 'results/_public_pilot' / args.run).resolve()
    if old.parent != (source / 'results/_public_pilot').resolve():
        raise ValueError('Expected an isolated public pilot directory name')
    events = [json.loads(line) for line in (old / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    contract = BrowseContract.model_validate(next(e['contract'] for e in events if e.get('event') == 'decision_browser_contract'))
    config = json.loads((old / 'config.json').read_text(encoding='utf-8'))
    project = json.loads((old / 'projects/public-demo/project.json').read_text(encoding='utf-8'))
    if project.get('auth') != {'type': 'none'} or contract.start_url != 'https://marmelab.com/atomic-crm-demo/':
        raise ValueError('Diagnostic is confined to the disposable public CRM demo')
    root = source / 'results/_decision_probe' / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    shutil.copytree(old / 'engine', root / 'engine')
    (root / 'tests/flows').mkdir(parents=True)
    shutil.copy2(source / 'tests/conftest.py', root / 'tests/conftest.py')
    shutil.copy2(old / 'pytest.ini', root / 'pytest.ini')
    (root / 'projects/public-demo').mkdir(parents=True)
    for path, data in [('config.json', config), ('projects/public-demo/project.json', project),
                       ('contract.json', contract.model_dump())]:
        (root / path).write_text(json.dumps(data, indent=2), encoding='utf-8')
    task = json.loads((old / 'task_contract.json').read_text(encoding='utf-8'))
    (root / 'task_contract.json').write_text(json.dumps(task, indent=2), encoding='utf-8')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source / 'engine').glob('*.py')}
    (root / 'implementation_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Controller diagnostic artifacts:', root, flush=True)
    def emit(event):
        with (root / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
        if event.get('event') in {'decision_browser_step', 'decision_browser_decision', 'decision_browser_complete'}:
            print(json.dumps(event), flush=True)
    class Tools:
        def tool(self, function):
            self.browse_goal = function
            return function
    tools = Tools()
    register_decision_browser(tools, ac.Deps, ac._bro, ac._value_gate, emit)
    session = ac.BrowserSession()
    deps = ac.Deps(session=session, ats_root=str(root), config=config, project=project)
    started = time.monotonic()
    result = {}
    agent_bench.keep_awake(True)
    try:
        await ac._bro(session.start)
        result = await tools.browse_goal(SimpleNamespace(deps=deps), contract)
        if result.get('ok'):
            result['export'] = await ac._bro(session.create_test, str(root), project,
                task['tc_id'], task['flow_id'], 'Frozen-contract complex CRM diagnostic')
        (root / 'recording.json').write_text(json.dumps({'steps': session.steps, 'assertions': session.assertions}, indent=2), encoding='utf-8')
        from decision_browser_tools import BrowserBridge
        final_observation = await ac._bro(BrowserBridge(session, contract, 'session_local_forms').snapshot)
        (root / 'final-observation.json').write_text(json.dumps(final_observation, indent=2), encoding='utf-8')
        await ac._bro(session.page.screenshot, path=str(root / 'final-page.png'), full_page=True)
    finally:
        await ac._bro(session.close)
        agent_bench.keep_awake(False)
    result['controller_s'] = round(time.monotonic() - started, 2)
    result['mode'] = 'frozen_contract_controller_diagnostic'
    if result.get('export', {}).get('status') == 'success':
        result['audit'] = audit_flow(root / 'tests/flows' / task['flow_id'], task)
        result['independent'] = agent_bench.verify_independent(str(root), str(root / 'tests'), task, 'dev', project['id'], runs=2, timeout=120)
    (root / 'scorecard.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'trace'}), flush=True)
    return 0 if (result.get('ok') and result.get('audit', {}).get('passed') and
                 result.get('independent', {}).get('verdict') == 'reliable') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
