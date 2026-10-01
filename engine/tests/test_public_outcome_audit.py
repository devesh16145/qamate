import pytest

from public_bench_tasks import get_task
from public_outcome_audit import audit_source, audit_flow, coverage_verdict


def click(target):
    return f'page.locator(\'[data-test="{target}"]\').click()'


def fill(target):
    return f'page.locator(\'[data-test="{target}"]\').fill(tc_data.get("input", ""))'


def text(value, absent=False):
    method = "not_to_contain_text" if absent else "to_contain_text"
    return f'expect(page.locator("body")).{method}({value!r})'


def url(value):
    return f'expect(page).to_have_url(re.compile(".*" + re.escape({value!r}) + ".*"))'


def source(rows, task="cart-edit"):
    name = get_task(task)["tc_id"].replace("-", "_")
    return f'def test_{name}(page, checkpoints, tc_data):\n' + "\n".join("    " + r for r in rows)


def cart():
    final = [url("/cart.html"), text("Sauce Labs Backpack", True), text("Sauce Labs Onesie")]
    return [fill("username"), fill("password"), click("login-button"),
            click("item-4-title-link"), text("Sauce Labs Backpack"), click("add-to-cart"),
            click("back-to-products"), click("add-to-cart-sauce-labs-onesie"),
            click("shopping-cart-link"), url("/cart.html"), text("Sauce Labs Backpack"), text("Sauce Labs Onesie"),
            click("remove-sauce-labs-backpack"), *final, click("continue-shopping"), click("shopping-cart-link"), *final]


def negative():
    return [click("login-button"), text("Epic sadface: Username is required"), fill("username"),
            click("login-button"), text("Epic sadface: Password is required"), fill("password"),
            click("login-button"), url("/inventory.html"), text("Products")]


def test_negative_login_exact_semantics_without_decorative_prefix():
    rows = [row.replace("Epic sadface: ", "") for row in negative()]
    task = get_task("negative-login")
    assert audit_source(source(rows, "negative-login"), task)["passed"]
    for bad in ("is required", "Email is required", "Username is optional"):
        changed = [row.replace("Username is required", bad) for row in rows]
        assert not audit_source(source(changed, "negative-login"), task)["passed"]
    for index in (1, 4):
        assert not audit_source(source(rows[:index] + rows[index+1:], "negative-login"), task)["passed"]
    rows[1], rows[4] = rows[4], rows[1]
    assert not audit_source(source(rows, "negative-login"), task)["passed"]


def test_smoke_sort_css_assertion_and_negative_controls():
    assertion = "expect(page.locator('[data-test=\"product-sort-container\"]')).to_have_value('lohi')"
    rows = [fill('username'), fill('password'), click('login-button'), assertion,
            click('add-to-cart-sauce-labs-onesie'), click('shopping-cart-link'),
            url('/cart.html'), text('Sauce Labs Onesie')]
    assert audit_source(source(rows, 'smoke'), get_task('smoke'))['passed']
    for replacement in ['', assertion.replace('lohi', 'hilo'),
                        assertion.replace('product-sort-container', 'unrelated')]:
        changed = [replacement if row == assertion else row for row in rows]
        assert not audit_source(source(changed, 'smoke'), get_task('smoke'))['passed']


@pytest.mark.parametrize("task,rows", [("cart-edit", cart()), ("negative-login", negative())])
def test_complete_supported_workflows(task, rows):
    assert audit_source(source(rows, task), get_task(task))["passed"]


def lifecycle():
    initial = [text(v) for v in ["QAMATE Lifecycle 9f58c327", "Qamate Test City", "Qamate original description", "Industrials", "10-49 employees"]]
    revised = [text(v) for v in ["QAMATE Lifecycle 9f58c327", "Qamate Revised City", "Qamate revised description", "Industrials", "10-49 employees"]]
    revised += [text("Qamate Test City", True), text("Qamate original description", True)]
    values = [f'expect(page.get_by_role("textbox", name={label!r})).to_have_value({value!r})'
              for label, value in [("Company name", "QAMATE Lifecycle 9f58c327"), ("Website", "https://example.com"),
                                   ("City", "Qamate Test City"), ("Description", "Qamate original description")]]
    return [fill("Company name"), *values, click("Create Company"), *initial, click("Edit"),
            fill("City"), fill("Description"), click("Save"), *revised, click("Companies"), fill("Search"),
            'expect(page.get_by_role("textbox", name="Search")).to_have_value("QAMATE Lifecycle 9f58c327")',
            text("QAMATE Lifecycle 9f58c327"), url("#/companies"), click("QAMATE Lifecycle 9f58c327"), *revised]


def test_complete_lifecycle_and_every_required_outcome():
    rows = lifecycle()
    assert audit_source(source(rows, "crm-lifecycle"), get_task("crm-lifecycle"))["passed"]
    for i, row in enumerate(rows):
        if row.startswith("expect"):
            altered = rows[:i] + rows[i+1:]
            assert not audit_source(source(altered, "crm-lifecycle"), get_task("crm-lifecycle"))["passed"], row


def test_lifecycle_wrong_record_and_missing_roundtrip_fail():
    rows = lifecycle()
    for marker in ["Create Company", "Edit", "Save", "Companies", "QAMATE Lifecycle 9f58c327"]:
        changed = [row.replace(marker, "wrong-record") if ".click()" in row else row for row in rows]
        assert not audit_source(source(changed, "crm-lifecycle"), get_task("crm-lifecycle"))["passed"]


