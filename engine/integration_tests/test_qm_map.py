"""Quick scan: learns an app's pages by following plain links only -- navigation and
"New ..." links -- never logout/delete/export links or other sites, and ends where it began."""
import http.server
import os
import sys
import threading

import pytest
from playwright.sync_api import sync_playwright

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
from qm_map import PageMap, quick_scan

NAV = '''<nav><a href="/orders.html">Orders</a> <a href="/customers.html">Customers</a>
<a href="/logout">Log out</a> <a href="https://example.com/docs">Docs</a></nav>'''
PAGES = {
    "/index.html": NAV + '<main><h1>Home</h1><a href="/delete-all">Delete all</a></main>',
    "/orders.html": NAV + '''<main><h1>Orders</h1><a href="/orders/new.html">New order</a>
        <a href="/export.csv">Export CSV</a><input aria-label="Search"></main>''',
    "/orders/new.html": NAV + '''<main><h1>New order</h1><label>Customer <input></label>
        <label>Status <select><option>Open</option><option>Paid</option></select></label>
        <button>Create order</button></main>''',
    "/customers.html": NAV + '<main><h1>Customers</h1><a href="/customers/new.html">Add customer</a></main>',
    "/customers/new.html": NAV + '<main><h1>Add customer</h1><label>Name <input></label><button>Save</button></main>',
}


@pytest.fixture(scope="module")
def site():
    requested = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            requested.append(self.path)
            body = PAGES.get(self.path)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write((f"<title>Shop</title>{body}" if body else "gone").encode())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", requested
    httpd.shutdown()


def test_quick_scan_learns_forms_through_links_only_and_returns(site, tmp_path):
    base, requested = site
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index.html")
        page_map = PageMap(str(tmp_path / "page_map.json"))
        result = quick_scan(page, page_map)
        assert page.url == base + "/index.html"
        browser.close()
    assert set(page_map.pages) >= {"/orders.html", "/orders/new.html", "/customers.html", "/customers/new.html"}
    assert not {"/logout", "/delete-all", "/export.csv"} & set(requested)
    assert all(not p.startswith("http") for p in requested)       # never left the app's origin
    form = page_map.pages["/orders/new.html"]["controls"]
    assert 'textbox "Customer"' in form and 'combobox "Status"' in form and 'button "Create order"' in form
    assert {"from": "/orders.html", "label": "New order", "to": "/orders/new.html"} in page_map.moves
    assert result["pages"] == 5 and os.path.exists(tmp_path / "page_map.json")


def test_explore_from_a_blank_browser_opens_the_site_named_in_the_request(site, tmp_path):
    """No project (the browser starts blank): "Explore <url>" maps that site, with no model call."""
    from qm_agent import FastAgent, summary_text

    class NoPlanner:
        def plan(self, *a, **k):
            raise AssertionError("exploring must not call the planner")
    base, requested = site
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        import qm_agent
        original = qm_agent.Planner
        qm_agent.Planner = lambda *a, **k: NoPlanner()
        try:
            agent = FastAgent(page, browser, {}, base_url=None, tests_root=str(tmp_path / "tests"))
            result = agent.run_task(f"Explore {base}/index.html and list its pages and forms. Follow links only.")
        finally:
            qm_agent.Planner = original
        assert page.url == base + "/index.html"
        browser.close()
    reply = summary_text(result)
    assert result["explored"]["pages"] == 5 and result["saved"] is None
    assert "fields: Customer, Status" in reply and "New order → `/orders/new.html`" in reply
    assert len(agent.page_map) == 5                       # kept for the next request in this chat
    assert not {"/logout", "/delete-all", "/export.csv"} & set(requested)


