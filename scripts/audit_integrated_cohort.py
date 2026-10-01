"""Audit explicit author cohorts against exports, server events and retained JUnit."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from bench_decision_workflow import audit


def assess(roots, mode):
    rows, manifests, tasks, sessions = [], [], [], []
    for root in map(Path, roots):
        row = {'artifact': str(root.resolve()), 'passed': False}
        rows.append(row)
        try:
            score = json.loads((root / 'scorecard.json').read_text())
            manifest = json.loads((root / 'implementation_manifest.json').read_text())
            task = json.loads((root / 'task_contract.json').read_text())
            manifests.append(manifest)
            tasks.append(task)
            # Session names are second-resolution and scoped to isolated roots.
            sessions.append((str(root.resolve()), score.get('session_id')))
            files = list((root / 'projects').glob('*/tests/flows/bench_integrated_' + mode + '/workflow.json'))
            if len(files) != 1:
                raise ValueError('Expected one workflow')
            workflow = json.loads(files[0].read_text())
            checks = {
                'verdict': score.get('verdict') == 'reliable',
                'self_verified': score.get('self_verified') is True,
                'required_outcomes': audit(workflow, task),
                'requested_task': task['id'] == 'INTEGRATED-' + mode.upper(),
                'source_manifest': manifest == score.get('source_sha256'),
                'replay_results': [r.get('passed') for r in score.get('independent_replays', [])] == [True, True],
            }
            junit = []
            for index in (1, 2):
                reports = list((root / 'independent' / str(index)).glob('verification-*/junit.xml'))
                cases = ET.parse(reports[0]).findall('.//testcase') if len(reports) == 1 else []
                junit.append(len(cases) == 1 and 'TC_INTEGRATED_001' in cases[0].get('name', '') and
                             not any(cases[0].find(tag) is not None for tag in ('failure', 'error', 'skipped')))
            checks['independent_junit'] = junit == [True, True]
            events = score.get('server_events', [])
            creates = [e['id'] for e in events if e['op'] == 'create']
            approvals = [e['id'] for e in events if e['op'] == 'approve']
            # Live author + two self-verifications + two independent replays.
            checks['five_distinct_roundtrips_no_duplicate_submits'] = (len(creates) == len(set(creates)) == 5 and
                len(approvals) == len(set(approvals)) == 5 and set(creates) == set(approvals) and
                all(not identity.startswith('OTHER-') for identity in creates))
            stream = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
            calls = [e.get('tool') for e in stream if e.get('event') == 'tool_call']
            rejected_unknown = {e.get('tool') for e in stream if e.get('event') == 'tool_result' and
                                str(e.get('summary', '')).startswith('Unknown tool name:') and
                                e.get('outcome', {}).get('ok') is False}
            row['rejected_unknown_tool_attempts'] = sum(tool in rejected_unknown for tool in calls)
            checks['no_planner_browser_fallback'] = all(tool in {
                'multi_app_catalog', 'browse_workflow', 'multi_app_create_test', 'run_test_case',
                'ask_user', 'read_test_file'} or tool in rejected_unknown for tool in calls)
            checks['decision_usage_present'] = (score.get('role_usage') or {}).get('decision', {}).get('requests', 0) > 0
            row.update(checks=checks, passed=all(checks.values()),
                       workflow_sha256=hashlib.sha256(files[0].read_bytes()).hexdigest())
        except (OSError, ValueError, KeyError, ET.ParseError) as exc:
            row['error_type'] = type(exc).__name__
    cohort = len(rows) == 3 and len({str(Path(p).resolve()) for p in roots}) == 3
    cohort &= len(sessions) == 3 and len(set(sessions)) == 3 and all(sid for _, sid in sessions)
    cohort &= len(manifests) == 3 and all(m == manifests[0] for m in manifests)
    cohort &= len(tasks) == 3 and all(t == tasks[0] for t in tasks)
    return {'passed': bool(cohort and all(r['passed'] for r in rows)), 'mode': mode,
            'cohort_consistent': bool(cohort), 'attempts': rows,
            'source_sha256': manifests[0] if manifests else {},
            'scope': 'Explicit integrated cohort only; excludes no earlier failed attempts from development history.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['request', 'return'], required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('roots', nargs=3)
    args = parser.parse_args()
    report = assess(args.roots, args.mode)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'cohort_consistent': report['cohort_consistent'],
                      'authors_passed': sum(r['passed'] for r in report['attempts'])}))
    raise SystemExit(0 if report['passed'] else 1)
