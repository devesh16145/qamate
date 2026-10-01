"""Predeclared read-only first-site probe; never used as a 30-attempt gate.

No site-specific engine changes. The evaluator knows the requested modules,
not selectors or action sequences. Failed first attempts remain on disk.
"""
import argparse
from copy import deepcopy
import ast
import json
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import pilot_saucedemo
from public_bench_tasks import TASKS
from public_outcome_audit import _calls, coverage_verdict

QUERY = 'QAMATE-NOMATCH-HOLDOUT-7c31'
TASK = {'id': 'HOLDOUT-CATALOGUE-001', 'tc_id': 'TC-HOLDOUT-001',
    'flow_id': 'bench_unseen_catalogue', 'app': 'public-ecommerce-admin',
    'difficulty': 'unseen-site-search-module-roundtrip', 'target_label': 'Poster shop admin demo',
    'base_url': 'https://marmelab.com/react-admin-demo/',
    'prompt': f'Test the public poster-shop admin demo in the registered project. In Products, search for {QUERY}, '
        'confirm that the query is retained and no products match, then clear it and confirm products return. '
        'Visit Orders and return to Products; verify that the product list is still populated. '
        'Use observed empty-state wording, not invented wording or random product names. '
        'Record and verify TC-HOLDOUT-001 in new flow bench_unseen_catalogue. '
        'This is read-only: do not create, edit, delete, purchase, export, message, log in with private accounts, '
        'or leave the registered origin. Stop if credentials or unsupported operations are required.'}


def audit(source):
    """Require ordered same-field search/clear, populated rows and route roundtrip."""
    try:
        tree = ast.parse(source)
        tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('test_TC_HOLDOUT_001')]
        if len(tests) != 1:
            return False
        rows = []
        for call in _calls(tests[0].body):
            if not isinstance(call.func, ast.Attribute):
                return False
            method, receiver = call.func.attr, call.func.value
            if isinstance(receiver, ast.Call) and ast.unparse(receiver.func) == 'expect' and len(receiver.args) == 1:
                target = ast.unparse(receiver.args[0])
                value = call.args[0] if call.args else None
                if method == 'to_have_value' and isinstance(value, ast.Constant):
                    rows.append(('value', (target, value.value)))
                elif method == 'to_contain_text' and target in {"page.locator('body')", 'page.locator("body")'} and isinstance(value, ast.Constant):
                    if isinstance(value.value, str) and re.fullmatch(r'No (?:[\w -]+ )?(?:found|results|records|matches)[.!]?', value.value, re.I):
                        rows.append(('empty', None))
                elif method == 'to_have_url':
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        rows.append(('url', value.value))
                    elif (isinstance(value, ast.Call) and ast.unparse(value.func) == 'urljoin' and
                          len(value.args) == 2 and ast.unparse(value.args[0]) == 'base_url' and
                          isinstance(value.args[1], ast.Constant)):
                        rows.append(('url', value.args[1].value))
                elif method == 'to_be_visible':
                    expected = 'a[href^="#/products/"]:visible:not([href$="/create"]):not([href$="/new"]):not([href$="/import"])'
                    node = receiver.args[0]
                    if (isinstance(node, ast.Attribute) and node.attr == 'first' and isinstance(node.value, ast.Call)
                            and ast.unparse(node.value.func) == 'page.locator' and len(node.value.args) == 1
                            and isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value == expected):
                        rows.append(('populated', None))
            elif method in {'click', 'fill', 'goto', 'select_option', 'press'}:
                rows.append(('action', None))
            elif method not in {'scroll_into_view_if_needed', 'wait_for_timeout', 'wait_for_load_state', 'mark_passed'}:
                return False
        query = next(i for i, row in enumerate(rows) if row[0] == 'value' and row[1][1] == QUERY)
        field = rows[query][1][0]
        empty = next(i for i in range(query + 1, len(rows)) if rows[i][0] == 'empty')
        if any(r[0] == 'action' for r in rows[query + 1:empty]):
            return False
        cleared = next(i for i in range(empty + 1, len(rows)) if rows[i] == ('value', (field, '')))
        populated = next(i for i in range(cleared + 1, len(rows)) if rows[i][0] == 'populated')
        orders = next(i for i in range(populated + 1, len(rows)) if rows[i][0] == 'url' and rows[i][1].endswith('#/commands'))
        returned = next(i for i in range(orders + 1, len(rows)) if rows[i][0] == 'url' and rows[i][1].endswith('#/products'))
        return any(r[0] == 'populated' for r in rows[returned + 1:])
    except (ValueError, SyntaxError, StopIteration, TypeError, IndexError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--planner-model-settings')
    parser.add_argument('--public-demo-login', action='store_true', help='Version 2 development retry after confirming the official demo accepts synthetic credentials')
    args = parser.parse_args()
    evaluator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    task = deepcopy(TASK)
    if args.public_demo_login:
        # Official demo source: examples/demo/src/authProvider.ts accepts all
        # username/password combinations and only sets localStorage username.
        task.update(app='public-demo', session_local_forms=True, contract_version=2)
        task['prompt'] += (' Public demo authentication is authorized using synthetic username qamate_demo '
                           'and synthetic password qamate_demo_only. This demo accepts arbitrary values locally. '
                           'Record login in the test; this does not authorize any record mutations. '
                           'The earlier first-site attempt remains failed; this is a development retry.')
    TASKS['unseen-catalogue'] = task
    result = []
    command = ['--provider', args.provider, '--task', 'unseen-catalogue', '--decision-openrouter',
               '--decision-loop', '--no-contract-hints', '--compact-decisions', '--review-contract',
               '--timeout', '600', '--tool-budget', '48']
    if args.planner_model_settings:
        command += ['--planner-model-settings', args.planner_model_settings]
    pilot_saucedemo.main(command, result.append)
    score = result[0]
    root = Path(score['artifact_root'])
    files = list((root / 'tests/flows/bench_unseen_catalogue').glob('test_*.py'))
    assessed = len(files) == 1 and audit(files[0].read_text(encoding='utf-8'))
    report = {'scope': 'Development retry after consumed first-site failure' if args.public_demo_login else 'First-site probe; same React-admin framework as CRM, not broad generalization',
              'evaluator_sha256': evaluator_hash,
              'task': task, 'outcome_audit': {'passed': assessed},
              'verdict': coverage_verdict(score['replay_verdict'], {'passed': assessed}),
              'artifact_root': str(root), 'source_sha256': score['source_sha256']}
    (root / 'holdout-assessment.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'holdout_verdict': report['verdict'], 'coverage': assessed}))
    return 0 if report['verdict'] == 'reliable' else 1


if __name__ == '__main__':
    raise SystemExit(main())
