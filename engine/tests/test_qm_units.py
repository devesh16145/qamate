"""Offline tests for the fast explorer's pure pieces: snapshot parsing, locator
rendering and step rendering. No browser."""
import pytest

from qm_observe import parse
from qm_selectors import to_python
import json

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


def test_hash_root_is_the_app_root():
    assert route_of("https://x.test/app/#/") == route_of("https://x.test/app/") == "/app/"


def test_explore_reply_lists_navigation_once_then_fields_actions_and_destinations():
    from qm_map import describe_pages
    m = PageMap()
    m.see(_obs("https://x.test/#/orders", LIST))
    m.see(_obs("https://x.test/#/orders/create", FORM))
    m.went("https://x.test/#/orders", "New order", "https://x.test/#/orders/create")
    lines = describe_pages(m)
    assert lines[0] == "**On every page:** Orders, Customers"
    listing = next(l for l in lines if l.startswith("- `/#/orders` "))
    assert "links: New order → `/#/orders/create`" in listing and "fields: Search" in listing
    form = next(l for l in lines if l.startswith("- `/#/orders/create` "))
    assert "fields: Customer, Status, Notes" in form and "actions: Create order" in form


def test_a_request_names_the_site_to_open():
    from qm_agent import is_exploration, task_url
    ask = "Explore https://marmelab.com/atomic-crm-demo/ and list its main pages. Follow links only."
    assert task_url(ask) == "https://marmelab.com/atomic-crm-demo/" and is_exploration(ask)
    assert task_url("Log in on (https://www.saucedemo.com).") == "https://www.saucedemo.com"
    assert not is_exploration("Explore the cart and write a test for removing an item")


def test_urls_reached_by_a_click_match_any_record_id():
    from qm_runtime import generic_url, url_pattern
    assert generic_url("/app/#/companies/55/show") == "/app/#/companies/:id/show"
    assert generic_url("/users/5f2b9c1e7a/edit?tab=2") == "/users/:id/edit?tab=2"
    assert generic_url("/inventory-item.html?id=4") == "/inventory-item.html?id=4"     # queries are kept
    pattern = url_pattern("/app/#/companies/:id/show")
    assert pattern.match("https://crm.test/app/#/companies/812/show")
    assert not pattern.match("https://crm.test/app/#/companies/812/edit")
    assert not url_pattern("/a/:identity").match("https://x.test/a/123")               # only whole segments


def test_planner_token_totals_survive_being_drained_between_turns():
    from types import SimpleNamespace as NS
    from qm_planner import Planner
    planner = Planner.__new__(Planner)
    planner.provider, planner.usage, planner.timings = NS(last_usage={"prompt_tokens": 10, "completion_tokens": 2}), {}, []
    planner.provider_name, planner.emit = "x", lambda e: None
    planner._account(0, None, False)
    assert planner.usage == {"input": 10, "output": 2}


# ── exact-or-ask matching (review of 2026-10-05) ────────────────────────────
from qm_ground import match_kind, words


def test_words_work_in_any_script():
    assert words("ग्राहक का नाम") == ["ग्राहक", "का", "नाम"]              # Devanagari marks stay in the word
    assert words("Créer une commande") == ["créer", "une", "commande"]
    assert words("Order #1002 — $15.99 (v2.6).") == ["order", "#1002", "$15.99", "v2.6"]
    assert words("❯Mark all as complete") == ["mark", "all", "as", "complete"]   # glyphs separate, never join


@pytest.mark.parametrize("target, label, role, kind", [
    ("Search", "Search", "textbox", "exact"),               # a label that is also a role word
    ("Clear search", "Clear search", "button", "exact"),
    ("Login button", "Login", "button", "exact"),           # the role named in the step
    ("Create transfer button", "New transfer", "link", "synonym"),   # links styled as buttons
    ("Cart", "Cart, 2 items", "button", "decorated"),       # counts and badges are decoration
    ("Inbox", "Inbox (3)", "link", "decorated"),
    ("Contacts tab", "1 contact", "tab", "decorated"),
    ("Contacts tab", "Contacts", "link", "partial"),        # ...but a tab is not a link
    ("New Contact", "Create contact", "link", "synonym"),
    ("Sign in", "Log in", "button", "synonym"),
    ("Ticket 150", "Ticket 1", "link", "partial"),          # numbers in the target are never decoration
    ("Combination Pliers", "Pliers", "checkbox", "partial"),
    ("Save", "Save and close", "button", "partial"),
    ("नया ऑर्डर बनाएं", "नया ऑर्डर बनाएं", "button", "exact"),
    ("Delete", "Archive", "button", None),
])
def test_only_a_real_name_match_counts(target, label, role, kind):
    assert match_kind(target, label, role)[0] == kind


