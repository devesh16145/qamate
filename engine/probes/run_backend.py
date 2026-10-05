#!/usr/bin/env python
"""An app whose data lives on the SERVER (like any real internal tool): what happens to the
agent's replay-before-save? Scripted planner, no models.

    venv/bin/python engine/probes/run_backend.py

The benchmark apps all keep their data in the browser, so a fresh replay context starts
clean. Here the record created while authoring is still there when the replay runs.
"""
import http.server, json, os, sys, tempfile, threading
ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE); sys.path.insert(0, os.path.join(ENGINE, 'integration_tests'))
from playwright.sync_api import sync_playwright
import qm_agent
from qm_agent import FastAgent, summary_text
from qm_verify import replay
from test_qm_agent import ScriptedPlanner

PAGE = '''<!doctype html><meta charset="utf-8"><title>Vendors</title><h1>Vendor register</h1>
<nav><a href="#/vendors">Vendors</a> <a href="#/vendors/new">New vendor</a></nav><main></main>
<script>
const main = document.querySelector('main');
async function render() {
  const path = location.hash.slice(1) || '/vendors';
  const vendors = await (await fetch('/api/vendors')).json();
  if (path === '/vendors') {
    main.innerHTML = '<h2>Vendors</h2><label>Search vendors <input type="search"></label><div id="rows"></div>';
    const input = main.querySelector('input');
    const draw = () => { document.getElementById('rows').innerHTML = vendors.filter(v => v.name.includes(input.value))
      .map(v => `<p><a href="#/vendors/${v.id}">${v.name}</a> — ${v.city}</p>`).join('') || '<p>No vendors found</p>'; };
    input.oninput = draw; draw();
  } else if (path === '/vendors/new') {
    main.innerHTML = '<h2>New vendor</h2><form><label>Vendor name <input name="name"></label><label>City <input name="city"></label><button>Create vendor</button><p role="alert"></p></form>';
    main.querySelector('form').onsubmit = async (e) => { e.preventDefault(); const f = e.target;
      const r = await fetch('/api/vendors', {method: 'POST', body: JSON.stringify({name: f.name.value, city: f.city.value})});
      const d = await r.json(); if (d.error) main.querySelector('[role=alert]').textContent = d.error; else location.hash = '/vendors/' + d.id; };
  } else { const v = vendors.find(x => String(x.id) === path.split('/')[2]);
    main.innerHTML = v ? `<h2>Vendor details</h2><h3>${v.name}</h3><p>City: ${v.city}</p><a href="#/vendors">Back to vendors</a>` : '<h2>Not found</h2>'; }
}
window.onhashchange = render; render();
</script>'''

def serve(unique):
    vendors = [{"id": 1, "name": "Acme Supplies", "city": "Delhi"}]
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, body, mime='application/json'):
            self.send_response(200); self.send_header('Content-Type', mime); self.end_headers(); self.wfile.write(body.encode())
        def do_GET(self):
            self._send(json.dumps(vendors)) if self.path == '/api/vendors' else self._send(PAGE, 'text/html; charset=utf-8')
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if unique and any(v['name'] == data['name'] for v in vendors):
                return self._send(json.dumps({"error": "A vendor with this name already exists"}))
            vendors.append({"id": len(vendors) + 1, **data}); self._send(json.dumps({"id": len(vendors)}))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, vendors

CREATE = [{"do": "click", "target": "New vendor"}, {"do": "fill", "target": "Vendor name", "value": "QA Vendor 01"},
          {"do": "fill", "target": "City", "value": "Pune"}, {"do": "click", "target": "Create vendor"},
          {"do": "expect_text", "value": "QA Vendor 01"}, {"do": "expect_text", "value": "City: Pune"}]
REOPEN = [{"do": "click", "target": "Back to vendors"}, {"do": "fill", "target": "Search vendors", "value": "QA Vendor 01"},
          {"do": "click", "target": "QA Vendor 01"}, {"do": "expect_text", "value": "City: Pune"}]
SCENARIOS = [("A. names must be unique; test: create + check details", True, CREATE),
             ("B. duplicates allowed; test: create + search + reopen", False, CREATE + REOPEN),
             ("C. duplicates allowed; test: create + check details only", False, CREATE)]
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for title, unique, plan in SCENARIOS:
        srv, vendors = serve(unique)
        base = f'http://127.0.0.1:{srv.server_port}/'
        scripted = ScriptedPlanner([{"steps": plan, "done": True, "test": {"flow": "vendors", "title": "Create a vendor"}}])
        qm_agent.Planner = lambda *a, **k: scripted
        page = b.new_page(); page.goto(base)
        agent = FastAgent(page, b, {}, base_url=base, tests_root=tempfile.mkdtemp(), scan="off")
        result = agent.run_task("Create vendor QA Vendor 01 in Pune and check it")
        print(f"\n{title}")
        print("  agent's own replay:", "passed" if result['replay'] and result['replay']['ok'] else f"FAILED at {(result['replay'] or {}).get('failed_step', {}).get('name')!r}: {str((result['replay'] or {}).get('failed_step', {}).get('error'))[:140]}")
        print("  saved:", bool(result['saved']), "| vendors named 'QA Vendor 01' on the server now:", sum(v['name'] == 'QA Vendor 01' for v in vendors))
        page.close(); srv.shutdown()
    b.close()
