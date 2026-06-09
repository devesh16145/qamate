"""
cart_create_compare.py — Full cart creation flow: Native vs MCP backend.

Executes the complete offline cart creation sequence through both backends
using identical code paths and reports step-by-step results.

Cart flow:
  1. Navigate to cart creation page
  2. Search customer → select SUPERTECH LIMITED (MUI autocomplete)
  3. Wait for shipping address to auto-populate
  4. Click Next (position:fixed footer button — the known problem)
  5. Search SKU → add item (MUI autocomplete)
  6. Select COD payment method (Tailwind hidden radio)
  7. Click Next again

Usage:
    cd agrim-ats
    python engine/cart_create_compare.py
"""

import sys, os, json, time, traceback

_THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS)

from agent_chat import BrowserSession
from mcp_browser import MCPBrowserSession

# ── Config ────────────────────────────────────────────────────────────────────

ATS_ROOT    = os.path.dirname(_THIS)
STORAGE     = os.path.join(ATS_ROOT, ".auth", "admin_dev_storage.json")
CART_URL    = "https://admin-dev.agrim.app/#/oms/cart/create"
CUSTOMER    = "SUPERTECH LIMITED"
SKU_SEARCH  = "Testing Ajay Seeds"

with open(os.path.join(ATS_ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)


# ── Step runner ───────────────────────────────────────────────────────────────

class StepRunner:
    def __init__(self, name, session):
        self.name    = name
        self.session = session
        self.results = []
        self._step   = 0

    def step(self, label, fn):
        self._step += 1
        t0 = time.perf_counter()
        try:
            r = fn(self.session)
            ok = r.get("ok", True) if isinstance(r, dict) else bool(r)
            err = (r.get("error") or r.get("mcp_error") or "") if isinstance(r, dict) else ""
            note = r.get("note") or r.get("hint") or "" if isinstance(r, dict) else ""
            # Extra info for debugging
            extra = ""
            if isinstance(r, dict):
                if r.get("elements"):
                    extra = f"{len(r['elements'])} elements"
                if r.get("autocomplete_options"):
                    extra = f"autocomplete: {r['autocomplete_options'][:3]}"
                if r.get("api_errors"):
                    extra += f" api_errors:{r['api_errors']}"
        except Exception as e:
            ok, err, note, extra = False, traceback.format_exc().splitlines()[-1], "", ""
        elapsed = (time.perf_counter() - t0) * 1000
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label:<45} {elapsed:6.0f}ms"
              + (f"  {extra}" if extra else "")
              + (f"  ERROR: {err[:80]}" if err and not ok else ""))
        self.results.append({
            "step": self._step, "label": label, "ok": ok,
            "elapsed_ms": round(elapsed), "error": err, "extra": extra,
        })
        return ok, (r if isinstance(r, dict) else {})

    def summary(self):
        passed = sum(1 for r in self.results if r["ok"])
        total  = len(self.results)
        ms     = sum(r["elapsed_ms"] for r in self.results)
        return passed, total, ms


# ── Cart creation flow (same code for both backends) ─────────────────────────