TICKETS = '''- main [ref=m]:
''' + "".join(f'''  - link "Ticket {n}" [ref=t{n}] [cursor=pointer]:
    - /url: "#"
''' for n in (1, 2, 15))


def test_a_label_that_only_resembles_the_target_is_never_acted_on():
    """Regression: 'Ticket 150' (not rendered yet) clicked 'Ticket 1' -- substring score plus
    'links to the same place are one choice' (every link was href="#")."""
    elements, _ = parse(TICKETS)
    candidates = rank({"op": "click", "target": "Ticket 150"}, SimpleNamespace(elements=elements))
    assert candidates and not any(c.exact for c in candidates) and decide(candidates) is None
    assert decide(rank({"op": "click", "target": "Ticket 15"}, SimpleNamespace(elements=elements))).element.ref == "t15"


SEARCH_FORM = '''- main [ref=m]:
  - generic [ref=g1]: Search
  - textbox "Search" [ref=box]
  - button "Search" [ref=go] [cursor=pointer]
  - button "Clear search" [ref=clr] [cursor=pointer]
  - combobox "Sector" [ref=vis] [cursor=pointer]
  - combobox [aria-hidden] [ref=hid]
'''


def test_the_action_decides_between_equally_named_controls():
    elements, _ = parse(SEARCH_FORM)
    obs = SimpleNamespace(elements=elements)
    pick = lambda op, target: decide(rank({"op": op, "target": target}, obs))
    assert pick("fill", "Search").element.ref == "box"          # only a field can be filled
    assert pick("click", "Search").element.ref == "go"          # a button is the natural thing to click
    assert pick("click", "Clear search").element.ref == "clr"
    assert pick("select", "Sector").element.ref == "vis"        # the hidden native twin never competes


def test_within_must_be_fully_matched_and_dialogs_win():
    snap = '''- main [ref=m]:
  - region "Billing address" [ref=r1]:
    - button "Save" [ref=s1]
  - region "Shipping address" [ref=r2]:
    - button "Save" [ref=s2]
  - dialog [ref=d]:
    - heading "Confirm" [level=2] [ref=h]
    - button "Save" [ref=s3]
'''
    elements, _ = parse(snap)
    obs = SimpleNamespace(elements=elements)
    assert decide(rank({"op": "click", "target": "Save", "within": "Shipping address"}, obs)).element.ref == "s2"
    assert decide(rank({"op": "click", "target": "Save", "within": "Returns address"}, obs)) is None
    assert decide(rank({"op": "click", "target": "Save"}, obs)).element.ref == "s3"   # the open dialog is in front


# ── snapshot parsing and labels the snapshot lacks ───────────────────────────
from qm_observe import pick_label


def test_quoted_snapshot_lines_are_parsed_not_dropped():
    """Regression: Playwright quotes a line whose label has ': ', ' #', braces... -- product
    cards with prices and rows with statuses vanished from the page model."""
    snap = '''- generic [ref=e1]:
  - 'link "Combination Pliers CO₂: A B $14.15" [ref=e2] [cursor=pointer]':
    - /url: /product/01M45
    - heading "Combination Pliers" [level=5] [ref=e3]
  - 'button "It''s 5 o''clock: go" [ref=e4]'
  - 'row "Order #1002 Status: Paid" [ref=e5]':
    - cell "Paid" [ref=e6]
  - generic [ref=e7]: "+14"
  - textbox [ref=e8]: typed value
  - textbox "Email" [ref=e9]:
    - /placeholder: you@example.com
'''
    by = {e.ref: e for e in parse(snap)[0]}
    assert by["e2"].name == "Combination Pliers CO₂: A B $14.15" and by["e2"].url == "/product/01M45"
    assert by["e3"].parent is by["e2"]                       # children stay under the quoted parent
    assert by["e4"].name == "It's 5 o'clock: go"
    assert by["e6"].region("row") == ("row", "Order #1002 Status: Paid")
    assert by["e7"].inline == "+14"
    assert by["e8"].label == ""                              # a field's text is its value, not its name
    assert by["e9"].props == {"placeholder": "you@example.com"}


