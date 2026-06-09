"""
backend_compare.py — Side-by-side benchmark: Native vs Playwright MCP backend.

Runs the same fixed sequence of browser operations through both backends and
produces a comparison table: timing, element count, aria tree size, screenshot
size, and any errors encountered.

Usage:
    cd agrim-ats
    python engine/backend_compare.py

Requires:
    - venv with playwright installed
    - Node.js + npx in PATH (for MCP backend)
    - .auth/admin_dev_storage.json (created by the ATS login flow)
"""

import sys
import os
import json
import time

# Make engine/ importable when run from agrim-ats/
_THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS)

from agent_chat import BrowserSession
from mcp_browser import MCPBrowserSession

# ── Config ────────────────────────────────────────────────────────────────────

ATS_ROOT = os.path.dirname(_THIS)
CONFIG_PATH = os.path.join(ATS_ROOT, "config.json")
ADMIN_URL = "https://admin-dev.agrim.app/#/oms/cart/create"
STORAGE_STATE = os.path.join(ATS_ROOT, ".auth", "admin_dev_storage.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

# ── Test sequence ─────────────────────────────────────────────────────────────

STEPS = [
    ("navigate",      lambda s: s.navigate(ADMIN_URL)),
    ("inspect",       lambda s: s.inspect(limit=60)),
    ("aria_snapshot", lambda s: s.aria_snapshot_page("body")),
    ("screenshot",    lambda s: {"ok": True, "bytes": len(s.screenshot())}),
    ("navigate_2",    lambda s: s.navigate("https://admin-dev.agrim.app/#/oms/cart")),
    ("inspect_2",     lambda s: s.inspect(limit=60)),
]

# ── Runner ────────────────────────────────────────────────────────────────────

def run_sequence(session):
    results = []
    for name, fn in STEPS:
        t0 = time.perf_counter()
        try:
            r = fn(session)
        except Exception as e:
            r = {"ok": False, "error": str(e)[:120]}
        elapsed = time.perf_counter() - t0
        results.append({
            "step": name,
            "ok": r.get("ok", False),
            "elapsed_ms": round(elapsed * 1000),
            "elements": len(r.get("elements", [])),
            "tree_chars": len(r.get("tree", "")),
            "bytes": r.get("bytes", 0),
            "error": r.get("error") or r.get("mcp_error", ""),
        })
        status = "OK" if r.get("ok") else "FAIL"
        print(f"    [{status}] {name:20s}  {elapsed*1000:6.0f}ms"
              + (f"  {len(r.get('elements',[]))} elements" if r.get("elements") else "")
              + (f"  {len(r.get('tree',''))} tree chars" if r.get("tree") else "")
              + (f"  {r.get('bytes',0)} bytes" if r.get("bytes") else "")
              + (f"  ERROR: {r.get('error') or r.get('mcp_error','')}" if not r.get("ok") else ""))
    return results


def start_session(session):
    ss = STORAGE_STATE if os.path.exists(STORAGE_STATE) else None
    bcfg = CONFIG.get("browser", {})
    try:
        info = session.start(
            start_url=ADMIN_URL,
            storage_state=ss,
            headless=True,
            browser_config=bcfg,
        )
    except TypeError:
        # MCPBrowserSession doesn't take browser_config
        info = session.start(
            start_url=ADMIN_URL,
            storage_state=ss,
            headless=True,
        )
    return info


# ── Comparison table ──────────────────────────────────────────────────────────

def compare(native_results, mcp_results):
    print("\n" + "="*80)
    print("COMPARISON: Native Python Playwright  vs  Playwright MCP Server")
    print("="*80)
    header = f"{'Step':<22} {'Native ms':>10} {'MCP ms':>10} {'Faster':>8}  {'Native elems':>13} {'MCP elems':>10}"
    print(header)
    print("-"*80)

    totals = {"native": 0, "mcp": 0}
    native_map = {r["step"]: r for r in native_results}
    mcp_map    = {r["step"]: r for r in mcp_results}

    for step, _ in STEPS:
        n = native_map.get(step, {})
        m = mcp_map.get(step, {})
        n_ms = n.get("elapsed_ms", 0)
        m_ms = m.get("elapsed_ms", 0)
        totals["native"] += n_ms
        totals["mcp"]    += m_ms
        faster = "native" if n_ms <= m_ms else "mcp   "
        n_elems = f"{n.get('elements',0)} elems" if n.get("elements") else (f"{n.get('tree_chars',0)} chars" if n.get("tree_chars") else f"{n.get('bytes',0)} B")
        m_elems = f"{m.get('elements',0)} elems" if m.get("elements") else (f"{m.get('tree_chars',0)} chars" if m.get("tree_chars") else f"{m.get('bytes',0)} B")
        n_ok = "" if n.get("ok") else " X"
        m_ok = "" if m.get("ok") else " X"
        print(f"{step:<22} {str(n_ms)+'ms'+n_ok:>10} {str(m_ms)+'ms'+m_ok:>10} {faster:>8}  {n_elems:>13} {m_elems:>10}")

    print("-"*80)
    total_faster = "native" if totals["native"] <= totals["mcp"] else "mcp   "
    print(f"{'TOTAL':<22} {str(totals['native'])+'ms':>10} {str(totals['mcp'])+'ms':>10} {total_faster:>8}")
    print("="*80)

    # Quality summary
    n_elems_total = sum(r.get("elements", 0) for r in native_results)
    m_elems_total = sum(r.get("elements", 0) for r in mcp_results)
    n_errors = sum(1 for r in native_results if not r.get("ok"))
    m_errors = sum(1 for r in mcp_results if not r.get("ok"))

    print(f"\nQuality summary:")
    print(f"  Elements found (total across inspect steps): Native={n_elems_total}  MCP={m_elems_total}")
    print(f"  Failed steps:                                Native={n_errors}       MCP={m_errors}")
    print(f"  Total time:                                  Native={totals['native']}ms  MCP={totals['mcp']}ms")

    winner_speed   = "NATIVE" if totals["native"] < totals["mcp"] else "MCP"
    winner_quality = "NATIVE" if n_elems_total >= m_elems_total else "MCP"
    winner_reliability = "NATIVE" if n_errors <= m_errors else "MCP"
    print(f"\n  Speed winner:       {winner_speed}")
    print(f"  Element coverage:   {winner_quality}")
    print(f"  Reliability:        {winner_reliability}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*80)
    print("ATS Browser Backend Comparison")
    print(f"  Admin URL:     {ADMIN_URL}")
    print(f"  Storage state: {'found' if os.path.exists(STORAGE_STATE) else 'NOT FOUND — will not be authenticated'}")
    print("="*80)

    # ── Native ────────────────────────────────────────────────────────────────
    print("\n[1/2] Native Python Playwright backend")
    print("-"*40)
    native = BrowserSession()
    try:
        t0 = time.perf_counter()
        info = start_session(native)
        print(f"    [start]  {(time.perf_counter()-t0)*1000:.0f}ms  auth={info.get('authed')}  url={info.get('url','')[:60]}")
        native_results = run_sequence(native)
    finally:
        native.close()

    # ── MCP ───────────────────────────────────────────────────────────────────
    print("\n[2/2] Playwright MCP server backend")
    print("-"*40)
    print("    (first run downloads @playwright/mcp — may take 30-60s)")
    mcp = MCPBrowserSession()
    try:
        t0 = time.perf_counter()
        info = start_session(mcp)
        print(f"    [start]  {(time.perf_counter()-t0)*1000:.0f}ms  auth={info.get('authed')}  url={info.get('url','')[:60]}")
        if info.get("mcp_error"):
            print(f"    MCP START ERROR: {info['mcp_error']}")
            print("    Skipping MCP steps.")
            mcp_results = [{"step": s, "ok": False, "elapsed_ms": 0, "elements": 0,
                            "tree_chars": 0, "bytes": 0, "error": "MCP failed to start"}
                           for s, _ in STEPS]
        else:
            mcp_results = run_sequence(mcp)
    finally:
        mcp.close()

    # ── Compare ───────────────────────────────────────────────────────────────
    compare(native_results, mcp_results)

    # Save results JSON for reference
    out = {
        "native": native_results,
        "mcp": mcp_results,
        "url": ADMIN_URL,
        "storage_state_found": os.path.exists(STORAGE_STATE),
    }
    out_path = os.path.join(ATS_ROOT, "results", "backend_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Full results saved: {out_path}")


if __name__ == "__main__":
    main()