def test_exploring_another_site_leaves_the_projects_memory_alone(site, tmp_path):
    from qm_agent import FastAgent
    import qm_agent
    base, _ = site
    map_path = tmp_path / "page_map.json"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        original = qm_agent.Planner
        qm_agent.Planner = lambda *a, **k: type("NoPlanner", (), {"timings": []})()
        try:
            agent = FastAgent(page, browser, {}, base_url="https://project-app.test/", tests_root=str(tmp_path / "t"),
                              page_map=PageMap(str(map_path)))
            result = agent.run_task(f"Explore {base}/index.html")
        finally:
            qm_agent.Planner = original
        browser.close()
    assert result["explored"]["pages"] == 5 and not map_path.exists() and len(agent.page_map) == 0


def test_explore_without_a_site_asks_which_one(tmp_path):
    from qm_agent import FastAgent, summary_text
    import qm_agent
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        original = qm_agent.Planner
        qm_agent.Planner = lambda *a, **k: type("NoPlanner", (), {"timings": []})()
        try:
            agent = FastAgent(page, browser, {}, base_url=None, tests_root=str(tmp_path / "t"))
            result = agent.run_task("Explore the app and list its main user flows, with the pages each flow touches.")
        finally:
            qm_agent.Planner = original
        browser.close()
    assert summary_text(result).startswith("Which site should I explore?")


# ── an "internal tool": navigation and content that only appear after opening things ──
TOOL_NAV = '''<nav aria-label="Main"><a href="/tool/index.html">Dashboard</a> <a href="/docs/index.html">Docs portal</a>
<button id="rep" aria-expanded="false" aria-controls="replinks" onclick="toggle(this)">Reports</button>
<ul id="replinks" hidden><li><a href="/tool/reports/sales.html">Sales report</a></li>
<li><a href="/tool/reports/stock.html">Stock report</a></li></ul>
<button aria-label="Open navigation" aria-expanded="false" aria-controls="more" onclick="toggle(this)">&#9776;</button>
<div id="more" hidden><a href="/tool/settings.html">Settings</a></div></nav>
<script>function toggle(b){const t=document.getElementById(b.getAttribute('aria-controls'));
const open=b.getAttribute('aria-expanded')!=='true';b.setAttribute('aria-expanded',open);t.hidden=!open;}</script>'''
TOOL = {
    "/tool/index.html": TOOL_NAV + '''<main><h1>Orders</h1>
<button id="act" aria-haspopup="menu" aria-expanded="false" onclick="toggle(this)" aria-controls="actmenu">Actions</button>
<div id="actmenu" role="menu" hidden><button role="menuitem" onclick="fetch('/tool/export')">Export all</button>
<a role="menuitem" href="/tool/archive.html">Archived orders</a></div>
<details><summary>Advanced filters</summary><label>Region <input></label></details>
<div role="tablist"><button role="tab" aria-selected="true" onclick="tab(0)">Overview</button>
<button role="tab" aria-selected="false" onclick="tab(1)">History</button></div>
<section id="p0"><p>12 open orders</p></section>
<section id="p1" hidden><a href="/tool/audit.html">Audit log</a><button onclick="fetch('/tool/clear')">Clear history</button></section>
<form action="/tool/submitted"><label>Note <input name="n"></label><button aria-expanded="false">More options</button></form>
<button aria-haspopup="dialog" onclick="fetch('/tool/delete')">Delete everything</button>
<button aria-pressed="false" onclick="fetch('/tool/theme')">Toggle theme</button>
<script>function tab(i){document.querySelectorAll('[role=tab]').forEach((t,j)=>t.setAttribute('aria-selected',i===j));
document.getElementById('p0').hidden=i!==0;document.getElementById('p1').hidden=i!==1;}</script></main>''',
    "/tool/reports/sales.html": TOOL_NAV + '''<main><h1>Sales</h1><div id="slot">Loading…</div>
<script>fetch('/tool/api/slow').then(r=>r.text()).then(()=>{document.getElementById('slot').innerHTML=
'<label>Sales filter <input></label><button>Show figures</button>';});</script></main>''',
    "/tool/reports/stock.html": TOOL_NAV + '<main><h1>Stock</h1><label>Warehouse <input></label></main>',
    "/tool/archive.html": TOOL_NAV + '<main><h1>Archive</h1><label>Archived search <input></label></main>',
    "/tool/settings.html": TOOL_NAV + '<main><h1>Settings</h1><label>Display name <input></label></main>',
    "/tool/audit.html": TOOL_NAV + '<main><h1>Audit</h1><label>Audit filter <input></label></main>',
}


