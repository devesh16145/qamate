"""Fast executor (qm_explorer) with scripted plain-language steps -- no model needed
except where a stub stands in for Jev."""
import functools
import http.server
import os
import sys
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
import qm_explorer
from qm_explorer import Explorer
from qm_steps import render
from qm_verify import replay

PLAIN = """<nav><a href="#orders">Orders</a><a href="#customers">Customers</a></nav>
<main><h1>Orders</h1>
<table><tr><th>Order</th><th>Status</th><th></th></tr>
<tr><td>#1001</td><td>Pending</td><td><button onclick="document.querySelector('output').textContent='Editing 1001'">Edit</button></td></tr>
<tr><td>#1002</td><td>Shipped</td><td><button onclick="document.querySelector('output').textContent='Editing 1002'">Edit</button></td></tr></table>
<div class="card"><span>Plan A</span><button onclick="document.querySelector('output').textContent='Plan A chosen'">Choose</button></div>
<div class="card"><span>Plan B</span><button onclick="document.querySelector('output').textContent='Plan B chosen'">Choose</button></div>
<button onclick="document.querySelector('output').textContent='Order deleted'">Delete order</button>
<div class="dd"><button role="combobox" aria-expanded="false" aria-controls="lb" aria-label="Priority"
  onclick="document.getElementById('lb').hidden=false;this.setAttribute('aria-expanded','true')">Normal</button>
<ul role="listbox" id="lb" hidden><li role="option" onclick="pick(this)">Low</li><li role="option" onclick="pick(this)">High</li></ul></div>
<output></output></main>
<script>function pick(li){const b=document.querySelector('[aria-label=Priority]');b.textContent=li.textContent;
document.getElementById('lb').hidden=true;b.setAttribute('aria-expanded','false');}</script>"""


@pytest.fixture(scope="module")
def server():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=os.path.join(ENGINE, "fixtures")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    p = context.new_page()
    yield p
    context.close()


def test_plain_language_flow_needs_no_model_and_replays(page, server, browser):
    ex = Explorer(page, base_url=server)
    started = time.monotonic()
    result = ex.run([
        {"do": "goto", "url": "operations.html#/transfers"},
        {"do": "click", "target": "New transfer"},
        {"do": "fill", "target": "Transfer name", "value": "Batch 9"},
        {"do": "select", "target": "Region dropdown", "value": "South"},
        {"do": "select", "target": "Destination", "value": "Kochi"},
        {"do": "fill", "target": "Units field", "value": "12"},
        {"do": "click", "target": "Choose service button"},
        {"do": "click", "target": "Express", "within": "Service options"},
        {"do": "click", "target": "Save transfer"},
        {"do": "expect_page_text", "value": "Destination: Kochi"},
    ])
    elapsed = time.monotonic() - started
    assert result["ok"], result["stopped"]
    assert [s["op"] for s in ex.steps].count("fill") == 2 and ex.data == {"transfer_name": "Batch 9", "units_field": "12"}
    assert ex.steps[-2]["expect_url"].startswith("/operations.html#/transfers/")
    assert elapsed < 15, elapsed
    assert replay(browser, ex.steps, ex.data, base_url=server)["ok"]


def test_repeated_controls_are_told_apart_by_their_item(page):
    page.set_content(PLAIN)
    ex = Explorer(page)
    assert ex.run([{"do": "click", "target": "Edit", "within": "#1002"}])["ok"]
    assert page.locator("output").inner_text() == "Editing 1002"
    assert ex.run([{"do": "click", "target": "Choose", "within": "Plan B"}])["ok"]
    assert page.locator("output").inner_text() == "Plan B chosen"
    assert ex.steps[0]["target"] == 'page.get_by_role("row", name="#1002 Shipped Edit").get_by_role("button")'


def test_ambiguous_step_without_a_decision_model_stops_with_candidates(page):
    page.set_content(PLAIN)
    out = Explorer(page).run_intent({"do": "click", "target": "Edit"})
    assert not out["ok"] and out["reason"] == "ambiguous"
    assert len(out["candidates"]) == 2 and all("Edit" in c for c in out["candidates"])


def test_decision_model_resolves_ambiguity(page, monkeypatch):
    page.set_content(PLAIN)
    asked = {}

    def fake_choose(config, step, candidates, **kw):
        asked["options"] = [c.element.summary() for c in candidates]
        return {"index": next(i for i, c in enumerate(candidates) if "#1001" in c.element.summary()),
                "confidence": 0.93, "by": "jev"}
    monkeypatch.setattr(qm_explorer, "choose", fake_choose)
    out = Explorer(page).run_intent({"do": "click", "target": "Edit", "label": "Edit the pending order"})
    assert out["ok"] and out["how"] == "jev" and out["confidence"] == 0.93
    assert page.locator("output").inner_text() == "Editing 1001"
    assert len(asked["options"]) == 2   # a short list, not the page