@pytest.mark.parametrize("info, expected", [
    ({"field": False, "text": "Add to cart", "icon": "cart-shopping"}, ("Add to cart", "text")),
    ({"field": False, "title": "Remove row", "icon": "trash"}, ("Remove row", "title")),
    ({"field": False, "icon": "pencil"}, ("edit", "icon")),
    ({"field": False, "icon": "fa-trash-can"}, ("delete", "icon")),
    ({"field": False, "testid": "edit-1041"}, ("edit", "id")),
    ({"field": False, "id": "btnSaveOrder"}, ("save order", "id")),
    ({"field": False, "id": "mat-input-338172"}, ("", "")),          # a generated id says nothing
    ({"field": True, "nearby": "SMS alerts"}, ("SMS alerts", "nearby")),
    ({"field": True, "placeholder": "Search orders", "nearby": "Filters"}, ("Search orders", "placeholder")),
    ({"field": True, "text": "current value"}, ("", "")),            # a field's own text is its value
])
def test_nameless_controls_are_named_from_what_the_page_says(info, expected):
    assert pick_label(info) == expected


# ── steps that follow their data ─────────────────────────────────────────────
from qm_steps import parameterize, usable_literals


def test_typed_values_are_followed_through_later_steps():
    literals = usable_literals({"vendor": "QA Vendor k3f9ab", "city": "Pune", "units": "12", "code": "V-77"},
                               always={"code"})
    assert [k for k, _ in literals] == ["vendor", "code"]          # distinctive values, and per-run unique ones
    click = parameterize({"op": "click", "target": 'page.get_by_role("link", name="QA Vendor k3f9ab")', "name": "x"}, literals)
    assert render(click) == 'flow.click(page.get_by_role("link", name=tc_data["vendor"]), "x")'
    row = parameterize({"op": "click", "name": "Edit", "target":
                        'page.get_by_role("row").filter(has_text="QA Vendor k3f9ab").get_by_role("link", name="Edit")'}, literals)
    assert 'filter(has_text=tc_data["vendor"])' in row["target"]
    whole = parameterize({"op": "expect_page_text", "value": "QA Vendor k3f9ab", "name": "n"}, literals)
    part = parameterize({"op": "expect_page_text", "value": "Saved V-77 for QA Vendor k3f9ab.", "name": "n"}, literals)
    assert render(whole) == 'flow.expect_page_text(tc_data["vendor"], True, "n")'
    assert render(part) == 'flow.expect_page_text("Saved " + tc_data["code"] + " for " + tc_data["vendor"] + ".", True, "n")'
    untouched = parameterize({"op": "expect_page_text", "value": "City: Pune, Units: 12, VV-770", "name": "n"}, literals)
    assert "value_expr" not in untouched and "data_key" not in untouched     # short words, numbers, parts of words


def test_new_step_options_render_and_stay_backwards_compatible():
    assert render({"op": "click", "target": "page.locator('#a')", "name": "Archive", "dialog": "accept"}) == \
        "flow.click(page.locator('#a'), \"Archive\", dialog=\"accept\")"
    assert render({"op": "click", "target": "page.locator('#a')", "name": "Open", "new_tab": True,
                   "expect_url": "/report"}) == 'flow.click(page.locator(\'#a\'), "Open", expect_url="/report", new_tab=True)'
    assert render({"op": "expect_page_text", "value": "Ready", "name": "n", "timeout": 20}) == \
        'flow.expect_page_text("Ready", True, "n", timeout=20)'
    assert render({"op": "expect_text", "target": "page.locator('#r')", "value": "Paid", "name": "n", "present": False}) == \
        "flow.expect_text(page.locator('#r'), \"Paid\", \"n\", present=False)"
    assert render({"op": "close_tab", "name": "Close the tab"}) == 'flow.close_tab("Close the tab")'


