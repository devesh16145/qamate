"""Execute generated navigation expressions, without a browser or model."""
import ast
from urllib.parse import urljoin

import pytest
from recorder_parser import generate_from_review


@pytest.mark.parametrize("strategy", [
    {"by": "role", "role": "option", "name": "Acme", "exact": True},
    {"by": "text", "value": "Paid", "exact": True},
    {"by": "label", "value": "Company", "exact": False},
])
def test_recorded_locator_preserves_live_exact_semantics(strategy):
    from agent_recorder import _locator_str
    tree = ast.parse(_locator_str(strategy), mode="eval")
    assert isinstance(tree.body, ast.Call)
    keywords = {item.arg: ast.literal_eval(item.value) for item in tree.body.keywords}
    assert keywords["exact"] is strategy["exact"]


@pytest.mark.parametrize("recorded,base,expected", [
    ("https://marmelab.com/atomic-crm-demo/", "https://marmelab.com/atomic-crm-demo/", "https://marmelab.com/atomic-crm-demo/"),
    ("https://old.test/app/#/companies?q=a", "https://new.test/app/", "https://new.test/app/#/companies?q=a"),
    ("https://old.test/cart.html", "https://new.test/", "https://new.test/cart.html"),
    ("https://old.test/app/?q=a%20b#section", "https://new.test/app/", "https://new.test/app/?q=a%20b#section"),
    ("https://admin.old.test/#/orders", "https://admin.new.test/", "https://admin.new.test/#/orders"),
    ("https://old.test//other.test/path", "https://new.test/app/", "https://new.test/other.test/path"),
])
def test_navigation_preserves_origin_relative_path(tmp_path, recorded, base, expected):
    payload = {"tc_id": "TC-NAV-001", "flowId": "navigation", "description": "Navigation",
               "steps": [{"id": 1, "type": "navigate", "rawLine": f"page.goto({recorded!r})"}],
               "assertions": [], "criteria": []}
    # Replacement must remain valid and keep its import on subsequent exports.
    for _ in range(2):
        result = generate_from_review(payload, str(tmp_path))
        assert result["status"] == "success"
        source = (tmp_path / "tests/flows/navigation/test_navigation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "goto"]
        assert len(calls) == 1
        actual = eval(compile(ast.Expression(calls[0].args[0]), "<navigation>", "eval"),
                      {"urljoin": urljoin, "base_url": base, "admin_url": base})
        assert actual == expected
        assert source.count("from urllib.parse import urljoin\n") == 1