@pytest.fixture(scope="module")
def tool():
    requested = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            requested.append(self.path)
            if self.path == "/tool/api/slow":
                import time
                time.sleep(2.5)    # a slow internal API
            body = PAGES_TOOL.get(self.path.split("?")[0], "ok" if self.path.startswith("/tool/api/") else None)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write((f"<title>Ops tool</title>{body}" if body else "gone").encode())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", requested
    httpd.shutdown()


PAGES_TOOL = {**TOOL, "/docs/index.html": "<h1>Another app on the same host</h1><a href='/docs/a.html'>A</a>"}
NEVER = {"/tool/export", "/tool/delete", "/tool/clear", "/tool/theme", "/tool/logout", "/docs/index.html"}


def _scan_tool(tool, **kw):
    base, requested = tool
    requested.clear()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/tool/index.html")
        page_map = kw.pop("page_map", None) or PageMap()
        result = quick_scan(page, page_map, **kw)
        end = page.url
        browser.close()
    return page_map, result, end, list(requested)


def test_scan_opens_menus_tabs_and_sections_and_follows_what_they_reveal(tool):
    page_map, result, end, requested = _scan_tool(tool, max_pages=20, max_seconds=60, max_depth=3)
    hidden_pages = {"/tool/reports/sales.html", "/tool/reports/stock.html", "/tool/archive.html",
                    "/tool/settings.html", "/tool/audit.html"}
    assert hidden_pages <= set(page_map.pages), sorted(page_map.pages)
    views = page_map.pages["/tool/index.html"]["views"]
    assert 'link "Sales report"' in views["section: Reports"]
    assert 'menuitem "Export all"' in views["menu: Actions"] and 'menuitem "Archived orders"' in views["menu: Actions"]
    assert 'textbox "Region"' in views["section: Advanced filters"]
    assert 'link "Audit log"' in views["tab: History"]
    assert any(v.startswith("section: Open navigation") for v in views)
    # A slow page was read after its data arrived.
    assert 'textbox "Sales filter"' in page_map.pages["/tool/reports/sales.html"]["controls"]
    # Nothing that acts was ever triggered: no export/delete/clear/theme, no form submission.
    assert not NEVER & set(requested) and not any(p.startswith("/tool/submitted") for p in requested)
    assert end.endswith("/tool/index.html") and result["views"] >= 5


def test_links_only_scan_clicks_nothing_but_still_finds_collapsed_navigation(tool):
    page_map, result, _, requested = _scan_tool(tool, max_pages=20, max_seconds=30, reveal=False)
    # Links that are in the page but collapsed (nav groups, menus) are plain addresses: followed.
    assert {"/tool/reports/sales.html", "/tool/settings.html", "/tool/archive.html"} <= set(page_map.pages)
    # Nothing was opened, so what only a tab shows stays unseen.
    assert "/tool/audit.html" not in page_map.pages and result["views"] == 0
    assert not any(p.get("views") for p in page_map.pages.values()) and not NEVER & set(requested)


def test_explore_more_continues_where_the_last_scan_stopped(tool, tmp_path):
    path = str(tmp_path / "page_map.json")
    first_map, first, _, _ = _scan_tool(tool, page_map=PageMap(path), max_pages=3, max_seconds=60, max_depth=3)
    assert first["queued"] > 0 and len(first_map) == 3
    second_map, second, _, _ = _scan_tool(tool, page_map=PageMap(path), max_pages=20, max_seconds=60, max_depth=3)
    assert second["new"] >= 3 and len(second_map) >= 6