def run_cart_flow(runner):
    s = runner.step

    # ── 1. Navigate ──────────────────────────────────────────────────────────
    s("Navigate to cart creation page",
      lambda b: b.navigate(CART_URL))

    # Give SPA 2s to fully mount after navigation
    time.sleep(2)

    # ── 2. Inspect + find customer search ────────────────────────────────────
    ok, insp = s("Inspect page (find customer search field)",
                 lambda b: b.inspect(limit=60))
    if not ok:
        print("    Aborting: cannot inspect page")
        return

    # Find customer search ref — try common names
    cust_ref = _find_ref(insp, ["customer", "search customer", "search"])
    if cust_ref:
        runner.results[-1]["extra"] = f"ref='{cust_ref}'"

    # ── 3. Fill customer search ───────────────────────────────────────────────
    if cust_ref:
        ok, fill_r = s(f"Fill customer search with '{CUSTOMER}'",
                       lambda b: b.act("fill", cust_ref, CUSTOMER))
    else:
        # Direct CSS fallback
        ok, fill_r = s(f"Fill customer search (direct CSS fallback)",
                       lambda b: b._act_direct("fill",
                           'input[placeholder*="Customer"], input[placeholder*="customer"], '
                           'input[type="text"]:first-of-type', CUSTOMER))

    # ── 4. Wait for autocomplete option ──────────────────────────────────────
    s(f"Wait for '{CUSTOMER}' in autocomplete",
      lambda b: b.wait_for_text_on_page(CUSTOMER, timeout_ms=8000))

    # ── 5. Click the customer option ─────────────────────────────────────────
    s(f"Click customer option '{CUSTOMER}'",
      lambda b: b.click_option_by_text(CUSTOMER))

    # ── 6. Wait for shipping address to auto-populate ────────────────────────
    s("Wait for shipping address to auto-populate",
      lambda b: b.wait_for_text_on_page("Pradesh", timeout_ms=5000))

    time.sleep(1)

    # ── 7. Find and click Next (the fixed-footer problem) ────────────────────
    ok, insp2 = s("Re-inspect to find Next button",
                  lambda b: b.inspect(limit=60))

    next_ref = _find_ref(insp2, ["next"])
    if next_ref:
        runner.results[-1]["extra"] += f"  next_ref='{next_ref}'"
        s("Click Next button (via ref)",
          lambda b: b.act("click", next_ref))
    else:
        # Try aria snapshot
        ok, snap = s("Aria snapshot to find Next",
                     lambda b: b.aria_snapshot_page("body"))
        if ok and "Next" in snap.get("tree", ""):
            s("Click Next (click_by_text fallback)",
              lambda b: b.click_by_text_direct("Next"))
        else:
            s("Click Next (direct CSS last resort)",
              lambda b: b._act_direct("click", 'button:has-text("Next"), button[class*="next"]'))

    time.sleep(2)

    # ── 8. Inspect again (should be on SKU step now) ──────────────────────────
    ok, insp3 = s("Inspect SKU step page",
                  lambda b: b.inspect(limit=60))

    # ── 9. Find SKU search and fill ───────────────────────────────────────────
    sku_ref = _find_ref(insp3, ["sku", "search sku", "search"])
    if sku_ref:
        s(f"Fill SKU search with '{SKU_SEARCH}'",
          lambda b: b.act("fill", sku_ref, SKU_SEARCH))
    else:
        s(f"Fill SKU search (direct CSS)",
          lambda b: b._act_direct("fill",
              'input[placeholder*="SKU"], input[placeholder*="sku"]', SKU_SEARCH))

    s(f"Wait for SKU results",
      lambda b: b.wait_for_text_on_page(SKU_SEARCH[:12], timeout_ms=8000))

    # ── 10. Click first SKU result ────────────────────────────────────────────
    s(f"Click first SKU result",
      lambda b: b.click_option_by_text(SKU_SEARCH[:12]))

    time.sleep(1)

    # ── 11. Select COD payment (hidden Tailwind radio) ────────────────────────
    s("Wait for payment method section",
      lambda b: b.wait_for_text_on_page("COD", timeout_ms=6000))

    # Try force_click (works for hidden radio)
    ok, _ = s("Select COD payment (force_click hidden radio)",
               lambda b: b.force_click_hidden('input[type="radio"]', nth=0))

    if not ok:
        # Fallback: click by text
        s("Select COD payment (click_by_text fallback)",
          lambda b: b.click_by_text_direct("COD"))

    time.sleep(1)

    # ── 12. Click final Next ──────────────────────────────────────────────────
    ok, insp4 = s("Re-inspect for final Next",
                  lambda b: b.inspect(limit=60))
    next_ref2 = _find_ref(insp4, ["next"])
    if next_ref2:
        s("Click final Next (via ref)",
          lambda b: b.act("click", next_ref2))
    else:
        s("Click final Next (click_by_text)",
          lambda b: b.click_by_text_direct("Next"))

    time.sleep(1)

    # ── 13. Verify we advanced ────────────────────────────────────────────────
    ok, snap_final = s("Take aria snapshot of final state",
                       lambda b: b.aria_snapshot_page("body"))
    if ok:
        tree = snap_final.get("tree", "")
        # Check for signs of success (moved past cart step)
        if any(k in tree for k in ["Summary", "Confirm", "Review", "Order", "Place"]):
            print("    => Appears to have advanced past cart step")
        elif "Next" in tree:
            print("    => Still on cart step (Next still visible)")
        else:
            print("    => Unknown state")