def test_low_confidence_choice_is_not_acted_on(page, monkeypatch):
    page.set_content(PLAIN)
    monkeypatch.setattr(qm_explorer, "choose", lambda *a, **k: {"index": 0, "confidence": 0.41, "by": "jev"})
    out = Explorer(page).run_intent({"do": "click", "target": "Edit"})
    assert not out["ok"] and out["reason"] == "ambiguous" and page.locator("output").inner_text() == ""


def test_destructive_actions_need_confirmation(page):
    page.set_content(PLAIN)
    out = Explorer(page).run_intent({"do": "click", "target": "Delete order"})
    assert not out["ok"] and out["reason"] == "needs_confirmation" and page.locator("output").inner_text() == ""
    confirmed = Explorer(page, confirm=lambda intent, element: True).run_intent({"do": "click", "target": "Delete order"})
    assert confirmed["ok"] and page.locator("output").inner_text() == "Order deleted"


def test_missing_element_and_false_check_are_reported_not_recorded(page):
    page.set_content(PLAIN)
    ex = Explorer(page, check_timeout_ms=800)
    assert ex.run_intent({"do": "click", "target": "Archive"})["reason"] in ("not_found", "ambiguous")
    out = ex.run_intent({"do": "expect_page_text", "value": "Order shipped"})
    assert not out["ok"] and out["reason"] == "check_failed"
    assert ex.steps == []


def test_repeating_the_same_action_is_stopped(page):
    page.set_content(PLAIN)
    ex = Explorer(page)
    # The first click changes the page ("Plan A chosen"); the next two change nothing.
    for _ in range(3):
        assert ex.run_intent({"do": "click", "target": "Choose", "within": "Plan A"})["ok"]
    stuck = ex.run_intent({"do": "click", "target": "Choose", "within": "Plan A"})
    assert not stuck["ok"] and stuck["reason"] == "repeating"


def test_repeating_an_action_that_changes_the_page_is_fine(page, server):
    """Regression (probe 2026-10-05): the loop guard refused a third 'Next page'."""
    ex = Explorer(page, base_url=server)
    result = ex.run([{"do": "goto", "url": "operations.html#/inventory"}] +
                    [{"do": "click", "target": "Next page"}] * 3 +
                    [{"do": "expect_page_text", "value": "Page 4 of 4"}, {"do": "click", "target": "Batch 024"},
                     {"do": "expect_page_text", "value": "Available units: 24"}])
    assert result["ok"], result["stopped"]
    assert [s["op"] for s in ex.steps].count("click") == 4


def test_custom_dropdown_select_records_open_and_pick(page, browser):
    page.set_content(PLAIN)
    ex = Explorer(page)
    out = ex.run_intent({"do": "select", "target": "Priority", "value": "High"})
    assert out["ok"], out
    assert page.get_by_role("combobox", name="Priority").inner_text() == "High"
    assert [s["op"] for s in ex.steps] == ["click", "click"]


@pytest.mark.skipif(os.environ.get("QM_OFFLINE") == "1", reason="needs internet")
def test_saucedemo_cart_with_plain_steps(page, browser):
    ex = Explorer(page, base_url="https://www.saucedemo.com/")
    started = time.monotonic()
    result = ex.run([
        {"do": "goto", "url": "https://www.saucedemo.com/"},
        {"do": "fill", "target": "Username", "value": "standard_user"},
        {"do": "fill", "target": "Password", "value": "secret_sauce"},
        {"do": "click", "target": "Login"},
        {"do": "click", "target": "Add to cart", "within": "Sauce Labs Bike Light"},
        {"do": "click", "target": "Add to cart", "within": "Sauce Labs Onesie"},
        {"do": "expect_page_text", "value": "Remove"},
    ])
    elapsed = time.monotonic() - started
    assert result["ok"], result["stopped"]
    assert 'bike-light' in ex.steps[4]["target"] and 'onesie' in ex.steps[5]["target"]
    print(f"\nSauceDemo login + 2 cart adds + check: {elapsed:.2f}s")
    # The password never reaches the test data: it is a reference, the value lives apart.
    assert ex.data == {"username": "standard_user", "password": "{secret:password}"}
    assert ex.secrets == {"password": "secret_sauce"}
    assert replay(browser, ex.steps, ex.data, base_url="https://www.saucedemo.com/", secrets=ex.secrets)["ok"]
    missing = replay(browser, ex.steps, ex.data, base_url="https://www.saucedemo.com/")
    assert not missing["ok"] and "secret 'password' is not set" in missing["failed_step"]["error"]


