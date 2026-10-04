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
    for _ in range(2):
        assert ex.run_intent({"do": "click", "target": "Choose", "within": "Plan A"})["ok"]
    third = ex.run_intent({"do": "click", "target": "Choose", "within": "Plan A"})
    assert not third["ok"] and third["reason"] == "repeating"


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
    assert replay(browser, ex.steps, ex.data, base_url="https://www.saucedemo.com/")["ok"]
