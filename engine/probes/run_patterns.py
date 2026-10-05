#!/usr/bin/env python
"""Capability probe: one mini-task per common web pattern (patterns.html), no models.

    venv/bin/python engine/probes/run_patterns.py [-v] [name filter ...]

Each case is a plan a planner could plausibly write. PASS = executed and replayed green;
a pattern that fails here cannot be tested by the fast engine today, whatever the model.
"""
import functools, http.server, os, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from playwright.sync_api import sync_playwright
from qm_explorer import Explorer
from qm_observe import observe
from qm_planner import page_summary, normalize_step
from qm_verify import replay
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(H, directory=HERE))
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f'http://127.0.0.1:{srv.server_port}/patterns.html'
c = lambda t, **k: {"do": "click", "target": t, **k}
f = lambda t, v, **k: {"do": "fill", "target": t, "value": v, **k}
txt = lambda v, present=True: {"do": "expect_text", "value": v, **({} if present else {"present": False})}
CASES = [
 ("Browser confirm() pop-up", "confirm", [c("Archive report"), txt("Report archived")]),
 ("Link opens a new tab", "newtab", [c("Open report"), txt("Revenue up 12%")]),
 ("Button inside an iframe", "iframe", [c("Approve request"), txt("Request approved")]),
 ("Web component (shadow DOM)", "shadow", [c("Refresh data"), txt("Data refreshed")]),
 ("Result after 6.5 s", "slow", [c("Generate report"), txt("Report ready")]),
 ("Checkboxes with no <label>", "checkboxes", [{"do": "check", "target": "SMS alerts"}, c("Save alerts"), txt("Saved: false,true")]),
 ("Dropdown with no <label>", "select", [{"do": "select", "target": "Priority", "value": "High"}, txt("Priority set to High")]),
 ("Icon-only button", "icons", [c("Edit", within="Invoice 1041"), txt("Editing invoice 1041")]),
 ("Row action in a table", "rows", [c("Edit", within="Bach"), txt("Editing Bach")]),
 ("Native date field", "date", [f("Due date", "2026-11-05"), c("Set date"), txt("Due 2026-11-05")]),
 ("Menu shown on hover", "hover", [c("Sign-in history"), txt("Quarterly report")]),
 ("Toast gone after 1.2 s", "toastfast", [c("Save settings"), txt("Settings saved")]),
 ("Toast gone after 3.5 s", "toast", [c("Save settings"), txt("Settings saved")]),
 ("Hindi labels", "hindi", [f("ग्राहक का नाम", "आशा"), c("नया ऑर्डर बनाएं"), txt("ऑर्डर बन गया: आशा")]),
 ("French labels", "french", [f("Nom du client", "Asha"), c("Créer une commande"), txt("Commande créée: Asha")]),
 ("Rich-text box", "rich", [f("Comment", "Looks good"), c("Post comment"), txt("Posted: Looks good")]),
 ("Item far down a lazy list", "longlist", [c("Ticket 150"), txt("Opened Ticket 150")]),
 # A blind plan (fill, save) is told "Choose a city from the list" and re-plans; this is the informed plan.
 ("Type-ahead that needs a pick", "typeahead", [f("City", "Pu"), c("Pune"), c("Save city"), txt("City saved: Pune")]),
 ("Same labels in two sections", "dup", [f("City", "Pune", within="Shipping address"), c("Save", within="Shipping address"), txt("Shipping saved: Pune")]),
]
only = [a for a in sys.argv[1:] if not a.startswith('-')]
results, RESULTS = [], []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name, route, plan in CASES:
        if only and not any(o.lower() in name.lower() for o in only): continue
        ctx = b.new_context(viewport={'width': 1280, 'height': 800}); page = ctx.new_page()
        popups = []; ctx.on('page', lambda p: popups.append(1))
        ex = Explorer(page, base_url=None, confirm=lambda *a: True)
        verdict, why = 'PASS', ''
        try:
            for intent in [{"do": "goto", "url": B + '?p=' + route}] + [normalize_step(x) for x in plan]:
                out = ex.run_intent(intent)
                if not out['ok']:
                    verdict = 'FAIL'
                    why = f"{intent['do']} {intent.get('target') or intent.get('value') or ''!r}: {out.get('reason')} - {str(out.get('detail')).splitlines()[0][:100]}"
                    if out.get('candidates'): why += f" | closest: {out['candidates'][:3]}"
                    break
            if verdict == 'PASS':
                r = replay(b, ex.steps, ex.data, base_url=None)
                if not r['ok']: verdict, why = 'REPLAY-FAIL', str(r['failed_step'])[:200]
        except Exception as e:
            verdict, why = 'ERROR', str(e)[:160]
        if verdict == 'PASS': why = ' | '.join(str(s.get('target') or '')[:60] for s in ex.steps if s.get('target'))[:150]
        print(f"{verdict:11s} {name:30s} {why}" + (" [a new tab opened; the engine stayed on the old one]" if len(popups) > 1 else ''))
        if verdict != 'PASS' and '-v' in sys.argv:
            print('     planner sees:', page_summary(observe(page))['elements'][:12], '| text:', page_summary(observe(page))['text'][:8])
        ctx.close()
        results.append(verdict)
        RESULTS.append((name, verdict, why))
    b.close()
print(f"\n{results.count('PASS')}/{len(results)} patterns pass")
