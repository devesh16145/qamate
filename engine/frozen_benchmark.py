"""Predeclared three-app benchmark. Retains every attempt, including failed authors."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from public_bench_tasks import get_task
from pilot_saucedemo import parse_model_settings

WORKFLOWS = ('crm-related-contact', 'crm-search', 'crm-lifecycle', 'smoke', 'cart-edit',
             'negative-login', 'ops-transfer', 'ops-validation', 'sort-detail', 'ops-allocation')


def abort_cohort(row):
    """A failed compilation ends its task, not unrelated predeclared tasks.

    Authentication/quota/transport/unknown terminal errors still abort. Never
    retry a failed author to substitute a green result for its denominator.
    """
    if row.get('verdict') == 'infrastructure_error' or row.get('infra_error'):
        return True
    if not row.get('terminal_error'):
        return False
    errors = row.get('errors') or []
    local_failures = ('Model token limit', "Tool 'browse_goal' exceeded max retries", 'Exceeded maximum retries')
    return not (errors and all(isinstance(error, str) and error.startswith('UnexpectedModelBehavior:') and
                              any(reason in error for reason in local_failures) for error in errors))


def summarize(rows, planned, full_suite):
    reliable = [r for r in rows if r.get('verdict') == 'reliable' and
                r.get('independent', {}).get('runs') == [True, True] and
                r.get('outcome_audit', {}).get('passed') is True and
                (r['arm'] == 'regular' or r.get('decision_loop') is True and
                 r.get('hybrid_exercised') is True and not r.get('hybrid_fallback'))]
    complete = len(rows) == planned
    return {'completed': len(rows), 'planned': planned, 'reliable': len(reliable),
            'independent_reliable_rate': len(reliable) / planned,
            'passed': bool(full_suite and complete and len(reliable) / planned >= .9),
            'scope': 'Workflow reliability only; excludes performance and multi-app acceptance'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--planner-model-settings', type=parse_model_settings)
    parser.add_argument('--arm', choices=['decision', 'regular'], default='decision')
    parser.add_argument('--tasks', nargs='+', choices=WORKFLOWS, default=list(WORKFLOWS))
    parser.add_argument('--workers', type=int, choices=[1, 2, 3], default=3)
    parser.add_argument('--no-contract-hints', action='store_true')
    parser.add_argument('--compact-decisions', action='store_true')
    parser.add_argument('--review-contract', action='store_true')
    args = parser.parse_args(argv)
    if args.planner_model_settings is not None and not isinstance(args.planner_model_settings, dict):
        parser.error('Planner model settings must be a JSON object')
    if len(set(args.tasks)) != len(args.tasks):
        parser.error('Duplicate tasks prohibited')
    source = Path(__file__).resolve().parent.parent
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = source / 'results/_frozen_bench' / stamp
    root.mkdir(parents=True)
    # Execute an immutable copy so unrelated implementation work cannot alter an
    # in-flight cohort. Only model settings are copied, never project accounts.
    runtime = root / 'runtime'
    (runtime / 'engine/fixtures').mkdir(parents=True)
    (runtime / 'tests').mkdir()
    for path in (source / 'engine').iterdir():
        if path.is_file() and path.suffix in {'.py', '.md'}:
            shutil.copy2(path, runtime / 'engine' / path.name)
    shutil.copy2(source / 'engine/fixtures/operations.html', runtime / 'engine/fixtures/operations.html')
    shutil.copy2(source / 'tests/conftest.py', runtime / 'tests/conftest.py')
    configuration = json.loads((source / 'config.json').read_text(encoding='utf-8'))
    (runtime / 'config.json').write_text(json.dumps({'llm': configuration.get('llm', {})}), encoding='utf-8')
    schedule = [{'task': task, 'repeat': repeat, 'arm': args.arm} for task in args.tasks for repeat in range(1, 4)]
    hashes = {str(p.relative_to(source / 'engine')): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [*(source / 'engine').glob('*.py'), *(source / 'engine').glob('*.md'), source / 'engine/fixtures/operations.html']}
    manifest = {'schedule': schedule, 'tasks': {t: get_task(t) for t in WORKFLOWS}, 'source_sha256': hashes,
                'held_out': [], 'consumed_holdouts': ['sort-detail', 'ops-allocation'], 'provider': args.provider,
                'contract_hints_enabled': not args.no_contract_hints,
                'decision_context_format': 'columns' if args.compact_decisions else 'objects',
                'contract_review_enabled': args.review_contract,
                'planner_settings_override_sha256': hashlib.sha256(json.dumps(args.planner_model_settings, sort_keys=True).encode()).hexdigest(),
                'generalization_scope': 'Known development workflows; not unseen-site evidence',
                'limits': {'author_seconds': 600, 'tool_budget': 48, 'independent_replays': 2},
                'known_application_defects': ['crm-lifecycle: Size persistence/display remains a required failing assertion if defect persists']}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Frozen benchmark artifacts:', root, flush=True)

    def attempt(entry):
        log = root / f"{entry['task']}-{entry['repeat']}.log"
        command = [sys.executable, str(runtime / 'engine/pilot_saucedemo.py'), '--provider', args.provider,
                   '--task', entry['task'], '--timeout', '600', '--tool-budget', '48']
        if args.arm == 'decision':
            command += ['--decision-openrouter', '--decision-loop']
        if args.no_contract_hints:
            command += ['--no-contract-hints']
        if args.compact_decisions:
            command += ['--compact-decisions']
        if args.review_contract:
            command += ['--review-contract']
        if args.planner_model_settings is not None:
            command += ['--planner-model-settings', json.dumps(args.planner_model_settings)]
        try:
            with log.open('w', encoding='utf-8') as stream:
                process = subprocess.run(command, cwd=str(runtime), stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, timeout=1000, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            text = log.read_text(encoding='utf-8')
            line = next((l for l in text.splitlines() if l.startswith('Pilot artifacts: ')), '')
            artifact = Path(line.removeprefix('Pilot artifacts: '))
            if not artifact.resolve().is_relative_to(runtime / 'results/_public_pilot'):
                raise ValueError('Invalid artifact path')
            result = json.loads((artifact / 'scorecard.json').read_text(encoding='utf-8'))
            return {**entry, **result, 'process_exit': process.returncode}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {**entry, 'verdict': 'infrastructure_error', 'error_type': type(exc).__name__}

    rows = []
    source_stable = True
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for start in range(0, len(schedule), args.workers):
            for row in pool.map(attempt, schedule[start:start + args.workers]):
                rows.append(row)
                summary = summarize(rows, len(schedule), set(args.tasks) == set(WORKFLOWS))
                report = {'attempts': rows, 'summary': summary, 'manifest': str(root / 'manifest.json')}
                (root / 'scorecard.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({k: row.get(k) for k in ('task', 'repeat', 'verdict', 'artifact_root')}), flush=True)
            if any(abort_cohort(r) for r in rows):
                print('Stopped on provider/infrastructure failure; unrun attempts are not passes.', flush=True)
                break
            if any(hashlib.sha256((runtime / 'engine' / name).read_bytes()).hexdigest() != digest for name, digest in hashes.items()):
                source_stable = False
                print('Stopped: frozen runtime source changed.', flush=True)
                break
    summary = summarize(rows, len(schedule), set(args.tasks) == set(WORKFLOWS))
    summary['source_stable'] = source_stable
    summary['passed'] &= source_stable
    (root / 'scorecard.json').write_text(json.dumps({'attempts': rows, 'summary': summary,
        'manifest': str(root / 'manifest.json')}, indent=2), encoding='utf-8')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
