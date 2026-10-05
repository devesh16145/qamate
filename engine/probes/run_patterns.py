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
    def do_GET(self):
        if '/slow' in self.path:                 # an API that takes its time: slow?ms=2500
            time.sleep(min(int(self.path.rpartition('ms=')[2] or 0), 10000) / 1000)
            self.send_response(200); self.send_header('Content-Type', 'text/plain'); self.end_headers(); self.wfile.write(b'ok')
            return
        return super().do_GET()
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
 ("Styled checkbox, input invisible", "styled", [{"do": "check", "target": "Samsung"}, txt("Showing: Samsung")]),
 ("Date typed as month/day/year", "segments", [f("Join date", "10/05/2026"), c("Save date"), txt("Joined 10/05/2026")]),
 ("Screen left aria-hidden", "hiddenapp", [f("Name", "Asha"), c("Create"), txt("Created Asha")]),
 ("Field named by its placeholder", "placeholder", [f("Email", "asha@example.com"), f("Age", "31"), c("Submit"), txt("Submitted asha@example.com / 31")]),
 ("Items with near-identical names", "namesakes", [c("Add to cart", within="Galaxy S20"), txt("Added Galaxy S20"), txt("Added Galaxy S20+", False)]),
 ("Page with timer loops", "ticker", [c("Save settings"), txt("Settings stored")]),
 ("Same labels in two sections", "dup", [f("City", "Pune", within="Shipping address"), c("Save", within="Shipping address"), txt("Shipping saved: Pune")]),
 ("Upload behind a button", "uploadbtn", [{"do": "upload", "target": "Attach file"}, txt("Attached test_upload.png")]),
 ("Upload behind a styled label", "uploadlabel", [{"do": "upload", "target": "Choose document"}, txt("Chosen test_upload.png")]),
 ("File download", "download", [c("Export CSV"), txt("Export started")]),
 ("Drag a card to another column", "dragdrop", [{"do": "drag", "target": "Fix login", "to": "Done"}, txt("Moved Fix login to Done")]),
 ("Drag handled with pointer events", "dragpointer", [{"do": "drag", "target": "Audit logs", "to": "In progress"}, txt("Moved Audit logs to In progress")]),
 ("Right-click menu", "contextmenu", [{"do": "rightclick", "target": "report.pdf"}, c("Archive"), txt("Archived report.pdf")]),
 ("Range input", "range", [f("Volume", "70"), txt("Volume 70")]),
 ("Slider without an input", "slider", [f("Brightness", "65"), txt("Brightness 65")]),
 ("Form in a frame from another site", "frameform", [f("Card holder", "Asha Rao"), {"do": "select", "target": "Plan", "value": "Yearly"}, c("Pay now"), txt("Paid by Asha Rao (Yearly)")]),
 ("Editor inside a frame", "frameeditor", [f("Description", "Printer is offline"), c("Save ticket"), txt("Saved: Printer is offline")]),
 ("Editable box with no role", "editable", [f("Comment", "Ship it"), c("Post"), txt("Posted: Ship it")]),
 ("Radio buttons, ticked", "radio", [{"do": "check", "target": "Express"}, txt("Shipping: Express, gift wrap: No")]),
 ("Radio buttons, as a choice", "radio", [{"do": "select", "target": "Shipping", "value": "Express"}, {"do": "select", "target": "Gift wrap", "value": "Yes"}, txt("Shipping: Express, gift wrap: Yes")]),
 ("On/off switch", "switch", [{"do": "check", "target": "Dark mode"}, txt("Dark mode enabled")]),
 ("List that takes several choices", "multiselect", [{"do": "select", "target": "Tags", "value": "Urgent"}, {"do": "select", "target": "Tags", "value": "Bug"}, txt("Tags: Urgent, Bug")]),
 ("Field on a tab not yet opened", "tabs", [f("Tax ID", "GST-22"), c("Save billing"), txt("Billing saved: GST-22")]),
 ("Field in a collapsed section", "accordion", [f("Timeout (s)", "45"), c("Apply"), txt("Timeout set to 45")]),
 ("Form swapped in by a slow request", "slowswap", [c("Withdraw"), f("Amount to be withdrawn", "200"), c("Withdraw money"), txt("Withdrew 200")]),
 ("Tab whose content is built when opened", "lazytabs", [f("Tax ID", "GST-22"), c("Save billing"), txt("Billing saved: GST-22")]),
 ("Cell edited in place", "inlineedit", [{"do": "dblclick", "target": "Viewer", "within": "Bach"}, f("Role", "Admin", within="Bach"), {"do": "press", "value": "Enter"}, txt("Role of Bach is now Admin")]),
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
        verdict, why, timings = 'PASS', '', []
        try:
            for intent in [{"do": "goto", "url": B + '?p=' + route}] + [normalize_step(x) for x in plan]:
                out = ex.run_intent(intent)
                timings.append(out)
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
        seconds = sum((s.get("ms") or 0) for s in timings) / 1000
        print(f"{verdict:11s} {name:34s} {seconds:5.1f}s  {why}" + (" [a new tab opened; the engine stayed on the old one]" if len(popups) > 1 else ''))
        if verdict != 'PASS' and '-v' in sys.argv:
            print('     planner sees:', page_summary(observe(page))['elements'][:12], '| text:', page_summary(observe(page))['text'][:8])
        ctx.close()
        results.append(verdict)
        RESULTS.append((name, verdict, why))
    b.close()
print(f"\n{results.count('PASS')}/{len(results)} patterns pass")
