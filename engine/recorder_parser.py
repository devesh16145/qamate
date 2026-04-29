"""
Agrim ATS - Recording Parser
Parses Playwright codegen output into structured steps and generates
test code with user-defined assertions. No coding required from user.
"""
import os
import json
import re
import sys


# ── Selector → Human Description ──

def describe_selector(selector):
    """Convert a CSS selector or Playwright locator into a human-readable description."""
    s = selector.strip()

    loc = re.search(r'page\.locator\((["\'])(.*?)\1\)', s)
    if loc:
        return _describe_css(loc.group(2))

    role = re.search(r'page\.get_by_role\((["\'])(.*?)\1', s)
    if role:
        name = re.search(r'name[=:]\s*(["\'])(.*?)\1', s)
        label = name.group(2) if name else role.group(2)
        return f'{role.group(2).title()} "{label}"'

    txt = re.search(r'page\.get_by_text\((["\'])(.*?)\1', s)
    if txt:
        return f'Text "{txt.group(2)}"'

    ph = re.search(r'page\.get_by_placeholder\((["\'])(.*?)\1', s)
    if ph:
        return f'Input "{ph.group(2)}"'

    lbl = re.search(r'page\.get_by_label\((["\'])(.*?)\1', s)
    if lbl:
        return f'Field "{lbl.group(2)}"'

    return s[:60]


def _describe_css(sel):
    """Describe a CSS selector in human terms."""
    s = sel.lower()

    placeholder = re.search(r'placeholder[*^~]?=["\']?([^"\'\[\]]+)', sel)
    text_content = re.search(r'text[=]["\']?([^"\'\[\]]+)', sel)
    aria_label = re.search(r'aria-label[=]["\']?([^"\'\[\]]+)', sel)
    title_attr = re.search(r'title[=]["\']?([^"\'\[\]]+)', sel)
    role_attr = re.search(r'role[=]["\']?([^"\'\[\]]+)', sel)
    data_test = re.search(r'data-testid[=]["\']?([^"\'\[\]]+)', sel)
    nth_match = re.search(r':nth-child\((\d+)\)', sel)

    desc = ""
    if 'input' in s:
        if placeholder:
            desc = f'Input "{placeholder.group(1)}"'
        elif aria_label:
            desc = f'Input "{aria_label.group(1)}"'
        else:
            desc = "Input field"
    elif 'button' in s:
        if text_content:
            desc = f'Button "{text_content.group(1)}"'
        elif aria_label:
            desc = f'Button "{aria_label.group(1)}"'
        elif title_attr:
            desc = f'Button "{title_attr.group(1)}"'
        else:
            desc = "Button"
    elif 'select' in s:
        if aria_label:
            desc = f'Dropdown "{aria_label.group(1)}"'
        else:
            desc = "Dropdown"
    elif 'textarea' in s:
        desc = "Text area"
    elif 'a[' in s or 'a.' in s:
        if text_content:
            desc = f'Link "{text_content.group(1)}"'
        else:
            desc = "Link"
    elif '[role=' in s and role_attr:
        desc = f'{role_attr.group(1).title()} element'
    elif data_test:
        desc = f'Element "{data_test.group(1)}"'
    else:
        cls = re.search(r'\.([a-zA-Z][\w-]*)', sel)
        id_match = re.search(r'#([a-zA-Z][\w-]*)', sel)
        if id_match:
            desc = f'Element #{id_match.group(1)}'
        elif cls:
            desc = f'Element .{cls.group(1)}'
        else:
            desc = sel[:50]

    if nth_match:
        desc += f' (#{nth_match.group(1)})'
    return desc


def _step_comment(stype, desc, value=""):
    """Generate a human-readable comment for a step."""
    if stype == "navigate":
        return f"Navigate to {desc}"
    elif stype == "fill":
        return f'Enter "{value}" in {desc}'
    elif stype == "type":
        return f'Type "{value}" in {desc}'
    elif stype == "click":
        return f"Click {desc}"
    elif stype == "select":
        return f'Select "{value}" in {desc}'
    elif stype == "press":
        return f'Press "{value}" on {desc}'
    elif stype == "wait":
        return "Wait for page load"
    elif stype == "check":
        return f"Check {desc}"
    elif stype == "dblclick":
        return f"Double-click {desc}"
    elif stype == "hover":
        return f"Hover over {desc}"
    else:
        return desc[:60]


