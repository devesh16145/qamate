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


# ── grounding (shapes taken from the Atomic CRM demo) ─────────────────────────
from types import SimpleNamespace

from qm_ground import decide, rank

CRM = '''- generic [ref=e1]:
  - navigation [ref=e2]:
    - link "Contacts" [ref=e3] [cursor=pointer]:
      - /url: "#/contacts"
    - link "Companies" [ref=e4] [cursor=pointer]:
      - /url: "#/companies"
  - main [ref=e5]:
    - heading "QAMATE Lifecycle" [level=5] [ref=e6]
    - tablist [ref=e7]:
      - tab "Activity" [selected] [ref=e8]
      - tab "1 contact" [ref=e9]
    - link "Create contact" [ref=e10] [cursor=pointer]:
      - /url: "#/contacts/create"
    - link "Acme Rockets" [ref=e11] [cursor=pointer]:
      - /url: "#/companies/7/show"
    - link "Acme Rockets" [ref=e12] [cursor=pointer]:
      - /url: "#/companies/7/show"
    - group [ref=e13]:
      - generic [ref=e14]: Company
      - combobox "Company" [ref=e15] [cursor=pointer]: Acme Rockets
    - link [ref=e20] [cursor=pointer]:
      - /url: "#/companies/55/show"
      - generic [ref=e21]:
        - img "QAMATE Lifecycle" [ref=e22]
        - generic [ref=e23]:
          - heading "QAMATE Lifecycle" [level=6] [ref=e24]
          - paragraph [ref=e25]: Industrials
'''


def _ground(op, target, **extra):
    elements, _ = parse(CRM)
    candidates = rank({"op": op, "target": target, **extra}, SimpleNamespace(elements=elements))
    pick = decide(candidates)
    return pick.element if pick else None, candidates


def test_a_nameless_card_link_is_named_by_its_content_and_clicked_through_its_heading():
    elements, _ = parse(CRM)
    card = next(e for e in elements if e.ref == "e20")
    assert card.label == "QAMATE Lifecycle Industrials"
    pick, _ = _ground("click", "QAMATE Lifecycle")
    assert pick is not None and pick.ref == "e24"   # the card's heading (readable), not its image


def test_an_exact_label_beats_one_that_only_contains_the_word():
    pick, _ = _ground("click", "Contacts")
    assert pick.ref == "e3"


def test_synonyms_ground_a_guessed_label_without_a_replan():
    pick, _ = _ground("click", "New Contact")
    assert pick.ref == "e10"


def test_a_named_role_wins_and_counts_in_labels_are_ignored():
    pick, _ = _ground("click", "Contacts tab")
    assert pick.ref == "e9"


def test_duplicate_links_to_the_same_place_are_one_choice():
    pick, candidates = _ground("click", "Acme Rockets")
    assert len([c for c in candidates if c.element.role == "link"]) == 2 and pick.ref == "e11"


def test_a_text_check_targets_the_control_showing_the_value():
    pick, _ = _ground("expect_text", "Company", value="Acme Rockets")
    assert pick.ref == "e15"


# ── app memory (qm_map) ──────────────────────────────────────────────────────
from qm_map import PageMap, page_controls, route_of


def _obs(url, snapshot, title="App"):
    elements, texts = parse(snapshot)
    return SimpleNamespace(url=url, title=title, elements=elements, page_text=texts)


LIST = '''- navigation [ref=e1]:
  - link "Orders" [ref=e2]:
    - /url: "#/orders"
  - link "Customers" [ref=e3]:
    - /url: "#/customers"
- main [ref=e4]:
  - link "New order" [ref=e5]:
    - /url: "#/orders/create"
  - textbox "Search" [ref=e6]
''' + "".join(f'''  - generic [ref=r{i}]:
    - link "Order #{1000 + i} Pending" [ref=l{i}]:
      - /url: "#/orders/{1000 + i}"
''' for i in range(12))

