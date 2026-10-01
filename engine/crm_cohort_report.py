"""Audit an explicit three-author CRM cohort; never select successful runs for it."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def assess(roots):
    reasons, attempts = [], []
    roots = [Path(root).resolve() for root in roots]
    if len(roots) != 3 or len(set(roots)) != 3:
        reasons.append('exactly_three_distinct_authors_required')
    manifests, contracts, sessions = [], [], []
    for root in roots:
        row = {'artifact': str(root), 'passed': False}
        attempts.append(row)
        try:
            score = json.loads((root / 'scorecard.json').read_text(encoding='utf-8'))
            manifest = json.loads((root / 'implementation_manifest.json').read_text(encoding='utf-8'))
            manifests.append(manifest)
            contracts.append(score.get('task_contract'))
            sessions.append(score.get('session_id'))
            row['verdict'] = score.get('verdict')
            conditions = {
                'fresh_author': bool(score.get('session_id') and score.get('created')),
                'full_task': score.get('task_contract', {}).get('name') == 'crm-related-contact',
                'coverage': score.get('outcome_audit', {}).get('passed') is True,
                'reliable': score.get('verdict') == 'reliable',
                'self_verified': score.get('self_verified') is True,
                'two_replays': score.get('independent', {}).get('runs') == [True, True],
                'decision_owned': score.get('decision_loop') is True and score.get('hybrid_exercised') is True
                                  and score.get('hybrid_fallback') is False,
                'source_manifest': bool(manifest.get('agent_recorder.py')) and manifest == score.get('source_sha256'),
            }
            junit = []
            for directory in score.get('independent', {}).get('artifact_dirs', []):
                path = Path(directory).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('Replay outside author artifact')
                reports = list(path.rglob('junit.xml'))
                if len(reports) != 1:
                    raise ValueError('Expected one JUnit per replay')
                cases = ET.parse(reports[0]).findall('.//testcase')
                junit.append(len(cases) == 1 and all(c.find('failure') is None and
                    c.find('error') is None and c.find('skipped') is None for c in cases))
            conditions['junit'] = junit == [True, True]
            files = list((root / 'tests/flows/bench_crm_related_contact').glob('test_*.py'))
            conditions['single_export'] = len(files) == 1
            row['export_sha256_at_audit'] = hashlib.sha256(files[0].read_bytes()).hexdigest() if len(files) == 1 else None
            row['checks'] = conditions
            row['passed'] = all(conditions.values())
        except (OSError, ValueError, ET.ParseError) as exc:
            row['error_type'] = type(exc).__name__
    if not manifests or any(m != manifests[0] for m in manifests):
        reasons.append('source_hashes_differ_or_missing')
    if not contracts or any(c != contracts[0] for c in contracts):
        reasons.append('task_contracts_differ_or_missing')
    if len(set(sessions)) != 3 or not all(sessions):
        reasons.append('three_distinct_sessions_required')
    if not all(row['passed'] for row in attempts):
        reasons.append('author_or_replay_gate_failed')
    return {'passed': not reasons, 'reasons': reasons, 'attempts': attempts,
            'source_sha256': manifests[0] if manifests else {},
            'scope': 'CRM three-author gate only; not full programme acceptance'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs=3)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = assess(args.roots)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'reasons': report['reasons']}))
    raise SystemExit(0 if report['passed'] else 1)
