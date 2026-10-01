import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_integrated_cohort as audit_module


def test_cohort_requires_junit_and_each_distinct_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, 'audit', lambda workflow, task: True)
    roots = []
    for index in range(3):
        root = tmp_path / str(index)
        flow = root / 'projects/p/tests/flows/bench_integrated_request'
        flow.mkdir(parents=True)
        (flow / 'workflow.json').write_text('{}')
        manifest = {'runtime.py': 'frozen'}
        (root / 'implementation_manifest.json').write_text(json.dumps(manifest))
        (root / 'task_contract.json').write_text(json.dumps({'id': 'INTEGRATED-REQUEST'}))
        score = {'verdict': 'reliable', 'self_verified': True, 'source_sha256': manifest,
                 'session_id': 'same-second-but-isolated-root',
                 'independent_replays': [{'passed': True}, {'passed': True}],
                 'role_usage': {'decision': {'requests': 12}},
                 'server_events': [{'op': op, 'id': str(i)} for i in range(5) for op in ('create', 'approve')]}
        (root / 'scorecard.json').write_text(json.dumps(score))
        (root / 'events.jsonl').write_text(json.dumps({'event': 'tool_call', 'tool': 'browse_workflow'}))
        for replay in (1, 2):
            folder = root / 'independent' / str(replay) / 'verification-fresh'
            folder.mkdir(parents=True)
            (folder / 'junit.xml').write_text('<testsuite><testcase name="test_TC_INTEGRATED_001"/></testsuite>')
        roots.append(root)
    assert audit_module.assess(roots, 'request')['passed']
    report = roots[0] / 'independent/1/verification-fresh/junit.xml'
    original = report.read_text()
    report.write_text('<testsuite><testcase name="test_TC_INTEGRATED_001"><skipped/></testcase></testsuite>')
    assert not audit_module.assess(roots, 'request')['passed']
    report.write_text(original)
    path = roots[1] / 'scorecard.json'
    score = json.loads(path.read_text())
    score['server_events'].append({'op': 'approve', 'id': '0'})
    path.write_text(json.dumps(score))
    assert not audit_module.assess(roots, 'request')['passed']
    assert not audit_module.assess([roots[0]] * 3, 'request')['passed']
