"""Predeclare six authors, retain failures, stop on terminal provider errors."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
from audit_integrated_cohort import assess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True)
    args = parser.parse_args()
    root = ROOT / 'results/_integrated_cohort' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'engine').glob('*.py')}
    schedule = [{'task': mode, 'repeat': repeat} for mode in ('request', 'return') for repeat in (1, 2, 3)]
    (root / 'manifest.json').write_text(json.dumps({'schedule': schedule, 'source_sha256': hashes}, indent=2))
    print('Integrated cohort artifacts:', root, flush=True)
    attempts = []
    for entry in schedule:
        if hashes != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'engine').glob('*.py')}:
            print('Stopped: source changed', flush=True)
            break
        log = root / f"{entry['task']}-{entry['repeat']}.log"
        try:
            with log.open('w', encoding='utf-8') as stream:
                subprocess.run([sys.executable, str(ROOT / 'engine/bench_decision_workflow.py'),
                    '--provider', args.provider, '--task', entry['task']], cwd=str(ROOT),
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, timeout=1000,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            line = next(line for line in log.read_text(encoding='utf-8').splitlines()
                        if line.startswith('Integrated author artifacts: '))
            artifact = Path(line.removeprefix('Integrated author artifacts: '))
            if not artifact.resolve().is_relative_to(ROOT / 'results/_integrated_author'):
                raise ValueError('Unexpected artifact location')
            score = json.loads((artifact / 'scorecard.json').read_text(encoding='utf-8'))
            row = {**entry, 'artifact_root': str(artifact), 'verdict': score['verdict'],
                   'terminal': bool(score.get('terminal_error'))}
        except (OSError, ValueError, StopIteration, subprocess.TimeoutExpired) as exc:
            row = {**entry, 'verdict': 'infrastructure_error', 'terminal': True, 'error_type': type(exc).__name__}
        attempts.append(row)
        (root / 'attempts.json').write_text(json.dumps(attempts, indent=2))
        print(json.dumps(row), flush=True)
        if row['terminal']:
            break
    reports = {mode: assess([Path(row['artifact_root']) for row in attempts if row['task'] == mode and row.get('artifact_root')], mode)
               for mode in ('request', 'return')}
    passed = len(attempts) == 6 and all(report['passed'] for report in reports.values())
    (root / 'scorecard.json').write_text(json.dumps({'passed': passed, 'planned': 6,
        'completed': len(attempts), 'reports': reports}, indent=2))
    print(json.dumps({'passed': passed, 'completed': len(attempts), 'planned': 6}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
