from frozen_benchmark import summarize, WORKFLOWS, abort_cohort
from public_bench_tasks import get_task


def reliable():
    return {'arm': 'decision', 'verdict': 'reliable', 'independent': {'runs': [True, True]},
            'outcome_audit': {'passed': True}, 'decision_loop': True, 'hybrid_exercised': True,
            'hybrid_fallback': False}


def test_task_local_failures_do_not_skip_unrelated_tasks_or_hide_provider_failure():
    assert not abort_cohort({'terminal_error': True, 'errors': ['UnexpectedModelBehavior: Model token limit (16384) exceeded']})
    assert not abort_cohort({'terminal_error': True, 'errors': ["UnexpectedModelBehavior: Tool 'browse_goal' exceeded max retries count of 2"]})
    for row in ({'terminal_error': True}, {'terminal_error': True, 'errors': ['ModelHTTPError: status_code: 402']},
                {'verdict': 'infrastructure_error'}, {'infra_error': 'timeout'},
                {'terminal_error': True, 'errors': ['UnexpectedModelBehavior: unknown failure']}):
        assert abort_cohort(row)


def test_suite_threshold_requires_complete_declared_denominator_and_coverage():
    rows = [reliable() for _ in range(27)] + [{'arm': 'decision', 'verdict': 'failing'} for _ in range(3)]
    assert summarize(rows, 30, True)['passed']
    assert not summarize(rows[:-1], 30, True)['passed']
    assert not summarize(rows, 30, False)['passed']
    rows[0]['outcome_audit']['passed'] = False
    assert not summarize(rows, 30, True)['passed']
    assert len(WORKFLOWS) == 10
    assert len({get_task(t)['app'] for t in WORKFLOWS}) == 3
    assert sum(bool(get_task(t).get('held_out')) for t in WORKFLOWS) == 2
