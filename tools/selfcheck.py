#!/usr/bin/env python
"""
QAmate — self-check / doctor
===============================

Verifies the self-healing locator engine and Playwright trace recording work
end-to-end, headless, with NO dependency on a specific app or login. Run it
any time (and in CI) to confirm the infrastructure is healthy:

    venv\\Scripts\\python tools\\selfcheck.py        (Windows)
    venv/bin/python tools/selfcheck.py              (mac/Linux)

Exit code 0 = all checks passed. ASCII-only output (Windows cp1252 console safe).
"""
import os
import sys
import zipfile
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from playwright.sync_api import sync_playwright
import smart_locator as sl

_results = []


def check(name, passed, detail=""):
    _results.append((name, bool(passed), detail))


def _launch(p):
    """Prefer real Chrome (what the suite uses); fall back to bundled Chromium."""
    try:
        return p.chromium.launch(channel="chrome", headless=True), "chrome"
    except Exception:
        return p.chromium.launch(headless=True), "chromium"


# A drift-resilient spec: the element is "the Save changes button", however the
# page chooses to render its id/class/markup.
SPEC = dict(
    fallbacks=[{"by": "role", "role": "button", "name": "Save changes"}],
    fingerprint={"tag": "button", "name": "Save changes", "text": "Save"},
)


def main():
    with sync_playwright() as p:
        browser, channel = _launch(p)
        print(f"[selfcheck] browser channel: {channel}")
        page = browser.new_context().new_page()

        # 1) Primary matches -> no heal needed
        page.set_content('<button id="save-btn" aria-label="Save changes">Save</button>')
        h = sl.Healer("selfcheck")
        loc = sl.smart_locator(page, 'page.get_by_role("button", name="Save changes")', healer=h).resolve()
        check("self-heal: primary match (no heal recorded)",
              loc.text_content() == "Save" and len(h.events) == 0)

        # 2) id + class drift -> recovered via FALLBACK tier
        page.set_content('<button id="cta-7f3a91" class="x9q2" aria-label="Save changes">Save</button>')
        h = sl.Healer("selfcheck")
        loc = sl.smart_locator(page, 'page.locator("#save-btn")', healer=h, **SPEC).resolve()
        check("self-heal: fallback after id/class drift",
              loc.get_attribute("aria-label") == "Save changes"
              and bool(h.events) and h.events[0]["tier"] == "fallback")

        # 3) heavy drift (only visible text survives) -> recovered via FINGERPRINT tier
        page.set_content('<div><span>noise</span><button id="zzz" class="q">Save</button></div>')
        h = sl.Healer("selfcheck")
        loc = sl.smart_locator(page, 'page.locator("#save-btn")', healer=h, **SPEC).resolve()
        check("self-heal: fingerprint after heavy drift",
              loc.text_content() == "Save"
              and bool(h.events) and h.events[0]["tier"] == "fingerprint")

        # 4) genuinely missing -> must fail loud, never a silent false pass
        page.set_content("<div>nothing here</div>")
        try:
            sl.smart_locator(page, 'page.locator("#save-btn")', **SPEC).resolve(
                timeout_ms=600, fallback_timeout_ms=300)
            check("self-heal: fails loud when element truly missing", False, "expected SelfHealError")
        except sl.SelfHealError:
            check("self-heal: fails loud when element truly missing", True)

        # 5) trace recording end-to-end -> valid trace zip with a .trace entry
        tdir = tempfile.mkdtemp()
        tzip = os.path.join(tdir, "selfcheck_trace.zip")
        tctx = browser.new_context()
        tctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        tpage = tctx.new_page()
        tpage.set_content('<button>Trace me</button>')
        tpage.get_by_role("button", name="Trace me").click()
        tctx.tracing.stop(path=tzip)
        ok = False
        if os.path.exists(tzip):
            with zipfile.ZipFile(tzip) as z:
                ok = any(n.endswith(".trace") for n in z.namelist())
        check("trace: records a valid Playwright trace zip", ok)
        shutil.rmtree(tdir, ignore_errors=True)

        browser.close()

    print("\n=== QAmate self-check ===")
    all_ok = True
    for name, passed, detail in _results:
        suffix = f"  -- {detail}" if (detail and not passed) else ""
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}{suffix}")
        all_ok = all_ok and passed
    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}\n")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