def test_use_data_resolves_unique_and_secret_values(tmp_path, monkeypatch):
    from qm_runtime import Flow, StepError
    flow = Flow.__new__(Flow)
    flow.unique, flow.secrets = "k3f9ab", {"admin_password": "from-memory"}
    data = {"name": "QA Vendor {unique}", "again": "QA Vendor {unique}", "password": "{secret:admin_password}", "n": 3}
    assert flow.use_data(data) == {"name": "QA Vendor k3f9ab", "again": "QA Vendor k3f9ab",
                                   "password": "from-memory", "n": 3}
    flow.secrets = {}
    (tmp_path / "secrets.local.json").write_text('{"admin_password": "from-file"}')
    test_file = tmp_path / "flows" / "vendors" / "test_vendors.py"
    assert flow.use_data(data, str(test_file))["password"] == "from-file"
    monkeypatch.setenv("QAMATE_SECRET_ADMIN_PASSWORD", "from-env")
    assert flow.use_data(data, str(test_file))["password"] == "from-env"
    monkeypatch.delenv("QAMATE_SECRET_ADMIN_PASSWORD")
    with pytest.raises(StepError, match="secret 'other' is not set"):
        flow.use_data({"x": "{secret:other}"}, str(test_file))


def test_secrets_are_written_beside_the_tests_and_ignored_by_git(tmp_path):
    from qm_testgen import write_test
    steps = [{"op": "goto", "value": "/", "name": "Open"},
             {"op": "fill", "target": 'page.get_by_role("textbox", name="Password")', "name": "Password",
              "value": "hunter2!", "data_key": "password", "secret": True}]
    path = write_test(str(tmp_path), "login", "TC-LOGIN-001", steps, {"password": "{secret:password}"},
                      secrets={"password": "hunter2!"})
    files = {p.name: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    assert "hunter2!" not in files["test_login.py"] + files["test_data.json"] + files["test_cases.json"]
    assert json.loads(files["secrets.local.json"]) == {"password": "hunter2!"}
    assert "secrets.local.json" in files[".gitignore"]
    assert "tc_data = flow.use_data(tc_data, __file__)" in open(path).read()
    # A second test with another password under the same name gets its own entry.
    write_test(str(tmp_path), "login", "TC-LOGIN-002", steps, {"password": "{secret:password}"},
               secrets={"password": "other-one"})
    assert json.loads((tmp_path / "secrets.local.json").read_text()) == {"password": "hunter2!", "password_2": "other-one"}
    data = json.loads((tmp_path / "flows" / "login" / "test_data.json").read_text())
    assert data["TC-LOGIN-002"] == {"password": "{secret:password_2}"} and data["TC-LOGIN-001"] == {"password": "{secret:password}"}


def test_a_done_pseudo_step_ends_the_plan_instead_of_failing_it():
    """Regression (live run): the planner wrote {"do": "done"} as a step; it was treated as an
    unknown step and cost a second planner call."""
    from qm_planner import StepScanner, normalize_plan
    plan = normalize_plan({"steps": [{"do": "click", "target": "Save"}, {"do": "done"}],
                           "test": {"flow": "<app area this test covers, one or two words taken from the task>"}})
    assert [s["do"] for s in plan["steps"]] == ["click"] and plan["done"] is True and plan["test"]["flow"] == "agent"
    assert [s["do"] for s in StepScanner().feed('{"steps": [{"do": "click", "target": "Save"}, {"do": "done"}]}')] == ["click"]


def test_real_names_recovered_from_the_page_come_first():
    """Playwright's AI snapshot leaves out names its default snapshot has (a button with an
    icon element inside; anything under a stray aria-hidden). The author's own name wins
    over every guess."""
    assert pick_label({"field": True, "named": "Name", "nearby": "Personal details"}) == ("Name", "name")
    assert pick_label({"field": False, "named": "Choose date", "icon": "calendar"}) == ("Choose date", "name")
    assert pick_label({"field": False, "named": "", "text": "Add to cart"}) == ("Add to cart", "text")


def test_a_dropdown_named_with_its_current_value_keeps_only_its_name():
    by = {e.ref: e for e in parse('''- generic [ref=e1]:
  - combobox "Department Marketing" [ref=e2]: Marketing
  - combobox "Marketing" [ref=e3]: Marketing
''')[0]}
    assert by["e2"].label == "Department" and by["e3"].label == "Marketing"


def test_typing_into_a_segmented_field_is_one_step():
    step = {"op": "type", "target": 'page.get_by_role("group", name="Join date")', "name": "Join date",
            "value": "10/05/2026", "data_key": "join_date"}
    assert render(step) == 'flow.type(page.get_by_role("group", name="Join date"), tc_data["join_date"], "Join date")'
