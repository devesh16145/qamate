"""Conservative coverage audit of generated public-demo tests, without execution.

This recognizes Qamate's straight-line Playwright emitter, not arbitrary Python.
Unsupported code is unassessed, never evidence of coverage. Replay remains a
separate mandatory gate; this is not a Python sandbox or a correctness proof.
"""
import ast
from pathlib import Path


def coverage_verdict(replay_verdict, audit):
    """Coverage can disqualify a replay success, never upgrade a failed run."""
    if replay_verdict == "reliable" and audit.get("passed") is not True:
        return "coverage_unverified"
    return replay_verdict


def _strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _calls(body):
    for node in body:
        if isinstance(node, ast.With):
            if not all(isinstance(i.context_expr, ast.Call) and
                       ast.unparse(i.context_expr.func) == "checkpoints.step" for i in node.items):
                raise ValueError("unsupported context manager")
            yield from _calls(node.body)
        elif isinstance(node, ast.Try):
            # The emitter optionally waits for networkidle in a caught block.
            if (node.orelse or node.finalbody or not node.handlers or
                any(not isinstance(s, ast.Pass) for h in node.handlers for s in h.body) or
                any(not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Call) or
                    ast.unparse(s.value.func) != "page.wait_for_load_state" for s in node.body)):
                raise ValueError("unsupported exception handling")
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call):
                yield node.value
            elif not isinstance(node.value, ast.Constant):
                raise ValueError("unsupported expression")
        elif not isinstance(node, ast.Pass):
            raise ValueError("unsupported test control flow")


def events(source, tc_id):
    tree = ast.parse(source)
    name = "test_" + tc_id.replace("-", "_")
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and
               (n.name == name or n.name.startswith(name + "_"))]
    if len(matches) != 1:
        raise ValueError("expected exactly one requested test")
    result = []
    for call in _calls(matches[0].body):
        if not isinstance(call.func, ast.Attribute):
            raise ValueError("unsupported helper call")
        method, receiver = call.func.attr, call.func.value
        if isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name) and receiver.func.id == "expect":
            target = ast.unparse(receiver.args[0]) if receiver.args else ""
            if method == "to_be_visible" and receiver.args:
                node = receiver.args[0]
                if (isinstance(node, ast.Attribute) and node.attr == "first" and
                    isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "page.locator" and
                    len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Constant) and
                    node.value.args[0].value == 'a[href^="#/companies/"]:visible:not([href$="/create"]):not([href$="/new"]):not([href$="/import"])'):
                    result.append(("collection", "companies"))
            if not call.args:
                continue
            expected = call.args[0]
            if method == "to_have_url" and target == "page":
                # Require the exact emitter shape, not an arbitrary regex that
                # merely contains the desired URL as an optional alternative.
                if (isinstance(expected, ast.Call) and ast.unparse(expected.func) == "re.compile" and
                    len(expected.args) == 1):
                    pattern = expected.args[0]
                    if (isinstance(pattern, ast.BinOp) and isinstance(pattern.op, ast.Add) and
                        isinstance(pattern.left, ast.BinOp) and isinstance(pattern.left.op, ast.Add) and
                        isinstance(pattern.left.left, ast.Constant) and pattern.left.left.value == ".*" and
                        isinstance(pattern.right, ast.Constant) and pattern.right.value == ".*" and
                        isinstance(pattern.left.right, ast.Call) and ast.unparse(pattern.left.right.func) == "re.escape" and
                        len(pattern.left.right.args) == 1 and isinstance(pattern.left.right.args[0], ast.Constant)):
                        result.append(("url", pattern.left.right.args[0].value))
                elif isinstance(expected, ast.Constant) and isinstance(expected.value, str):
                    result.append(("url", expected.value))
            elif method in {"to_contain_text", "not_to_contain_text", "to_have_text", "to_have_value"} and isinstance(expected, ast.Constant):
                if method == "to_have_value":
                    result.append(("input_value", (target, expected.value)))
                if target in {"page.locator('body')", 'page.locator("body")'} and method != "to_have_value":
                    result.append(("absent" if method == "not_to_contain_text" else "present", expected.value))
                elif method == "to_have_value" and any(s in {
                    "product-sort-container", '[data-test="product-sort-container"]',
                    "[data-test='product-sort-container']"} for s in _strings(receiver)):
                    result.append(("sort", expected.value))
                elif method == "to_have_value" and target in {
                    'page.locator(\'internal:role=textbox[name="Search"i]\')',
                    "page.get_by_role('textbox', name='Search')"}:
                    result.append(("search_value", expected.value))
        elif method in {"click", "fill", "select_option", "goto"}:
            result.append((method, " ".join(_strings(receiver))))
        elif method == 'press_sequentially':
            result.append(('fill', ' '.join(_strings(receiver))))
        elif (method == 'press' and ast.unparse(receiver) != 'page.keyboard'
              and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
              and call.args[0].value == 'ControlOrMeta+a'):
            # Recorder's select-all before replacement typing is not readback.
            result.append(('fill', ' '.join(_strings(receiver))))
        elif method == 'press' and ast.unparse(receiver) == 'page.keyboard' and len(call.args) == 1 and isinstance(call.args[0], ast.Constant) and call.args[0].value == 'Tab':
            # Emitted numeric-field blur is a mutation boundary, not an assertion.
            result.append(('fill', 'keyboard Tab'))
        elif method not in {"scroll_into_view_if_needed", "wait_for_timeout", "wait_for_load_state"} and ast.unparse(call.func) != "checkpoints.mark_passed":
            raise ValueError("unsupported operation")
    return result