def _find_ref(insp_result, keywords):
    """Find first element ref whose name matches any keyword (case-insensitive)."""
    if not isinstance(insp_result, dict):
        return None
    for el in insp_result.get("elements", []):
        name = (el.get("name") or el.get("ref") or "").lower()
        if any(k in name for k in keywords):
            return el["ref"]
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run_backend(label, session_cls, browser_config=None):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    session = session_cls()
    runner  = StepRunner(label, session)

    try:
        # Start browser
        t0 = time.perf_counter()
        try:
            info = session.start(
                start_url=CART_URL,
                storage_state=STORAGE if os.path.exists(STORAGE) else None,
                headless=True,
                browser_config=browser_config or {},
            )
        except TypeError:
            info = session.start(
                start_url=CART_URL,
                storage_state=STORAGE if os.path.exists(STORAGE) else None,
                headless=True,
            )
        start_ms = (time.perf_counter() - t0) * 1000
        print(f"  [start] {start_ms:.0f}ms  auth={info.get('authed')}  url={info.get('url','')[:60]}")
        if info.get("mcp_error"):
            print(f"  FATAL: {info['mcp_error']}")
            return None

        run_cart_flow(runner)
    finally:
        try:
            session.close()
        except Exception:
            pass

    passed, total, flow_ms = runner.summary()
    print(f"\n  Result: {passed}/{total} steps passed  |  {flow_ms}ms flow time")
    return runner.results


def compare(native_r, mcp_r):
    print(f"\n{'='*70}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*70}")
    if not native_r or not mcp_r:
        print("  Cannot compare — one or both runs failed to start")
        return

    n_pass = sum(1 for r in native_r if r["ok"])
    m_pass = sum(1 for r in mcp_r   if r["ok"])
    n_ms   = sum(r["elapsed_ms"] for r in native_r)
    m_ms   = sum(r["elapsed_ms"] for r in mcp_r)

    print(f"  {'Metric':<30} {'Native':>10} {'MCP':>10}")
    print(f"  {'-'*50}")
    print(f"  {'Steps passed':<30} {n_pass:>10} {m_pass:>10}")
    print(f"  {'Total flow time (ms)':<30} {n_ms:>10} {m_ms:>10}")

    # Per-step comparison
    n_map = {r["label"]: r for r in native_r}
    m_map = {r["label"]: r for r in mcp_r}
    print(f"\n  {'Step':<46} {'Native':>8} {'MCP':>8}")
    print(f"  {'-'*65}")
    for r in native_r:
        m = m_map.get(r["label"], {})
        n_status = "PASS" if r["ok"] else "FAIL"
        m_status = "PASS" if m.get("ok") else "FAIL" if m else "N/A "
        print(f"  {r['label']:<46} {n_status:>8} {m_status:>8}")

    print(f"\n  Verdict:")
    if n_pass == m_pass:
        faster = "NATIVE" if n_ms < m_ms else "MCP"
        print(f"    Both completed {n_pass}/{len(native_r)} steps. {faster} was faster.")
    elif n_pass > m_pass:
        print(f"    NATIVE succeeded more steps ({n_pass} vs {m_pass}).")
    else:
        print(f"    MCP succeeded more steps ({m_pass} vs {n_pass}).")

    out = {"native": native_r, "mcp": mcp_r}
    out_path = os.path.join(ATS_ROOT, "results", "cart_create_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Full results: {out_path}")


if __name__ == "__main__":
    native_results = run_backend(
        "NATIVE — Python Playwright",
        BrowserSession,
        browser_config=CONFIG.get("browser", {}),
    )
    mcp_results = run_backend(
        "MCP — Playwright MCP Server",
        MCPBrowserSession,
    )
    compare(native_results, mcp_results)
