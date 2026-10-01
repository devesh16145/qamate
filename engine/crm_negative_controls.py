"""Replay an unchanged generated CRM test against explicit simulated readback faults."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

from agent_eval import run_test_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', required=True, help='Directory beneath results/_public_pilot or results/_decision_probe')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent
    root = (source / args.artifact).resolve()
    allowed = [(source / 'results' / name).resolve() for name in ('_public_pilot', '_decision_probe')]
    if root.parent not in allowed:
        parser.error('Only isolated pilot artifacts are accepted')
    task = json.loads((root / 'task_contract.json').read_text(encoding='utf-8'))
    if task.get('flow_id') != 'bench_crm_related_contact':
        parser.error('Expected related-contact task')
    flow = root / 'tests/flows' / task['flow_id']
    files = list(flow.glob('test_*.py'))
    if len(files) != 1:
        parser.error('Expected one generated test')
    before = hashlib.sha256(files[0].read_bytes()).hexdigest()
    out = source / 'results/_crm_controls' / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    report = {'source_artifact': str(root), 'test_sha256': before,
              'scope': 'Simulated rendered readback faults; not backend storage mutation coverage', 'runs': []}
    modes = {'control': '', 'wrong_saved_values': 'Qamate Test City',
             'failed_persistence': 'Qamate Revised City', 'incorrect_relationship': 'QAMATE Relationship 9f58c327'}
    print('Negative-control artifacts:', out, flush=True)
    for mode, expected in modes.items():
        destination = out / mode
        destination.mkdir()
        evidence = destination / 'injection.json'
        env = {'ATS_ROOT': str(root), 'ATS_PROJECT_ID': 'public-demo', 'ATS_ENV': 'dev',
               'ATS_NO_MANUAL_INPUT': '1', 'ATS_RESULTS_DIR': str(destination),
               'PYTHONPATH': str(source / 'engine'), 'PYTEST_PLUGINS': 'crm_fault_plugin',
               'QAMATE_CRM_FAULT': mode, 'QAMATE_CRM_FAULT_EVIDENCE': str(evidence)}
        passed, summary = run_test_once(str(flow), task['tc_id'], env=env, cwd=str(root), timeout=150)
        failures, errors = [], []
        for junit in destination.rglob('junit.xml'):
            tree = ET.parse(junit)
            failures.extend((f.text or '') for f in tree.findall('.//failure'))
            errors.extend((f.text or '') for f in tree.findall('.//error'))
        injection = json.loads(evidence.read_text()) if evidence.exists() else {}
        intended = (not passed and not errors and injection.get('injections', 0) > 0 and
                    any('AssertionError' in f and expected in f for f in failures))
        row = {'mode': mode, 'test_passed': passed, 'expected_result_observed': passed if mode == 'control' else intended,
               'injection': injection, 'failures': failures, 'errors': errors, 'summary': summary}
        report['runs'].append(row)
        print(json.dumps({k: row[k] for k in ('mode', 'test_passed', 'expected_result_observed')}), flush=True)
    report['generated_test_unchanged'] = before == hashlib.sha256(files[0].read_bytes()).hexdigest()
    report['passed'] = report['generated_test_unchanged'] and all(r['expected_result_observed'] for r in report['runs'])
    (out / 'scorecard.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
