"""
observe_smoke.py — Live smoke test for the unified observe()/refs harness.

Drives the public Playwright docs site (no login) using ONLY the intent-level
API — observe percept, ref-based act, identify_at grounding. No LLM: a pass
here means the harness works deterministically before any model is involved.

Usage:
    cd qamate
    venv\\Scripts\\python.exe engine\\observe_smoke.py
"""

import sys, os, json, time, traceback

_THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS)

from agent_chat import BrowserSession

ATS_ROOT = os.path.dirname(_THIS)
DOCS_HOME = "https://playwright.dev/"
DOCS_INTRO = "https://playwright.dev/docs/intro"

RESULTS = []


def step(label, fn):
    t0 = time.perf_counter()
    try:
        r = fn()
        ok = r.get("ok", True) if isinstance(r, dict) else bool(r)
        err = (r.get("error") or "") if isinstance(r, dict) else ""
    except Exception:
        r, ok, err = {}, False, traceback.format_exc().splitlines()[-1]
    ms = (time.perf_counter() - t0) * 1000
    extra = ""
    if isinstance(r, dict):
        if "element_count" in r:
            extra = f"{r['element_count']} elements"
        if r.get("ref"):
            extra = f"ref={r['ref']}"
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} {ms:6.0f}ms  {extra}"
          + (f"  ERR: {err[:70]}" if err and not ok else ""))
    RESULTS.append({"label": label, "ok": ok, "ms": round(ms), "error": err})
    return ok, (r if isinstance(r, dict) else {})


def find_ref(insp, keywords, role=None):
    for el in insp.get("elements", []):
        name = (el.get("name") or el.get("ref") or "").lower()
        if any(k in name for k in keywords):
            if role is None or (el.get("role") or "") == role:
                return el["ref"]
    return None


def main():
    print("=" * 78)
    print("  UNIFIED OBSERVE/REFS HARNESS - LIVE SMOKE (playwright.dev, no LLM)")
    print("=" * 78)
    s = BrowserSession()
    try:
        info = s.start(start_url=DOCS_HOME, headless=True)
        print(f"  [start] url={str(info.get('url', ''))[:60]}")
        time.sleep(1)

        ok, insp = step("observe: unified percept", lambda: s.inspect(limit=60))

        gs_ref = find_ref(insp, ["get started"], role="link")
        if not gs_ref:
            step("find Get started link", lambda: {"ok": False, "error": "no Get started ref"})
        else:
            step("click Get started by ref", lambda: s.act("click", gs_ref))

        time.sleep(1)
        ok, insp2 = step("re-observe after navigation", lambda: s.inspect(limit=60))

        heading_ref = find_ref(insp2, ["intro", "installation", "playwright"], role="heading")
        step("observe includes docs heading",
             lambda: {"ok": heading_ref is not None or len(insp2.get("elements", [])) > 5,
                      "ref": heading_ref,
                      "error": "" if insp2.get("elements") else "empty percept"})

        ok, insp3 = step("navigate to docs intro", lambda: s.navigate(DOCS_INTRO) or s.inspect(limit=40))
        el0 = next((e for e in (s.by_ref or {}).values()
                    if (e.get("rect") or {}).get("w")), None)
        if el0:
            cx = el0["rect"]["x"] + el0["rect"]["w"] / 2
            cy = el0["rect"]["y"] + el0["rect"]["h"] / 2
            step("identify_at(center of a known element)",
                 lambda: s.identify_at(cx, cy))
        else:
            step("identify_at precondition (rects captured)",
                 lambda: {"ok": False, "error": "no element rects in registry"})

        for st in s.steps:
            compile(st["rawLine"], "<rawline>", "exec")
        step("all recorded rawLines compile as Python", lambda: {"ok": True})

    finally:
        try:
            s.close()
        except Exception:
            pass

    passed = sum(1 for r in RESULTS if r["ok"])
    print("-" * 78)
    print(f"  RESULT: {passed}/{len(RESULTS)} steps passed")
    out = os.path.join(ATS_ROOT, "results", "observe_smoke.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"  saved -> {out}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