FORM = '''- navigation [ref=e1]:
  - link "Orders" [ref=e2]:
    - /url: "#/orders"
  - link "Customers" [ref=e3]:
    - /url: "#/customers"
- main [ref=e4]:
  - textbox "Customer" [ref=f1]
  - combobox "Status" [ref=f2]
  - textbox "Notes" [ref=f3]
  - button "Create order" [ref=f4]
'''


def test_routes_fold_record_ids_and_drop_queries():
    assert route_of("https://x.test/app/#/orders/1042/show?tab=2") == "/app/#/orders/:id/show"
    assert route_of("https://x.test/users/5f2b9c1e7a/edit") == "/users/:id/edit"
    assert route_of("https://x.test/orders?page=2") == "/orders"


def test_page_controls_keep_fields_and_collapse_record_lists():
    lines = page_controls(_obs("https://x.test/#/orders", LIST))
    assert 'link "New order"' in lines and 'textbox "Search"' in lines
    assert sum(1 for l in lines if l.startswith('link "Order #')) == 2 and "... +10 more links" in lines


def test_map_tells_the_planner_real_labels_and_where_links_lead(tmp_path):
    path = str(tmp_path / "page_map.json")
    m = PageMap(path)
    m.see(_obs("https://x.test/#/orders", LIST))
    m.see(_obs("https://x.test/#/orders/create", FORM))
    m.went("https://x.test/#/orders", "New order", "https://x.test/#/orders/create")
    m.save()
    lines = PageMap(path).for_task("Create an order for ACME with status Paid", "https://x.test/#/dashboard")
    assert lines[0].startswith("Everywhere: ") and 'link "Orders"' in lines[0]
    form = next(l for l in lines if l.startswith("/#/orders/create"))
    assert 'button "Create order"' in form and 'link "Orders"' not in form       # chrome listed once
    listing = next(l for l in lines if l.startswith("/#/orders ("))
    assert 'link "New order" -> /#/orders/create' in listing
    assert lines.index(form) < lines.index(listing)   # most relevant page first ("create", "order", "status")


def test_map_respects_its_budget_and_skips_the_current_page():
    m = PageMap()
    m.see(_obs("https://x.test/#/orders", LIST))
    m.see(_obs("https://x.test/#/orders/create", FORM))
    assert not any(l.startswith("/#/orders/create") for l in m.for_task("orders", "https://x.test/#/orders/create"))
    assert sum(len(l) for l in m.for_task("orders", budget=120)) <= 120


SHOP = '''- main [ref=m1]:
''' + "".join(f'''  - generic [ref=c{i}]:
    - button "View details for {name}" [ref=v{i}] [cursor=pointer]:
      - img "{name}" [ref=i{i}]
    - button "View details for {name}" [ref=t{i}] [cursor=pointer]:
      - generic [ref=n{i}]: {name}
    - button "Add to cart" [ref=a{i}] [cursor=pointer]
''' for i, name in enumerate(["Sauce Labs Backpack", "Sauce Labs Onesie", "Sauce Labs Bike Light"])) + '''  - button "Cart, 2 items" [ref=cart] [cursor=pointer]
'''


def test_a_cards_image_named_like_its_title_is_not_a_rival():
    elements, _ = parse(SHOP)
    pick = decide(rank({"op": "click", "target": "Sauce Labs Onesie"}, SimpleNamespace(elements=elements)))
    assert pick is not None and pick.element.ref == "n1"


def test_look_alike_controls_need_a_within_to_be_chosen():
    elements, _ = parse(SHOP)
    obs = SimpleNamespace(elements=elements)
    assert decide(rank({"op": "click", "target": "Cart"}, obs)).element.ref == "cart"
    assert decide(rank({"op": "click", "target": "Add to cart"}, obs)) is None       # which one?
    pick = decide(rank({"op": "click", "target": "Add to cart", "within": "Sauce Labs Bike Light"}, obs))
    assert pick.element.ref == "a2"
