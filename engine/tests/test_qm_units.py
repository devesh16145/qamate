"""Offline tests for the fast explorer's pure pieces: snapshot parsing, locator
rendering and step rendering. No browser."""
import pytest

from qm_observe import parse
from qm_selectors import to_python
from qm_steps import render, call_spec

SNAPSHOT = '''- generic [ref=e2]:
  - navigation [ref=e3]:
    - link "Orders" [ref=e4] [cursor=pointer]:
      - /url: /orders
  - main [ref=e5]:
    - heading "Orders" [level=1] [ref=e6]
    - table [ref=e7]:
      - row "#1001 Pending Edit" [ref=e8]:
        - cell "#1001" [ref=e9]
        - cell [ref=e10]:
          - button "Edit" [ref=e11]
    - dialog "Confirm delete" [ref=e12]:
      - paragraph [ref=e13]: Delete order?
      - button "Delete" [ref=e14]
    - checkbox "Remember me" [checked] [ref=e15]
    - button [ref=e16] [cursor=pointer]: All Items
    - textbox "Say \\"hi\\"" [ref=e17]
    - text: Saving transfer…
'''


def test_parse_reads_roles_names_state_regions_and_links():
    elements, texts = parse(SNAPSHOT)
    by_ref = {e.ref: e for e in elements}
    assert by_ref["e4"].role == "link" and by_ref["e4"].url == "/orders"
    assert by_ref["e4"].region("navigation") == ("navigation", "")
    assert by_ref["e11"].region("row") == ("row", "#1001 Pending Edit")
    assert by_ref["e14"].region("dialog") == ("dialog", "Confirm delete")
    assert by_ref["e15"].attrs.get("checked") is True
    assert by_ref["e6"].attrs.get("level") == "1"
    assert by_ref["e16"].label == "All Items" and by_ref["e16"].interactive
    assert by_ref["e17"].name == 'Say "hi"'
    assert "Delete order?" in texts and "Saving transfer…" in texts
    assert by_ref["e14"].summary() == 'button "Delete" in dialog "Confirm delete"'


@pytest.mark.parametrize("selector, expected", [
    ('internal:role=button[name="Login"i]', 'page.get_by_role("button", name="Login")'),
    ('internal:role=button[name="Login"s]', 'page.get_by_role("button", name="Login", exact=True)'),
    ('internal:role=row[name="#1001 Pending Edit"i] >> internal:role=button',
     'page.get_by_role("row", name="#1001 Pending Edit").get_by_role("button")'),
    ('internal:role=heading[level=2][name="Orders"i]', 'page.get_by_role("heading", level=2, name="Orders")'),
    ('internal:role=checkbox[checked=true][name="Agree"i]', 'page.get_by_role("checkbox", checked=True, name="Agree")'),
    ('internal:label="Status"i', 'page.get_by_label("Status")'),
    ('internal:text="Welcome"s', 'page.get_by_text("Welcome", exact=True)'),
    ('internal:attr=[placeholder="Order number"i]', 'page.get_by_placeholder("Order number")'),
    ('internal:testid=[data-testid="save-btn"s]', 'page.get_by_test_id("save-btn")'),
    ('[data-test="login-button"]', "page.locator('[data-test=\"login-button\"]')"),
    ('div >> internal:has-text="Plan A"i >> internal:role=button',
     'page.locator("div").filter(has_text="Plan A").get_by_role("button")'),
    ('internal:role=button[name="Choose"i] >> nth=1', 'page.get_by_role("button", name="Choose").nth(1)'),
    ('internal:role=button[name="Choose"i] >> nth=0', 'page.get_by_role("button", name="Choose").first'),
    ('internal:role=button[name="Say \\"hi\\""i]', 'page.get_by_role("button", name=\'Say "hi"\')'),
])
def test_selectors_render_as_readable_python(selector, expected):
    assert to_python(selector) == expected


def test_unknown_selector_forms_fall_back_to_raw_locator():
    sel = 'internal:control=enter-frame >> internal:role=button[name="Pay"i]'
    assert to_python(sel) == f"page.locator({sel!r})" or to_python(sel).startswith("page.locator(")


def test_steps_render_to_flow_calls():
    target = 'page.get_by_role("textbox", name="Username")'
    assert render({"op": "fill", "target": target, "name": "Username", "value": "alice", "data_key": "username"}) == \
        'flow.fill(page.get_by_role("textbox", name="Username"), tc_data["username"], "Username")'
    assert render({"op": "click", "target": 'page.get_by_role("button", name="Login")', "name": "Login",
                   "expect_url": "/inventory.html"}) == \
        'flow.click(page.get_by_role("button", name="Login"), "Login", expect_url="/inventory.html")'
    assert render({"op": "goto", "value": "/", "name": "Open the app"}) == 'flow.goto("/", "Open the app")'
    assert render({"op": "press", "value": "Enter"}) == 'flow.press("Enter")'
    assert render({"op": "expect_page_text", "value": "Saved", "present": False, "name": "No error"}) == \
        'flow.expect_page_text("Saved", False, "No error")'
    assert render({"op": "upload", "target": 'page.get_by_label("File")'}) == \
        'flow.upload(page.get_by_label("File"), TEST_UPLOAD_IMAGE)'
    with pytest.raises(ValueError):
        call_spec({"op": "teleport"})
