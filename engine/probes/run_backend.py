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

def plan(name):
    create = [{"do": "click", "target": "New vendor"}, {"do": "fill", "target": "Vendor name", "value": name},
              {"do": "fill", "target": "City", "value": "Pune"}, {"do": "click", "target": "Create vendor"},
              {"do": "expect_text", "value": name}, {"do": "expect_text", "value": "City: Pune"}]
    reopen = [{"do": "click", "target": "Back to vendors"}, {"do": "fill", "target": "Search vendors", "value": name},
              {"do": "click", "target": name}, {"do": "expect_text", "value": "City: Pune"}]
    return create, create + reopen


FIXED, UNIQUE = "QA Vendor 01", "QA Vendor {unique}"
# (title, names must be unique on the server, plan, should the test be saved?)
SCENARIOS = [
    ("A. fixed name, server rejects duplicates: create + check", True, plan(FIXED)[0], False),
    ("B. fixed name, duplicates allowed: create + search + reopen", False, plan(FIXED)[1], False),
    ("C. {unique} name, server rejects duplicates: create + check", True, plan(UNIQUE)[0], True),
    ("D. {unique} name, duplicates allowed: create + search + reopen", False, plan(UNIQUE)[1], True),
]


def run(verbose=True):
    results = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for title, unique, steps, should_save in SCENARIOS:
            srv, vendors = serve(unique)
            base = f'http://127.0.0.1:{srv.server_port}/'
            scripted = ScriptedPlanner([{"steps": steps, "done": True, "test": {"flow": "vendors", "title": "Create a vendor"}}])
            original, qm_agent.Planner = qm_agent.Planner, (lambda *a, **k: scripted)
            try:
                page = b.new_page(); page.goto(base)
                agent = FastAgent(page, b, {}, base_url=base, tests_root=tempfile.mkdtemp(), scan="off")
                result = agent.run_task("Create a vendor in Pune and check it")
            finally:
                qm_agent.Planner = original
            names = sorted(v['name'] for v in vendors if v['name'].startswith('QA Vendor'))
            results.append({"title": title, "saved": bool(result['saved']), "expected": should_save, "names": names,
                            "replay": result['replay'], "code": result.get('code') or [], "summary": summary_text(result)})
            if verbose:
                rp = result['replay'] or {}
                print(f"\n{title}")
                print("  replays:", rp.get('runs'), "| saved:", bool(result['saved']), "(expected", str(should_save) + ")",
                      "| vendors on the server:", names)
                if not rp.get('ok'):
                    print("  failed at:", (rp.get('failed_step') or {}).get('name'), '-', str((rp.get('failed_step') or {}).get('error'))[:110])
                if '{unique}' in str(steps) and result.get('code'):
                    print("  " + "\n  ".join(line for line in result['code'] if 'tc_data' in line)[:700])
            page.close(); srv.shutdown()
        b.close()
    return results


if __name__ == "__main__":
    out = run()
    good = sum(r["saved"] == r["expected"] for r in out)
    print(f"\n{good}/{len(out)} scenarios behave as they should")
