import copy
import pytest
from pydantic import ValidationError
from multi_app import Workflow, origin


def workflow():
    return {"bindings": [{"app": "store", "actor": "seller", "url": "https://store.test/"}],
            "steps": [{"app": "store", "actor": "seller", "op": "open"},
                      {"app": "store", "actor": "seller", "op": "capture", "test_id": "id", "capture": "order"}]}


@pytest.mark.parametrize("url", ["file:///tmp/a", "https://user:pw@example.com", "javascript:alert(1)", "https://example.com:bad"])
def test_reject_bad_origins(url):
    with pytest.raises(ValueError):
        origin(url)


def test_normalize_origin():
    assert origin("https://EXAMPLE.com/x") == origin("https://example.com:443/y")
    assert origin("https://example.com") != origin("http://example.com")


@pytest.mark.parametrize("change", ["actor", "capture", "duplicate", "missing_open", "extra", "empty", "timeout", "same_app_origin"])
def test_reject_invalid_graph(change):
    data = workflow()
    step = data["steps"][-1]
    if change == "actor": step["actor"] = "admin"
    if change == "capture": step["record"] = "unknown"
    if change == "duplicate": data["steps"].append(copy.deepcopy(step))
    if change == "missing_open": data["steps"].pop(0)
    if change == "extra": step["python"] = "anything"
    if change == "empty": step.update(op="expect_text", value=" "); step.pop("capture")
    if change == "timeout": step["timeout_ms"] = 60000
    if change == "same_app_origin": data["bindings"].append({"app": "store", "actor": "other", "url": "https://other.test"})
    with pytest.raises(ValidationError):
        Workflow.model_validate(data)


def test_round_trip():
    data = workflow()
    assert Workflow.model_validate_json(Workflow.model_validate(data).model_dump_json()).steps[-1].capture == "order"
