"""Write recorded steps as a pytest-playwright test, in the layout the IDE uses.

tests/flows/<flow>/
    test_<flow>.py      one @pytest.mark.tc("TC-...") function per test case
    test_data.json      {tc_id: {data_key: value}} -- fill values, editable per test
    test_cases.json     test-case metadata + step list for the IDE

Each step becomes exactly one `flow.<method>(...)` line (see qm_steps.render), run by the
same qm_runtime.Flow that executed it while authoring.
"""
import json
import os
import re

from qm_steps import render

HEADER = '''"""{title} flow."""

import os

import pytest
from playwright.sync_api import Page
from qm_runtime import Flow

# Default file for upload steps.
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")
'''

_STEP_TYPES = {"goto": "navigate", "click": "click", "dblclick": "dblclick", "hover": "hover",
               "fill": "fill", "select": "select", "check": "check", "press": "press", "upload": "upload"}


def func_name(tc_id):
    return "test_" + re.sub(r"\W", "_", tc_id)


def build_function(tc_id, steps, description="", expected=""):
    doc = description.strip() or tc_id
    if expected.strip():
        doc += f"\n\n    Expected result: {expected.strip()}"
    doc = doc.replace('"""', "'''")
    lines = [
        f'@pytest.mark.tc("{tc_id}")',
        f"def {func_name(tc_id)}(page: Page, tc_data, base_url, checkpoints):",
        f'    """{doc}"""',
        "    flow = Flow(page, base_url=base_url, checkpoints=checkpoints)",
    ]
    lines += ["    " + render(step) for step in steps]
    lines.append('    checkpoints.mark_passed("Flow completed - all steps passed")')
    return "\n".join(lines) + "\n"


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=4, ensure_ascii=False)


def _ensure_imports(source):
    if "from qm_runtime import Flow" not in source:
        source = source.replace("import pytest\n", "import pytest\nfrom qm_runtime import Flow\n", 1) \
            if "import pytest\n" in source else "from qm_runtime import Flow\n" + source
    if "TEST_UPLOAD_IMAGE" not in source:
        source += ('\nTEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", '
                   '"fixtures", "test_upload.png")\n')
        if "import os\n" not in source:
            source = "import os\n" + source
    return source


def write_test(tests_root, flow_id, tc_id, steps, data=None, description="", expected=""):
    """Create or replace test `tc_id` in flow `flow_id`. Returns the test file path."""
    flow_dir = os.path.join(tests_root, "flows", flow_id)
    os.makedirs(flow_dir, exist_ok=True)
    for init in (os.path.join(tests_root, "flows", "__init__.py"), os.path.join(flow_dir, "__init__.py")):
        if not os.path.exists(init):
            open(init, "w", encoding="utf-8").close()

    path = os.path.join(flow_dir, f"test_{flow_id}.py")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            source = _ensure_imports(f.read())
    else:
        source = HEADER.format(title=flow_id.replace("_", " ").title())
    func = build_function(tc_id, steps, description, expected)
    pattern = re.compile(r'\n*@pytest\.mark\.tc\("' + re.escape(tc_id) + r'"\)\ndef ' +
                         re.escape(func_name(tc_id)) + r"\(.*?(?=\n\n\n@pytest\.mark|\n@pytest\.mark|\Z)", re.DOTALL)
    if pattern.search(source):
        source = pattern.sub(lambda _m: "\n\n\n" + func.rstrip("\n"), source, count=1)
    else:
        source = source.rstrip("\n") + "\n\n\n" + func
    if not source.endswith("\n"):
        source += "\n"
    compile(source, path, "exec")   # never write a file that doesn't parse
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)

    data_path = os.path.join(flow_dir, "test_data.json")
    all_data = _read_json(data_path, {})
    all_data[tc_id] = dict(data or {})
    _write_json(data_path, all_data)

    cases_path = os.path.join(flow_dir, "test_cases.json")
    cases = [c for c in _read_json(cases_path, []) if c.get("tc_id") != tc_id]
    cases.append({
        "tc_id": tc_id,
        "description": description,
        **({"expected_result": expected} if expected else {}),
        "engine": "qm",
        "steps": [{"id": i + 1, "type": _STEP_TYPES.get(s["op"], "assert"),
                   "targetDescription": s.get("name") or s["op"],
                   "value": "" if s.get("data_key") else str(s.get("value") or ""),
                   "varName": s.get("data_key") or "", "rawLine": render(s)}
                  for i, s in enumerate(steps)],
    })
    _write_json(cases_path, cases)
    return path