def audit_source(source, task):
    try:
        rows = events(source, task["tc_id"])
    except (SyntaxError, ValueError, TypeError, IndexError):
        return {"passed": False, "reason": "unsupported_or_invalid_test", "version": 1}
    name = task["flow_id"]
    missing = []
    generic = {v['flow_id']: k for k, v in __import__('public_bench_tasks').TASKS.items()
               if k in {'ops-transfer', 'ops-validation', 'ops-allocation', 'sort-detail'}}
    if name in generic:
        from public_bench_tasks import required_outcome_groups
        blocks, block = [], []
        for kind, value in rows:
            if kind in {'click', 'goto', 'fill', 'select_option'}:
                if block:
                    blocks.append(block)
                    block = []
            else:
                block.append((kind, value[1] if kind == 'input_value' else value))
        if block:
            blocks.append(block)
        mapping = {'url_contains': 'url', 'page_contains_text': 'present',
                   'page_not_contains_text': 'absent', 'element_has_value': 'input_value'}
        cursor = 0
        for group_spec in required_outcome_groups(generic[name]):
            needed = {(mapping[o['kind']], o['value']) for o in group_spec['outcomes']}
            match = next((i for i in range(cursor, len(blocks)) if needed <= set(blocks[i])), None)
            if match is None:
                missing.append(group_spec['name'])
            else:
                cursor = match + 1
        return {'passed': not missing, 'missing': missing, 'version': 2}

    def action(op, marker, after=-1, ignore_case=False):
        return next((i for i, (kind, value) in enumerate(rows) if i > after and kind == op and
                     (marker.casefold() in value.casefold() if ignore_case else marker in value)), None)

    def require(label, ok):
        if not ok:
            missing.append(label)

    def group(start, end, required):
        if start is None or end is None or start >= end:
            return False
        segment = rows[start + 1:end]
        # Assertions must describe one settled state, not combine observations
        # across intervening clicks, navigation or field changes.
        return not any(k in {"click", "goto", "fill", "select_option"} for k, _ in segment) and all(r in segment for r in required)

    login = action("click", "login-button")
    if name in {"bench_crm_lifecycle", "bench_crm_related_contact"}:
        related = name == "bench_crm_related_contact"
        company = "QAMATE Relationship 9f58c327" if related else "QAMATE Lifecycle 9f58c327"
        create = action("click", "Create Company")
        edit = action("click", "Edit", create) if create is not None else None
        save = action("click", "Save", edit) if edit is not None else None
        companies = action("click", "Companies", save) if save is not None else None
        search = action("fill", "Search", companies) if companies is not None else None
        reopen = action("click", company, search) if search is not None else None
        require("lifecycle_action_order", None not in (create, edit, save, companies, search, reopen))
        initial = [("present", v) for v in [company, "Qamate Test City", "Qamate original description", "Industrials"]]
        revised = [("present", v) for v in [company, "Qamate Revised City", "Qamate revised description", "Industrials"]]
        if not related:
            initial.append(("present", "10-49 employees"))
            revised.append(("present", "10-49 employees"))
        revised += [("absent", "Qamate Test City"), ("absent", "Qamate original description")]
        require("created_detail", group(create, edit, initial))
        require("edited_detail", group(save, companies, revised))
        require("searched_exact_record", group(search, reopen, [
            ("search_value", company), ("present", company),
            ("url", "#/companies")]))
        next_action = next((i for i, (k, _) in enumerate(rows) if reopen is not None and i > reopen and
                           k in {"click", "fill", "goto", "select_option"}), len(rows))
        require("reopened_edited_detail", group(reopen, next_action if related else len(rows), revised))
        if related:
            contact_create = action("click", "Save", reopen) if reopen is not None else None
            parent_return = action("click", company, contact_create) if contact_create is not None else None
            require("saved_related_contact", group(contact_create, parent_return, [
                ("present", "Qamate RelationTest"), ("present", "qamate.relation@example.com"),
                ("present", "QA Engineer"), ("present", company)]))
            contact_tab = action("click", "contact", parent_return, ignore_case=True) if parent_return is not None else None
            require("parent_child_relationship_readback", group(contact_tab, len(rows), [
                ("present", company), ("present", "Qamate RelationTest"), ("present", "QA Engineer")]))
        # Require explicit pre-submit readback of each supplied text field on one
        # settled form state, rather than accepting fills as proof of input state.
        last_mutation = max((i for i, (k, _) in enumerate(rows[:create or 0])
                             if k in {"click", "fill", "select_option", "goto"}), default=-1)
        pre_submit = rows[last_mutation + 1:create] if create is not None else []
        for label, expected in [("Company name", company),
                                ("Website", "https://example.com"),
                                ("City", "Qamate Test City"), ("Description", "Qamate original description")]:
            require("pre_submit_" + label, any(k == "input_value" and label.lower() in v[0].lower()
                                              and v[1] == expected for k, v in pre_submit))
    elif name == "bench_crm_search":
        search = action("fill", "Search")
        clear = action("click", "Clear search", search) if search is not None else None
        require("search_empty_results_on_companies", group(search, clear, [
            ("search_value", "QAMATE-NOMATCH-9f58c327"), ("url", "#/companies"),
            ("present", "No companies found")]))
        require("cleared_and_positive_population", group(clear, len(rows), [
            ("search_value", ""), ("url", "#/companies"), ("collection", "companies")]))
    elif name == 'bench_public_pilot':
        cart = action('click', 'shopping-cart-link')
        added = action('click', 'add-to-cart-sauce-labs-onesie')
        username, password = action('fill', 'username'), action('fill', 'password')
        require('login_recorded', None not in (username, password, login, added) and max(username, password) < login < added)
        require('sort_selected', added is not None and any(k == 'sort' and v == 'lohi' for k, v in rows[:added]))
        require('cart_product', group(cart, len(rows), [('url', '/cart.html'), ('present', 'Sauce Labs Onesie')]))
    elif name == "bench_negative_login":
        second = action("click", "login-button", login) if login is not None else None
        third = action("click", "login-button", second) if second is not None else None
        username = action("fill", "username", login) if login is not None else None
        password = action("fill", "password", second) if second is not None else None
        # Accept the exact meaningful validation message with or without the
        # decorative prefix. Never accept generic "required" or wrong-field text.
        require("empty_username_validation", any(group(login, username, [("present", prefix + "Username is required")])
                                                  for prefix in ("", "Epic sadface: ")))
        require("empty_password_validation", any(group(second, password, [("present", prefix + "Password is required")])
                                                  for prefix in ("", "Epic sadface: ")))
        require("negative_submission_order", None not in (login, username, second, password, third) and login < username < second < password < third)
        require("no_prefilled_negative_inputs", login is not None and not any(k == "fill" for k, _ in rows[:login]))
        require("successful_inventory", group(third, len(rows), [("url", "/inventory.html"), ("present", "Products")]))
    elif name == "bench_cart_edit":
        remove = action("click", "remove-sauce-labs-backpack")
        continued = action("click", "continue-shopping", remove) if remove is not None else None
        returned = action("click", "shopping-cart-link", continued) if continued is not None else None
        cart = action("click", "shopping-cart-link")
        detail = action("click", "item-4-title-link")
        add = action("click", "add-to-cart", detail) if detail is not None else None
        username, password = action("fill", "username"), action("fill", "password")
        require("login_recorded", None not in (login, username, password, detail) and max(username, password) < login < detail)
        require("detail_name_before_add", group(detail, add, [("present", "Sauce Labs Backpack")]))
        require("both_products_before_removal", group(cart, remove, [("url", "/cart.html"), ("present", "Sauce Labs Backpack"), ("present", "Sauce Labs Onesie")]))
        final = [("url", "/cart.html"), ("absent", "Sauce Labs Backpack"), ("present", "Sauce Labs Onesie")]
        require("correct_removal", group(remove, continued, final))
        require("roundtrip_persistence", group(returned, len(rows), final))
    else:
        return {"passed": False, "reason": "unsupported_task", "version": 1}
    return {"passed": not missing, "missing": missing, "version": 1}


def audit_flow(flow, task):
    paths = list(Path(flow).glob("test_*.py"))
    if len(paths) != 1:
        return {"passed": False, "reason": "expected_one_test_file", "version": 1}
    try:
        return audit_source(paths[0].read_text(encoding="utf-8"), task)
    except (OSError, UnicodeError):
        return {"passed": False, "reason": "unreadable_test", "version": 1}
