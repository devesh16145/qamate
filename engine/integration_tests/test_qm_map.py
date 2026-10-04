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