def related_contact():
    rows = [row.replace("QAMATE Lifecycle", "QAMATE Relationship") for row in lifecycle()
            if "10-49 employees" not in row]
    company = "QAMATE Relationship 9f58c327"
    return rows + [click("New contact"), fill("First name"), fill("Last name"), fill("Email"),
                   fill("Title"), click("Save"), text("Qamate RelationTest"),
                   text("qamate.relation@example.com"), text("QA Engineer"), text(company),
                   click(company), click("Contacts"), text(company),
                   text("Qamate RelationTest"), text("QA Engineer")]


def test_related_contact_all_assertions_required_and_tab_case_supported():
    rows = related_contact()
    task = get_task("crm-related-contact")
    assert audit_source(source(rows, "crm-related-contact"), task)["passed"]
    for i, row in enumerate(rows):
        if row.startswith("expect"):
            assert not audit_source(source(rows[:i] + rows[i+1:], "crm-related-contact"), task)["passed"], row
    for wrong in ["Wrong company", "Wrong contact", "Wrong title"]:
        altered = list(rows)
        altered[-1 if wrong == "Wrong title" else -2 if wrong == "Wrong contact" else -3] = text(wrong)
        assert not audit_source(source(altered, "crm-related-contact"), task)["passed"]


@pytest.mark.parametrize("index", [4, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20])
def test_every_required_cart_outcome_and_roundtrip_is_mandatory(index):
    rows = cart()
    rows.pop(index)
    assert not audit_source(source(rows), get_task("cart-edit"))["passed"]


@pytest.mark.parametrize("index", range(9))
def test_every_negative_login_stage_is_mandatory(index):
    rows = negative()
    rows.pop(index)
    assert not audit_source(source(rows, "negative-login"), get_task("negative-login"))["passed"]


def test_unexecuted_and_caught_assertions_do_not_count():
    valid = source(cart())
    for altered in [valid.replace("    expect", "    # expect"),
                    valid.replace("    expect", "    if False: expect"),
                    valid + "\n    return\n", valid.replace("test_TC_PILOT_002", "test_OTHER")]:
        assert not audit_source(altered, get_task("cart-edit"))["passed"]


def test_optional_regex_and_wrong_page_cannot_satisfy_identity():
    for changed in [url("/inventory.html"), 'expect(page).to_have_url(re.compile("/cart.html|.*"))']:
        altered = source(cart()).replace(url("/cart.html"), changed)
        assert not audit_source(altered, get_task("cart-edit"))["passed"]


def test_navigation_between_assertions_invalidates_state_group():
    rows = cart()
    rows.insert(15, click("back-to-products"))
    assert not audit_source(source(rows), get_task("cart-edit"))["passed"]


def test_emitter_checkpoint_wrappers_and_readiness_try_are_supported():
    wrapped = source(cart()).splitlines()
    wrapped = wrapped[0] + '\n    with checkpoints.step("flow"):\n' + '\n'.join('    ' + s for s in wrapped[1:])
    wrapped += '\n    try:\n        page.wait_for_load_state("networkidle")\n    except Exception:\n        pass\n'
    assert audit_source(wrapped, get_task("cart-edit"))["passed"]
    hidden = wrapped.replace('page.wait_for_load_state("networkidle")', 'expect(page).to_have_url("/cart.html")')
    assert not audit_source(hidden, get_task("cart-edit"))["passed"]


def test_missing_or_multiple_test_files_fail_closed(tmp_path):
    assert not audit_flow(tmp_path, get_task("cart-edit"))["passed"]
    (tmp_path / "test_a.py").write_text(source(cart()))
    assert audit_flow(tmp_path, get_task("cart-edit"))["passed"]
    (tmp_path / "test_b.py").write_text(source(cart()))
    assert not audit_flow(tmp_path, get_task("cart-edit"))["passed"]


def test_unassessed_mutations_fail_closed():
    assert not audit_source(source(cart()) + '\n    page.evaluate("mutate()")', get_task("cart-edit"))["passed"]


@pytest.mark.parametrize("verdict", ["timeout", "model_error", "failing", "flaky", "not_delivered"])
def test_coverage_cannot_upgrade_execution_failures(verdict):
    assert coverage_verdict(verdict, {"passed": True}) == verdict


@pytest.mark.parametrize("audit", [{}, {"passed": False}, {"passed": "true"}])
def test_replay_alone_cannot_satisfy_coverage(audit):
    assert coverage_verdict("reliable", audit) == "coverage_unverified"
    assert coverage_verdict("reliable", {"passed": True}) == "reliable"


def crm():
    selector = 'a[href^="#/companies/"]:visible:not([href$="/create"]):not([href$="/new"]):not([href$="/import"])'
    return ['page.get_by_role("textbox", name="Search").fill("QAMATE-NOMATCH-9f58c327")',
        'expect(page.get_by_role("textbox", name="Search")).to_have_value("QAMATE-NOMATCH-9f58c327")',
        url("#/companies"), text("No companies found"),
        'page.get_by_role("button", name="Clear search").click()',
        'expect(page.get_by_role("textbox", name="Search")).to_have_value("")',
        url("#/companies"), f'expect(page.locator({selector!r}).first).to_be_visible()']


def test_crm_requires_positive_record_collection():
    rows = crm()
    assert audit_source(source(rows, "crm-search"), get_task("crm-search"))["passed"]
    rows[-1] = text("No companies found", absent=True)
    assert not audit_source(source(rows, "crm-search"), get_task("crm-search"))["passed"]


@pytest.mark.parametrize("missing", range(8))
def test_crm_missing_required_outcome_or_action_fails(missing):
    rows = crm()
    del rows[missing]
    assert not audit_source(source(rows, "crm-search"), get_task("crm-search"))["passed"]