# ── real-app behaviour (review of 2026-10-05) ───────────────────────────────
@pytest.fixture(scope="module")
def patterns():
    """engine/probes/patterns.html: one small page per common web pattern."""
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=os.path.join(ENGINE, "probes")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/patterns.html?p="
    httpd.shutdown()


def test_checks_that_verify_no_change_are_flagged(page, patterns, browser):
    """The live-run case: after searching, "Industrials" is on the page whatever happens --
    a filter button says so, and the record showed it before the search too. What the search
    changed is which records are listed; only those checks verify it."""
    ex = Explorer(page)
    result = ex.run([
        {"do": "goto", "url": patterns + "records"},
        {"do": "fill", "target": "Search", "value": "Acme"},
        {"do": "expect_page_text", "value": "Industrials"},                              # there anyway
        {"do": "expect_page_text", "value": "Industrials", "within": "Acme Rockets"},    # true before the search too
        {"do": "expect_page_text", "value": "Volt Grid", "present": False},              # the search's effect
        {"do": "expect_page_text", "value": "Quantum Labs", "present": False},           # never there
        {"do": "fill", "target": "Search", "value": "zzz"},
        {"do": "expect_page_text", "value": "No companies found"},                       # an effect
    ])
    assert result["ok"], result["stopped"]
    anywhere, scoped, gone, never, _, empty = ex.steps[2:8]
    assert "already on the page" in anywhere["weak"] and "already showed this" in scoped["weak"]
    assert "never on the page" in never["weak"]
    assert "weak" not in gone and "weak" not in empty
    # Saying where the text belongs gives a check tied to that record, not to the page.
    assert scoped["op"] == "expect_text" and scoped["target"] == 'page.get_by_role("article").filter(has_text="Acme Rockets")'
    assert sum("verifies no change" in note for note in ex.notes) == 3
    assert replay(browser, ex.steps, ex.data)["ok"]
    wrong = ex.run_intent({"do": "expect_page_text", "value": "Energy", "within": "Acme Rockets"})
    assert not wrong["ok"] and wrong["reason"] in ("check_failed", "not_found")


def test_an_irreversible_question_from_the_app_needs_the_users_yes(page, patterns, browser):
    asked = []
    ex = Explorer(page)                                    # nobody to ask
    ex.run_intent({"do": "goto", "url": patterns + "danger"})
    refused = ex.run_intent({"do": "click", "target": "Clean up"})
    assert not refused["ok"] and refused["reason"] == "needs_confirmation" and "Delete all archived reports?" in refused["detail"]
    assert page.locator("#out").inner_text() == "Archive kept" and len(ex.steps) == 1     # answered Cancel, not recorded
    ex = Explorer(page, confirm=lambda intent, what: asked.append(what) or True)
    ex.run_intent({"do": "goto", "url": patterns + "danger"})
    done = ex.run_intent({"do": "click", "target": "Clean up"})
    assert done["ok"] and page.locator("#out").inner_text() == "Archive emptied"
    assert "Delete all archived reports?" in asked[0] and ex.steps[-1]["dialog"] == "accept"
    assert 'dialog="accept"' in render(ex.steps[-1])
    assert replay(browser, ex.steps, ex.data)["ok"]


def test_a_link_that_opens_a_new_tab_is_followed_and_closed(page, patterns, browser):
    from qm_testgen import build_function
    ex = Explorer(page)
    result = ex.run([
        {"do": "goto", "url": patterns + "newtab"},
        {"do": "click", "target": "Open report"},
        {"do": "expect_page_text", "value": "Revenue up 12%"},
        {"do": "close_tab"},
        {"do": "expect_visible", "target": "Open report"},
    ])
    assert result["ok"], result["stopped"]
    assert ex.steps[1]["new_tab"] is True and ex.steps[1]["expect_url"].endswith("?p=report")
    assert ex.page is page and len(page.context.pages) == 1      # back on the first tab, the other one closed
    source = build_function("TC-TABS-001", ex.steps)
    assert source.count("    page = flow.page") == 2             # after the click and after close_tab
    assert replay(browser, ex.steps, ex.data)["ok"]
    ex.cleanup()