# ── Parse codegen output into structured steps ──

def parse_steps(ats_root):
    """Parse temp_recording.py into structured steps for the review UI."""
    temp_file = os.path.join(ats_root, "temp_recording.py")
    if not os.path.exists(temp_file):
        return {"status": "error", "message": "Recording file not found"}

    with open(temp_file, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'def test_[a-zA-Z0-9_]+\(.*?\)[^:]*:\s*(.*)', content, re.DOTALL)
    if not match:
        return {"status": "error", "message": "Could not parse generated code"}

    body_lines = match.group(1).strip().split('\n')
    steps = []
    input_counter = 1

    for line in body_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        step = {
            "id": len(steps) + 1,
            "rawLine": stripped,
            "type": "other",
            "target": "",
            "targetDescription": "",
            "value": "",
            "varName": "",
        }

        # Navigate
        nav = re.search(r'\.goto\((["\'])(.*?)\1\)', stripped)
        if nav:
            step["type"] = "navigate"
            step["target"] = nav.group(2)
            step["targetDescription"] = f"Navigate to {nav.group(2)}"
            steps.append(step)
            continue

        # Fill
        fill = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.fill\((["\'])(.*?)\2\)', stripped)
        if fill:
            step["type"] = "fill"
            step["target"] = fill.group(1).strip()
            step["value"] = fill.group(3)
            step["varName"] = f"input_{input_counter}"
            step["targetDescription"] = describe_selector(step["target"])
            input_counter += 1
            steps.append(step)
            continue

        # Type
        typ = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.type\((["\'])(.*?)\2', stripped)
        if typ:
            step["type"] = "type"
            step["target"] = typ.group(1).strip()
            step["value"] = typ.group(3)
            step["varName"] = f"input_{input_counter}"
            step["targetDescription"] = describe_selector(step["target"])
            input_counter += 1
            steps.append(step)
            continue

        # Double-click
        dbl = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.dblclick\(\)', stripped)
        if dbl:
            step["type"] = "dblclick"
            step["target"] = dbl.group(1).strip()
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        # Click
        clk = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.click\(\)', stripped)
        if clk:
            step["type"] = "click"
            step["target"] = clk.group(1).strip()
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        # Check
        chk = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.check\(\)', stripped)
        if chk:
            step["type"] = "check"
            step["target"] = chk.group(1).strip()
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        # Select option
        sel = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.select_option\((["\'])(.*?)\2\)', stripped)
        if sel:
            step["type"] = "select"
            step["target"] = sel.group(1).strip()
            step["value"] = sel.group(3)
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        sel_list = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.select_option\(\[(.*?)\]\)', stripped)
        if sel_list:
            step["type"] = "select"
            step["target"] = sel_list.group(1).strip()
            step["value"] = sel_list.group(2).strip().strip("'\"")
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        # Hover
        hov = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.hover\(\)', stripped)
        if hov:
            step["type"] = "hover"
            step["target"] = hov.group(1).strip()
            step["targetDescription"] = describe_selector(step["target"])
            steps.append(step)
            continue

        # Press
        prs = re.search(r'([\w\.\(\)\'\"\[\]=*:,\s>~+]+?)\.press\((["\'])(.*?)\2\)', stripped)
        if prs:
            step["type"] = "press"
            step["target"] = prs.group(1).strip()
            step["value"] = prs.group(3)
            step["targetDescription"] = f'Press "{prs.group(3)}" on {describe_selector(step["target"])}'
            steps.append(step)
            continue

        # Wait for load state
        if 'wait_for_load_state' in stripped:
            step["type"] = "wait"
            state = re.search(r'wait_for_load_state\((["\'])(.*?)\1\)', stripped)
            step["targetDescription"] = f'Wait for page load ({state.group(2) if state else "load"})'
            steps.append(step)
            continue

        # Set viewport size
        if 'set_viewport_size' in stripped:
            step["type"] = "viewport"
            vp = re.search(r'set_viewport_size\((\{.*?\})\)', stripped)
            step["targetDescription"] = f'Set viewport size {vp.group(1) if vp else ""}'
            steps.append(step)
            continue

        # Fallback
        step["targetDescription"] = stripped[:80]
        steps.append(step)

    return {"status": "success", "steps": steps}


# ── Assertion code generation ──

def _generate_assertion_code(assertion):
    """Generate Python assertion code from an assertion definition."""
    atype = assertion.get("type", "")
    selector = assertion.get("selector", "")
    value = assertion.get("value", "")
    timeout = assertion.get("timeout", 10000)

    if atype == "element_visible":
        return f'expect(page.locator("{selector}")).to_be_visible(timeout={timeout})'
    elif atype == "element_not_visible":
        return f'expect(page.locator("{selector}")).not_to_be_visible(timeout={timeout})'
    elif atype == "element_contains_text":
        return f'expect(page.locator("{selector}")).to_contain_text("{value}", timeout={timeout})'
    elif atype == "element_has_text":
        return f'expect(page.locator("{selector}")).to_have_text("{value}", timeout={timeout})'
    elif atype == "element_has_value":
        return f'expect(page.locator("{selector}")).to_have_value("{value}", timeout={timeout})'
    elif atype == "element_enabled":
        return f'expect(page.locator("{selector}")).to_be_enabled(timeout={timeout})'
    elif atype == "element_disabled":
        return f'expect(page.locator("{selector}")).to_be_disabled(timeout={timeout})'
    elif atype == "element_count":
        count = assertion.get("count", 1)
        op = assertion.get("operator", ">=")
        return f'assert page.locator("{selector}").count() {op} {count}'
    elif atype == "page_contains_text":
        return f'page.wait_for_timeout(500)\nassert "{value}" in page.content()'
    elif atype == "page_not_contains_text":
        return f'page.wait_for_timeout(500)\nassert "{value}" not in page.content()'
    elif atype == "url_contains":
        return f'page.wait_for_timeout(500)\nassert "{value}" in page.url'
    elif atype == "url_equals":
        return f'page.wait_for_timeout(500)\nassert page.url == "{value}"'
    elif atype == "url_not_contains":
        return f'page.wait_for_timeout(500)\nassert "{value}" not in page.url'
    elif atype == "title_contains":
        return f'expect(page).to_have_title(re.compile(r".*{value}.*"), timeout={timeout})'
    elif atype == "no_error_toast":
        return (
            f'page.wait_for_timeout(1000)\n'
            f'    error_toasts = page.locator("[class*=error], [class*=danger], [class*=toast-error], [role=alert]")\n'
            f'    assert error_toasts.count() == 0, "Error toast appeared on page"'
        )
    elif atype == "success_toast":
        return (
            f'page.wait_for_timeout(1000)\n'
            f'    success = page.locator("[class*=success], [class*=toast-success]")\n'
            f'    expect(success).to_be_visible(timeout={timeout})'
        )
    elif atype == "toast_contains":
        return (
            f'page.wait_for_timeout(1000)\n'
            f'    toast = page.locator("[class*=toast], [class*=notification], [role=alert]")\n'
            f'    expect(toast.first).to_contain_text("{value}", timeout={timeout})'
        )
    elif atype == "screenshot":
        return 'page.screenshot(full_page=True)'
    elif atype == "wait_ms":
        ms = assertion.get("value", 2000)
        return f'page.wait_for_timeout({ms})'
    return f'# Unknown assertion type: {atype}'


# ── Generate test code from reviewed steps ──

def generate_from_review(payload, ats_root):
    """
    Generate test code from reviewed steps with assertions.
    payload = {
        "tc_id", "description", "flowId", "preconditions", "expectedResult",
        "steps": [...],
        "assertions": [...],
        "criteria": [...],
    }
    """
    tc_id = payload["tc_id"]
    description = payload.get("description", "")
    flow_id = payload.get("flowId", "general")
    preconditions = payload.get("preconditions", "")
    expected_result = payload.get("expectedResult", "")
    steps = payload.get("steps", [])
    assertions = payload.get("assertions", [])
    criteria = payload.get("criteria", [])

    flow_dir = os.path.join(ats_root, "tests", "flows", flow_id)
    os.makedirs(flow_dir, exist_ok=True)

    # Build data dict from steps
    data_dict = {}
    for step in steps:
        if step.get("varName") and step.get("value"):
            data_dict[step["varName"]] = step["value"]

    # Build step->assertions map
    assertions_by_step = {}
    for a in assertions:
        sid = a.get("afterStep", 0)
        assertions_by_step.setdefault(sid, []).append(a)

    # Generate test lines
    test_lines = []
    for step in steps:
        sid = step["id"]
        stype = step.get("type", "other")
        raw = step.get("rawLine", "")
        desc = step.get("targetDescription", "")

        # Replace hardcoded values with tc_data references
        if stype in ("fill", "type") and step.get("varName"):
            var_name = step["varName"]
            if stype == "fill":
                raw = re.sub(
                    r'\.fill\((["\'])(.*?)\1\)',
                    f'.fill(tc_data.get("{var_name}", ""))',
                    raw,
                )
            else:
                raw = re.sub(
                    r'\.type\((["\'])(.*?)\1',
                    f'.type(tc_data.get("{var_name}", "")',
                    raw,
                )

        # Navigate URLs: replace base_url if applicable
        if stype == "navigate":
            raw_url_match = re.search(r'\.goto\((["\'])(.*?)\1\)', raw)
            if raw_url_match:
                full_url = raw_url_match.group(2)
                path_match = re.search(r'https?://[^/]+(/.*)', full_url)
                if path_match:
                    path = path_match.group(1)
                    raw = raw.replace(full_url, f'base_url + "{path}"')

        # Comment
        test_lines.append(f"    # Step {sid}: {_step_comment(stype, desc, step.get('value', ''))}")
        test_lines.append(f"    {raw}")

        # Stability wait after interactive actions
        if stype in ("fill", "type", "click", "select", "dblclick", "check", "press"):
            test_lines.append("    page.wait_for_timeout(1000)")

        # Per-step assertions
        for a in assertions_by_step.get(sid, []):
            a_code = _generate_assertion_code(a)
            a_desc = a.get("description", a.get("type", "assertion"))
            test_lines.append(f"    # Verify: {a_desc}")
            for a_line in a_code.split('\n'):
                test_lines.append(f"    {a_line}")

    # Success/Failure criteria at the end
    if criteria:
        test_lines.append("")
        test_lines.append("    # ── Success / Failure Criteria ──")
        for c in criteria:
            c_code = _generate_assertion_code(c)
            c_desc = c.get("description", c.get("type", "criteria"))
            test_lines.append(f"    # {c_desc}")
            for c_line in c_code.split('\n'):
                test_lines.append(f"    {c_line}")

    # Build the full function
    underscored_id = tc_id.replace("-", "_")
    func_name = f"test_{underscored_id}"

    func_code = f'\n\n@pytest.mark.tc("{tc_id}")\n'
    func_code += f'def {func_name}(page: Page, tc_data, base_url):\n'
    docstring = f'    """{description}'
    if preconditions:
        docstring += f'\n\n    Preconditions: {preconditions}'
    if expected_result:
        docstring += f'\n\n    Expected Result: {expected_result}'
    docstring += '\n    """'
    func_code += docstring + '\n'
    func_code += "\n".join(test_lines) + "\n"

    # Write to test file
    test_py = os.path.join(flow_dir, f"test_{flow_id}.py")
    if not os.path.exists(test_py):
        with open(test_py, "w", encoding="utf-8") as f:
            f.write(f'"""{flow_id.replace("_", " ").title()} flow."""\n\n')
            f.write("import re\nimport pytest\nfrom playwright.sync_api import expect, Page\n\n")

    with open(test_py, "r", encoding="utf-8") as f:
        existing = f.read()

    if f"def {func_name}" in existing:
        # Replace existing function using a robust pattern
        pattern = (
            r'(\\n\\n@pytest\\.mark\\.tc\\("' + re.escape(tc_id) +
            r'"\\)\\ndef ' + re.escape(func_name) +
            r'\(.*?)(?=\\n\\n@pytest\\.mark|\\Z)'
        )
        new_func = func_code.rstrip()
        existing = re.sub(pattern, new_func, existing, flags=re.DOTALL)
        with open(test_py, "w", encoding="utf-8") as f:
            f.write(existing)
    else:
        with open(test_py, "a", encoding="utf-8") as f:
            f.write(func_code)

    # Save test data
    data_file = os.path.join(flow_dir, "test_data.json")
    tc_data_json = {}
    if os.path.exists(data_file):
        with open(data_file, "r", encoding="utf-8") as f:
            try:
                tc_data_json = json.load(f)
            except Exception:
                pass
    tc_data_json[tc_id] = data_dict
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(tc_data_json, f, indent=4)

    # Update test_cases.json
    tc_file = os.path.join(flow_dir, "test_cases.json")
    tcs = []
    if os.path.exists(tc_file):
        with open(tc_file, "r", encoding="utf-8") as f:
            try:
                tcs = json.load(f)
            except Exception:
                pass

    tc_entry = {"tc_id": tc_id, "description": description}
    if preconditions:
        tc_entry["preconditions"] = preconditions
    if expected_result:
        tc_entry["expected_result"] = expected_result
    if assertions:
        tc_entry["assertions"] = assertions
    if criteria:
        tc_entry["criteria"] = criteria

    exists = False
    for tc in tcs:
        if tc.get("tc_id") == tc_id:
            tc.update(tc_entry)
            exists = True
            break
    if not exists:
        tcs.append(tc_entry)

    with open(tc_file, "w", encoding="utf-8") as f:
        json.dump(tcs, f, indent=4)

    # Cleanup temp file
    try:
        os.remove(os.path.join(ats_root, "temp_recording.py"))
    except Exception:
        pass

    return {
        "status": "success",
        "tc_id": tc_id,
        "flow": flow_id,
        "data": data_dict,
        "assertions_count": len(assertions),
        "criteria_count": len(criteria),
    }


# ── Legacy direct mode (backward compat) ──

def parse_and_inject(tc_id, description, flow_id, ats_root):
    """Parse temp_recording.py, extract dynamic data, and inject into flow (legacy)."""
    result = parse_steps(ats_root)
    if result["status"] != "success":
        print(json.dumps(result))
        return

    payload = {
        "tc_id": tc_id,
        "description": description,
        "flowId": flow_id,
        "steps": result["steps"],
        "assertions": [],
        "criteria": [],
    }
    res = generate_from_review(payload, ats_root)
    print(json.dumps(res))


# ── CLI entry point ──

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Usage: recorder_parser.py <mode> [args...]"}))
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "parse":
        # python recorder_parser.py parse <ats_root>
        if len(sys.argv) < 3:
            print(json.dumps({"status": "error", "message": "Missing ats_root"}))
            sys.exit(1)
        result = parse_steps(sys.argv[2])
        print(json.dumps(result))

    elif mode == "generate":
        # python recorder_parser.py generate <ats_root>
        # Reads payload JSON from stdin
        if len(sys.argv) < 3:
            print(json.dumps({"status": "error", "message": "Missing ats_root"}))
            sys.exit(1)
        payload = json.load(sys.stdin)
        result = generate_from_review(payload, sys.argv[2])
        print(json.dumps(result))

    elif mode == "legacy":
        # python recorder_parser.py legacy <tc_id> <desc> <flow_id> <ats_root>
        if len(sys.argv) < 6:
            print(json.dumps({"status": "error", "message": "Missing arguments for legacy mode"}))
            sys.exit(1)
        parse_and_inject(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])

    else:
        print(json.dumps({"status": "error", "message": f"Unknown mode: {mode}"}))
        sys.exit(1)
