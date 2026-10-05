#!/usr/bin/env python
"""Run hand-written plans through the fast engine -- no models, so this measures the
ENGINE (observe, ground, act, record, replay), not the planner.

    venv/bin/python engine/probes/run_plans.py                    # every plan in plans/
    venv/bin/python engine/probes/run_plans.py plans/smoke.json -v
    venv/bin/python engine/probes/run_plans.py --look URL         # what the planner would see

A plan is {"url", "seen", "steps": [intent, ...]}; "seen": false marks an app the engine was
never tuned on -- write its plan from --look output only, run it once, and keep the result.
"""
import functools, glob, http.server, json, os, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)
from playwright.sync_api import sync_playwright
from qm_explorer import Explorer
from qm_observe import observe
from qm_planner import normalize_step, page_summary
from qm_verify import replay


def fixture_server():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=os.path.join(ENGINE, "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/"


def show_page(page, limit=60):
    s = page_summary(observe(page))
    print(f"     planner sees {s['url']} ({len(s['elements'])} elements):")
    for line in s["elements"][:limit]:
        print("       ", line)
    print("        text:", s["text"][:20])


def run_plan(browser, path, fixtures, verbose):
    plan = json.load(open(path, encoding="utf-8"))
    url = plan["url"].replace("fixture:", fixtures)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    explorer = Explorer(page, base_url=None, confirm=lambda *a: True)
    started, problem = time.monotonic(), None
    for index, intent in enumerate([{"do": "goto", "url": url}] + [normalize_step(s) for s in plan["steps"]]):
        outcome = explorer.run_intent(intent)
        if verbose:
            what = str(intent.get("target") or intent.get("value") or intent.get("url") or "")[:40]
            print(f"   {index:2d} {'ok  ' if outcome['ok'] else 'FAIL'} {outcome['ms']:5d}ms {intent['do']:16s} {what}")
        if not outcome["ok"]:
            problem = (f"step {index} {intent['do']} {str(intent.get('target') or intent.get('value') or '')!r}: "
                       f"{outcome.get('reason')} - {str(outcome.get('detail')).splitlines()[0][:120]}")
            if verbose:
                for cand in outcome.get("candidates") or []:
                    print("      closest:", cand)
                show_page(page)
            break
    authoring = time.monotonic() - started
    replays = []
    if problem is None:
        replays = [replay(browser, explorer.steps, explorer.data) for _ in range(2)]
        bad = next((r for r in replays if not r["ok"]), None)
        if bad:
            problem = f"replay: {str(bad['failed_step'])[:160]}"
    context.close()
    return {"name": os.path.basename(path)[:-5], "seen": plan.get("seen", True), "ok": problem is None,
            "steps": len(explorer.steps), "authoring_s": round(authoring, 1),
            "replay_s": round(replays[0]["ms"] / 1000, 1) if replays else None, "problem": problem}


def main(argv):
    verbose = "-v" in argv
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        if "--look" in argv:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(argv[argv.index("--look") + 1], wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            show_page(page, 140)
            return 0
        paths = [a for a in argv if a.endswith(".json")] or sorted(glob.glob(os.path.join(HERE, "plans", "*.json")))
        fixtures = fixture_server()
        rows = []
        for path in paths:
            if verbose:
                print(os.path.basename(path))
            rows.append(run_plan(browser, path, fixtures, verbose))
        browser.close()
    print(f"\n{'plan':22s} {'app':7s} result  steps  authoring  replay")
    for r in rows:
        print(f"{r['name']:22s} {'seen' if r['seen'] else 'UNSEEN':7s} {'pass' if r['ok'] else 'FAIL':6s} {r['steps']:5d} {r['authoring_s']:9.1f}s "
              f"{(str(r['replay_s']) + 's') if r['replay_s'] is not None else '-':>7s}  {r['problem'] or ''}")
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} plans pass")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
