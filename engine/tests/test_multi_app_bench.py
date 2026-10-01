from copy import deepcopy

import pytest
from agent_bench import score_events
from bench_multi_app import audit_workflow, TASKS, FIELDS, classify_authoring


def workflow():
    return {"bindings": [{"app": app, "actor": "user", "url": f"https://{app}.test/"} for app in ("producer", "consumer")],
            "steps": [
                {"app": "producer", "actor": "user", "op": "open"},
                {"app": "producer", "actor": "user", "op": "click", "test_id": "create"},
                {"app": "producer", "actor": "user", "op": "capture", "test_id": "record", "capture": "order"},
                {"app": "consumer", "actor": "user", "op": "open"},
                {"app": "consumer", "actor": "user", "op": "click", "test_id": "approve", "record": "order"},
                {"app": "producer", "actor": "user", "op": "expect_text", "test_id": "status", "record": "order", "value": "Approved"}]}


def test_expected_cross_app_business_outcome():
    assert audit_workflow(workflow())


@pytest.mark.parametrize("fault", ["no_assertion", "wrong_app", "uncorrelated", "wrong_value", "out_of_order", "no_create"])
def test_rejects_superficially_passing_but_wrong_tests(fault):
    data = deepcopy(workflow())
    if fault == "no_assertion": data["steps"].pop()
    if fault == "wrong_app": data["steps"][-1]["app"] = "consumer"
    if fault == "uncorrelated": data["steps"][-1].pop("record")
    if fault == "wrong_value": data["steps"][-1]["value"] = "Pending"
    if fault == "out_of_order": data["steps"][-2:] = reversed(data["steps"][-2:])
    if fault == "no_create": data["steps"].pop(1)
    assert not audit_workflow(data)


def test_multi_app_export_is_delivery_not_verification():
    result = score_events([{"event": "tool_result", "tool": "multi_app_create_test",
                            "summary": '{"status":"success","verified":false}'}])
    assert result["created"] and not result["self_verified"]


def test_form_audit_requires_all_fields_and_correlated_preapproval_checks():
    data = workflow()
    assert not audit_workflow(data, TASKS["form"])


    data["steps"][1:1] = [{"app": "producer", "actor": "user", "op": "fill", "test_id": key, "value": value} for key, value in FIELDS.items()]
    approval = next(i for i, s in enumerate(data["steps"]) if s.get("test_id") == "approve")
    checks = [{"app": "consumer", "actor": "user", "op": "expect_text", "test_id": "request-" + key,
               "value": value, "record": "order"} for key, value in FIELDS.items()]
    data["steps"][approval:approval] = checks
    assert audit_workflow(data, TASKS["form"])
    assert not audit_workflow(data, TASKS["validation"])
    data["steps"][1:1] = [{"app": "producer", "actor": "user", "op": "click", "test_id": "create"},
                         {"app": "producer", "actor": "user", "op": "expect_text", "test_id": "error", "value": "Name is required"}]
    assert audit_workflow(data, TASKS["validation"])
    checks[0]["record"] = None
    assert not audit_workflow(data, TASKS["form"])


@pytest.mark.parametrize("fault", [None, "completed", "self_verified", "created", "terminal_error"])
def test_replay_reliability_does_not_mask_incomplete_authoring(fault):
    result = {"completed": True, "self_verified": True, "created": True, "outcome_audit": True,
              "role_usage": {"decision": {"requests": 2}},
              "independent_replays": [{"passed": True}, {"passed": True}],
              "negative_controls": [{"passed": False, "summary": "1 failed"}] * 2}
    if fault: result[fault] = fault == "terminal_error"
    verdict = classify_authoring(result, {"controller_fallbacks": 0})
    assert verdict["workflow_reliable"]
    assert verdict["authoring_success"] == (fault is None)
    assert verdict["verdict"] == ("reliable_hybrid" if fault is None else "independent_replay_only")
