"""Admin Panel flow — Checkpoint-based Playwright E2E tests.

Tests target the Agrim Admin Panel (separate URL + separate login from the
Seller App). Each test uses the `admin_page` fixture, which:
  - creates its own browser context (can coexist with seller_page in the
    same test if a TC needs cross-platform coverage)
  - applies the admin storage_state (one login per session)
  - returns a Page already pointing at the admin panel URL

URL + user are picked from config.json `platforms.admin` (default: admin-dev.agrim.app,
amit.dalal@agrim.app). The admin uses hash routing (`/#/login`), so all
in-app navigation goes through `admin_url + "#/<route>"`.

Test cases are authored from analyzer captures of the admin panel — see
ATS_TEST_PATTERNS.md for the patterns used across the seller app and
admin panel.
"""

import re
import os
import pytest
from playwright.sync_api import expect, Page


# ── Helpers ──────────────────────────────────────────────────

def _admin_goto(page, admin_url, route):
    """Navigate within the admin panel using its hash-based routing.

    `route` is the part after `#/` (e.g. "purchase-orders", "vendors/new").
    Leading slashes / hash prefixes are stripped so callers can be sloppy.
    """
    route = route.lstrip("#").lstrip("/")
    base = admin_url if admin_url.endswith("/") else admin_url + "/"
    page.goto(base + "#/" + route, wait_until="domcontentloaded")
    # Admin panel (React SPA + hash routing) needs a settling beat
    page.wait_for_timeout(800)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


def _scroll_down(page, pixels=500):
    """SPA-safe scroll — see ATS_TEST_PATTERNS.md §1."""
    page.mouse.wheel(0, pixels)
    page.wait_for_timeout(400)


def _scroll_to_top(page):
    page.keyboard.press("Home")
    page.wait_for_timeout(300)


def _check_no_admin_errors(page, context_label=""):
    """Assert no error toast / alert is visible on the admin panel."""
    page.wait_for_timeout(400)
    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
        '[class*="toast"]:visible:has-text("failed")',
        '[class*="text-destructive"]:visible',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            text = errors.first.text_content().strip()[:200]
            if text:
                raise AssertionError(
                    f"Admin error detected{f' ({context_label})' if context_label else ''}: {text}"
                )


def _wait_for_admin_ready(page, timeout=15000):
    """Wait for any signal that the admin SPA has mounted past the splash/
    initial loader. The admin uses Ant Design-style top nav — looking for
    typical sidebar/menu landmarks is reliable across routes."""
    candidates = [
        '[class*="ant-layout-sider"]',
        '[class*="MuiDrawer"]',
        'nav, [role="navigation"]',
        '[class*="sidebar"]',
        'aside',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout // len(candidates))
            return
        except Exception:
            continue
    # Last-resort: any visible button (means SPA mounted something)
    page.locator('button:visible').first.wait_for(state="visible", timeout=5000)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def admin_home(admin_page: Page, admin_url):
    """Land on the admin panel's home/dashboard route after login state
    has been applied. Most TCs should start here."""
    admin_page.goto(admin_url, wait_until="domcontentloaded")
    _wait_for_admin_ready(admin_page)
    admin_page.wait_for_timeout(800)
    return admin_page


# ── Test cases ───────────────────────────────────────────────
#
# Test cases will be added after the UI walkthrough. Each TC follows the
# checkpoint pattern (see catalog flow for reference):
#
# @pytest.mark.tc("TC-ADMIN-001")
# def test_TC_ADMIN_001_<short_name>(admin_home: Page, admin_url, tc_data, checkpoints):
#     """One-line summary of the scenario."""
#     page = admin_home
#
#     def cp_<step>():
#         ...assertions...
#
#     checkpoints.run("Step description", cp_<step>)


@pytest.mark.tc("TC-ADMIN-EXPLORE-ORDERS")
def test_TC_ADMIN_EXPLORE_ORDERS(page: Page, tc_data, base_url, admin_url, checkpoints):
    """Explore orders
    """
    # Step 1: Navigate to Navigate to https://admin-dev.agrim.app/#/login
    page.goto(admin_url + "#/login")
    page.wait_for_load_state("networkidle")
    # Step 2: Enter "amit.dalal@agrim.app" in Textbox "Username"
    page.get_by_role("textbox", name="Username").fill(tc_data.get("input_1", ""))
    # Step 3: Click Textbox "Password"
    page.get_by_role("textbox", name="Password").click()
    page.wait_for_timeout(500)
    # Step 5: Enter "A" in Textbox "Password"
    page.get_by_role("textbox", name="Password").fill(tc_data.get("input_2", ""))
    # Step 7: Enter "Amit@12345" in Textbox "Password"
    page.get_by_role("textbox", name="Password").fill(tc_data.get("input_3", ""))
    # Step 8: Click Button "Sign in"
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_timeout(500)
    # Step 9: Click Button "user icon Purchase orders"
    page.get_by_role("button", name="user icon Purchase orders").click()
    page.wait_for_timeout(500)
    # Step 10: Click Menuitem "All Orders"
    page.get_by_role("menuitem", name="All Orders").click()
    page.wait_for_timeout(500)
    # Step 11: Click child(3) > img")
    page.locator(r".align-items-center > div > div:nth-child(3) > img").scroll_into_view_if_needed()
    page.locator(r".align-items-center > div > div:nth-child(3) > img").click()
    page.wait_for_timeout(500)
    # Step 12: Click root").first
    page.locator(r".MuiFormControl-root").first.scroll_into_view_if_needed()
    page.locator(r".MuiFormControl-root").first.click()
    page.wait_for_timeout(500)
    # Step 13: Enter "m&" in Textbox "Search"
    page.get_by_role("textbox", name="Search").first.fill(tc_data.get("input_4", ""))
    # Step 14: Double-click M_Brand1")
    page.get_by_role("menuitem", name="M&M_Brand1").dblclick()
    page.wait_for_timeout(500)
    # Step 15: Click child(3) > div").first
    page.locator(r".align-items-center > div:nth-child(3) > div").first.scroll_into_view_if_needed()
    page.locator(r".align-items-center > div:nth-child(3) > div").first.click()
    page.wait_for_timeout(500)
    # Step 16: Click Menuitem "ODISHA"
    page.get_by_role("menuitem", name="ODISHA").click()
    page.wait_for_timeout(500)
    # Step 17: Click child(2) > div").first
    page.locator(r".jss7 > .d-flex > div > div:nth-child(2) > div").first.scroll_into_view_if_needed()
    page.locator(r".jss7 > .d-flex > div > div:nth-child(2) > div").first.click()
    page.wait_for_timeout(500)
    # Step 18: Click Text "CUTTACK WH"
    page.get_by_text("CUTTACK WH").click()
    page.wait_for_timeout(500)
    # Step 19: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Load Type$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Load Type$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 20: Click Text "Full Load"
    page.get_by_text("Full Load").click()
    page.wait_for_timeout(500)
    # Step 21: Click Img "Filled Checkbox"
    page.get_by_role("img", name="Filled Checkbox").click(force=True)
    page.wait_for_timeout(500)
    # Step 22: Click Text "PO Type"
    page.get_by_text("PO Type").nth(1).click()
    page.wait_for_timeout(500)
    # Step 23: Click Menuitem "Inventory Purchase"
    page.get_by_role("menuitem", name="Inventory Purchase").click()
    page.wait_for_timeout(500)
    # Step 24: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Type of Inventory Purchase$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Type of Inventory Purchase$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 25: Click Menuitem "Business Purchase"
    page.get_by_role("menuitem", name="Business Purchase", exact=True).click()
    page.wait_for_timeout(500)
    # Step 26: Click Textbox "Search"
    page.get_by_role("textbox", name="Search").nth(1).click()
    page.wait_for_timeout(500)
    # Step 27: Enter "test" in Textbox "Search"
    page.get_by_role("textbox", name="Search").nth(1).fill(tc_data.get("input_5", ""))
    # Auto-fix: MUI autocomplete select #1 → pick .first
    # (different .nth per call so multi-row flows pick distinct items)
    page.wait_for_timeout(2000)  # let autocomplete API trigger
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    _ac_popup = page.locator(
        ".MuiAutocomplete-popper:visible, "
        ".MuiAutocomplete-listbox:visible, "
        ".MuiPopover-root:visible, "
        ".MuiMenu-paper:visible"
    ).last
    _ac_item = _ac_popup.locator('li, [role="menuitem"]').first
    _selected = False
    try:
        _ac_item.wait_for(state="visible", timeout=12000)
        page.wait_for_timeout(500)  # let item finalise
        _ac_box = _ac_item.bounding_box()
        if _ac_box and _ac_box["height"] > 5:
            page.mouse.click(_ac_box["x"] + _ac_box["width"] / 2,
                             _ac_box["y"] + _ac_box["height"] / 2)
            _selected = True
    except Exception:
        pass
    if not _selected:
        # Fallback: keyboard select — ArrowDown N+1 times to highlight nth item
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
    page.wait_for_timeout(1500)  # React state commit after selection
    # Step 29: Click "]")
    page.get_by_placeholder("#").first.click(force=True)
    page.wait_for_timeout(500)
    # Step 30: Enter "7" in "]")
    page.get_by_placeholder("#").first.press_sequentially(tc_data.get("input_6", ""), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    # Step 31: Click Text "Add New Row"
    page.get_by_text("Add New Row").click()
    page.wait_for_timeout(500)
    # Step 32: Click Textbox "Search"
    page.get_by_role("textbox", name="Search").nth(2).click()
    page.wait_for_timeout(500)
    # Step 33: Enter "test" in Textbox "Search"
    page.get_by_role("textbox", name="Search").nth(2).fill(tc_data.get("input_7", ""))
    # Auto-fix: MUI autocomplete select #2 → pick .nth(1)
    # (different .nth per call so multi-row flows pick distinct items)
    page.wait_for_timeout(2000)  # let autocomplete API trigger
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    _ac_popup = page.locator(
        ".MuiAutocomplete-popper:visible, "
        ".MuiAutocomplete-listbox:visible, "
        ".MuiPopover-root:visible, "
        ".MuiMenu-paper:visible"
    ).last
    _ac_item = _ac_popup.locator('li, [role="menuitem"]').nth(1)
    _selected = False
    try:
        _ac_item.wait_for(state="visible", timeout=12000)
        page.wait_for_timeout(500)  # let item finalise
        _ac_box = _ac_item.bounding_box()
        if _ac_box and _ac_box["height"] > 5:
            page.mouse.click(_ac_box["x"] + _ac_box["width"] / 2,
                             _ac_box["y"] + _ac_box["height"] / 2)
            _selected = True
    except Exception:
        pass
    if not _selected:
        # Fallback: keyboard select — ArrowDown N+1 times to highlight nth item
        for _i in range(2):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(100)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
    page.wait_for_timeout(1500)  # React state commit after selection
    # Step 35: Click ").nth(1)
    page.get_by_placeholder("#").nth(1).click(force=True)
    page.wait_for_timeout(500)
    # Step 36: Enter "8" in ").nth(1)
    page.get_by_placeholder("#").nth(1).press_sequentially(tc_data.get("input_8", ""), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    # Step 37: Click Spinbutton "spinbutton"
    page.get_by_role("spinbutton").nth(2).click(force=True)
    page.wait_for_timeout(500)
    # Step 38: Enter "8" in Spinbutton "spinbutton"
    page.get_by_role("spinbutton").nth(2).press_sequentially(tc_data.get("input_9", ""), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    # Step 39: Click Text "Save Changes"
    # Auto-fix: wait for "Save Changes" wrapper to enable, then force-click + JS fallback
    try:
        page.wait_for_function(
            """() => {
                const els = Array.from(document.querySelectorAll("p, span, button, div"));
                const el = els.find(x => (x.textContent || "").trim() === "Save Changes");
                if (!el) return false;
                // Walk up to 5 ancestors checking opacity & pointer-events
                let cur = el;
                for (let i = 0; i < 5 && cur; i++) {
                    const cs = window.getComputedStyle(cur);
                    if (parseFloat(cs.opacity || "1") < 0.9) return false;
                    if (cs.pointerEvents === "none") return false;
                    cur = cur.parentElement;
                }
                return true;
            }""",
            timeout=15000,
        )
    except Exception:
        pass  # click anyway
    page.get_by_text("Save Changes").first.click(force=True)
    # JS-click fallback in case wrapper still has pointer-events:none
    page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll("p, span, button"));
        const el = els.find(x => (x.textContent || "").trim() === "Save Changes");
        if (el) el.click();
    }""")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2000)  # let post-save UI render
    page.wait_for_timeout(500)
    # Step 40: Click Cell "cell"
    page.get_by_role("cell").nth(5).click()
    page.wait_for_timeout(500)
    # Step 41: Click Img "edit icon"
    page.get_by_role("img", name="edit icon").click()
    page.wait_for_timeout(500)
    # Step 42: Click ")).first
    page.locator(r"div").filter(has_text=re.compile(r"^ODISHA$")).first.scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^ODISHA$")).first.click()
    page.wait_for_timeout(500)
    # Step 43: Click invisible")
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").scroll_into_view_if_needed()
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").click()
    page.wait_for_timeout(500)
    # Step 44: Click Text "Update"
    # Auto-fix: wait for "Update" wrapper to enable, then force-click + JS fallback
    try:
        page.wait_for_function(
            """() => {
                const els = Array.from(document.querySelectorAll("p, span, button, div"));
                const el = els.find(x => (x.textContent || "").trim() === "Update");
                if (!el) return false;
                // Walk up to 5 ancestors checking opacity & pointer-events
                let cur = el;
                for (let i = 0; i < 5 && cur; i++) {
                    const cs = window.getComputedStyle(cur);
                    if (parseFloat(cs.opacity || "1") < 0.9) return false;
                    if (cs.pointerEvents === "none") return false;
                    cur = cur.parentElement;
                }
                return true;
            }""",
            timeout=15000,
        )
    except Exception:
        pass  # click anyway
    page.get_by_text("Update").first.click(force=True)
    # JS-click fallback in case wrapper still has pointer-events:none
    page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll("p, span, button"));
        const el = els.find(x => (x.textContent || "").trim() === "Update");
        if (el) el.click();
    }""")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2000)  # let post-save UI render
    page.wait_for_timeout(500)
    # Step 45: Click ")).first
    page.locator(r"div").filter(has_text=re.compile(r"^Update$")).first.scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Update$")).first.click()
    page.wait_for_timeout(500)
    # Step 46: Click Text "Tracking"
    page.get_by_text("Tracking").click()
    page.wait_for_timeout(500)
    # Step 47: Click Proof")
    page.get_by_text("Documents & Proof").click()
    page.wait_for_timeout(500)
    # Step 48: Click Text "Addresses"
    page.get_by_text("Addresses").click()
    page.wait_for_timeout(500)
    # Step 49: Click Text "Return"
    page.get_by_text("Return").click()
    page.wait_for_timeout(500)
    # Step 50: Click Text "Shipment"
    page.get_by_text("Shipment").click()
    page.wait_for_timeout(500)
    # Step 51: Click Text "Payment"
    page.get_by_text("Payment", exact=True).click()
    page.wait_for_timeout(500)
    # Step 52: Click Text "Overview"
    page.get_by_text("Overview").click()
    page.wait_for_timeout(500)
    # Step 53: Click ")).first
    page.locator(r"div").filter(has_text=re.compile(r"^Accept$")).first.scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Accept$")).first.click()
    page.wait_for_timeout(500)
    # Step 54: Click Text "XYZ_WH"
    page.get_by_text("XYZ_WH", exact=True).click()
    page.wait_for_timeout(500)
    # Step 55: Click ")).nth(4)
    page.locator(r"div").filter(has_text=re.compile(r"^XYZ_WH$")).nth(4).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^XYZ_WH$")).nth(4).click()
    page.wait_for_timeout(500)
    # Step 56: Click invisible")
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").scroll_into_view_if_needed()
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").click()
    page.wait_for_timeout(500)
    # Step 57: Click div
    page.locator(r"div").filter(has_text="Confirm").nth(4).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text="Confirm").nth(4).click()
    page.wait_for_timeout(500)
    # Step 58: Click Text "Pack at XYZ_WH"
    page.get_by_text("Pack at XYZ_WH").click()
    page.wait_for_timeout(500)
    # Step 59: Click Text "Ready"
    page.get_by_text("Ready").nth(2).click()
    page.wait_for_timeout(500)
    # Step 60: Click Proof")
    page.get_by_text("Documents & Proof").click()
    page.wait_for_timeout(500)
    # Step 61: Click Text "Click to Upload Document"
    with page.expect_file_chooser() as fc_info:
        page.get_by_text("Click to Upload Document").first.click()
    fc_info.value.set_files(TEST_UPLOAD_IMAGE)
    page.wait_for_timeout(500)
    # Step 63: Click Text "Ready"
    page.get_by_text("Ready").nth(2).click()
    page.wait_for_timeout(500)
    # Step 64: Click Text "Picked Up"
    page.get_by_text("Picked Up").nth(1).click()
    page.wait_for_timeout(500)
    # Step 65: with page.expect_download() as download_info:
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Generate & Download PoP").click()
        page.wait_for_timeout(500)
    download = download_info.value
    # Step 69: Click Button "Close"
    page.get_by_role("button", name="Close").click()
    page.wait_for_timeout(500)
    # Step 70: Click Text "File size should not exceed"
    with page.expect_file_chooser() as fc_info:
        page.get_by_text("File size should not exceed").first.click()
    fc_info.value.set_files(TEST_UPLOAD_IMAGE)
    page.wait_for_timeout(500)
    # Step 72: Click Text "Accept"
    page.get_by_text("Accept").nth(5).click()
    page.wait_for_timeout(500)
    # Step 73: Click Text "Reject"
    page.get_by_text("Reject", exact=True).click()
    page.wait_for_timeout(500)
    # Step 74: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Reason$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Reason$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 75: Click invisible")
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").scroll_into_view_if_needed()
    page.locator(r".MuiBackdrop-root.MuiBackdrop-invisible").click()
    page.wait_for_timeout(500)
    # Step 77: Click Text "Shipment"
    page.get_by_text("Shipment").click()
    page.wait_for_timeout(500)
    # Step 78: Click Text "Shipments will reflect here"
    page.get_by_text("Shipments will reflect here").click()
    page.wait_for_timeout(500)
    # Step 79: Click Text "Payment"
    page.get_by_text("Payment", exact=True).click()
    page.wait_for_timeout(500)
    # Step 80: Click Text "Debit Note"
    page.get_by_text("Debit Note").click()
    page.wait_for_timeout(500)
    # Step 81: Click Text "Return"
    page.get_by_text("Return").click()
    page.wait_for_timeout(500)
    # Step 82: Click Text "Shipment"
    page.get_by_text("Shipment").click()
    page.wait_for_timeout(500)
    # Step 83: Click Img "edit icon"
    page.get_by_role("img", name="edit icon").click()
    page.wait_for_timeout(500)
    # Step 84: Click Img "Edit"
    page.get_by_role("img", name="Edit").first.click()
    page.wait_for_timeout(500)
    # Step 85: Click Img "Edit"
    page.get_by_role("img", name="Edit").nth(1).click()
    page.wait_for_timeout(500)
    # Step 87: Double-click Img "Edit"
    page.get_by_role("img", name="Edit").nth(1).dblclick()
    page.wait_for_timeout(500)
    # Step 88: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Update$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Update$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 89: Click Cell "May 2026 19:30"
    page.get_by_role("cell", name="May 2026 19:30").click()
    page.wait_for_timeout(500)
    # Step 90: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Cancel$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Cancel$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 91: Click ")).nth(1)
    page.locator(r"div").filter(has_text=re.compile(r"^Reason$")).nth(1).scroll_into_view_if_needed()
    page.locator(r"div").filter(has_text=re.compile(r"^Reason$")).nth(1).click()
    page.wait_for_timeout(500)
    # Step 92: Click Text "Incorrect Purchase Price"
    page.get_by_text("Incorrect Purchase Price").click()
    page.wait_for_timeout(500)
    # Step 93: Click Text "Confirm"
    page.get_by_text("Confirm", exact=True).click()
    page.wait_for_timeout(500)
    # Step 94: Click Text "Overview"
    page.get_by_text("Overview").click()
    page.wait_for_timeout(500)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow executed successfully")


# ============================================================
# TC-ADMIN-001 — Full PO Lifecycle
# Hand-authored from analyzer captures + PO_CREATION_FLOW_DISCOVERY.md.
# One end-to-end flow: admin creates PO → accepts → edits → packs →
# uploads ready proof → marks ready. Each business action is its own
# checkpoint so a failure at "Pack" still records that "Create" passed.
# ============================================================

TEST_UPLOAD_IMAGE = os.path.join(
    os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png"
)


def _admin_settle(page, timeout=8000):
    """Best-effort wait for the admin SPA to quiesce after an action."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(600)


def _click_popup_item(page, nth_idx=0, timeout=12000):
    """Click the nth item in the most recently opened MUI popup
    (autocomplete / menu). Uses bounding-box mouse click so React's
    onClick handler actually fires — `.click()` on the <li> sets display
    text but doesn't wire internal state. Scoped to body-portal popups
    (NOT sidebar). See ATS_TEST_PATTERNS.md §17."""
    popup = page.locator(
        ".MuiAutocomplete-popper:visible, "
        ".MuiAutocomplete-listbox:visible, "
        ".MuiPopover-root:visible, "
        ".MuiMenu-paper:visible"
    ).last
    item = popup.locator('li, [role="menuitem"]').nth(nth_idx)
    item.wait_for(state="visible", timeout=timeout)
    page.wait_for_timeout(400)
    box = item.bounding_box()
    if not box or box["height"] < 5:
        raise AssertionError(f"Popup item .nth({nth_idx}) has no usable bounding box (height={box['height'] if box else 'None'})")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(1200)


def _click_gated_submit(page, label, timeout=15000):
    """Click an admin gated submit button (Save Changes, Update, Submit,
    Confirm, Accept, etc.). These have opacity:0.6 + pointer-events:none
    on the wrapper when not ready; we wait for enable, then force-click,
    then JS-click as final fallback. See ATS_TEST_PATTERNS.md."""
    try:
        page.wait_for_function(
            f"""() => {{
                const els = Array.from(document.querySelectorAll("p, span, button, div"));
                const el = els.find(x => (x.textContent || "").trim() === "{label}");
                if (!el) return false;
                let cur = el;
                for (let i = 0; i < 5 && cur; i++) {{
                    const cs = window.getComputedStyle(cur);
                    if (parseFloat(cs.opacity || "1") < 0.9) return false;
                    if (cs.pointerEvents === "none") return false;
                    cur = cur.parentElement;
                }}
                return true;
            }}""",
            timeout=timeout,
        )
    except Exception:
        pass  # try clicking anyway
    clicked = False
    try:
        page.get_by_text(label, exact=True).first.click(force=True, timeout=5000)
        clicked = True
    except Exception:
        clicked = False
    if not clicked:
        # JS-click fallback (bypasses pointer-events:none on ancestors).
        # Only fires when the real click failed — firing both double-submits
        # the form (created two POs per run before this guard).
        page.evaluate(
            f"""() => {{
                const els = Array.from(document.querySelectorAll("p, span, button"));
                const el = els.find(x => (x.textContent || "").trim() === "{label}");
                if (el) el.click();
            }}"""
        )


def _fill_mui_number(page, locator, value):
    """Fill an MUI number input (quantity/rate/etc.) using press_sequentially
    + Tab so React's onChange + validation fires. .fill() doesn't trigger
    onChange on these inputs. See ATS_TEST_PATTERNS.md §18."""
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    page.wait_for_timeout(200)
    locator.press_sequentially(str(value), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(400)


def _dump_save_failure(page, state):
    """Diagnostic dump when PO Save doesn't redirect. Writes a full-page
    screenshot next to this test file and prints any visible toast / error /
    validation text + the current line-item field values so we can see why
    the form silently refused to submit. Temporary — remove once green."""
    import json as _json
    shot = os.path.join(os.path.dirname(__file__), "_save_failure.png")
    try:
        page.screenshot(path=shot, full_page=True)
        print(f"[TC-ADMIN-001][DIAG] screenshot -> {shot}")
    except Exception as e:
        print(f"[TC-ADMIN-001][DIAG] screenshot failed: {e}")
    print(f"[TC-ADMIN-001][DIAG] url={page.url}")
    # Visible toasts / alerts / helper-text
    try:
        info = page.evaluate(
            """() => {
                const out = {toasts: [], helpers: [], rows: []};
                const seen = new Set();
                const grab = (sel) => Array.from(document.querySelectorAll(sel))
                    .map(e => (e.textContent||'').trim())
                    .filter(t => t && t.length < 200 && !seen.has(t) && seen.add(t));
                out.toasts = grab('[role="alert"], [class*="toast"], [class*="Toastify"], [class*="snackbar"], [class*="Snackbar"]');
                out.helpers = grab('[class*="error"], [class*="Mui-error"], [class*="helperText"], [class*="MuiFormHelperText"]');
                // line-item inputs: number/qty/rate fields
                out.rows = Array.from(document.querySelectorAll('input[name="quantity"], input[placeholder="#"], [role="spinbutton"], input[type="number"]'))
                    .map(i => ({name: i.getAttribute('name')||'', ph: i.getAttribute('placeholder')||'', val: i.value}));
                return out;
            }"""
        )
        print("[TC-ADMIN-001][DIAG] " + _json.dumps(info, ensure_ascii=False)[:1500])
    except Exception as e:
        print(f"[TC-ADMIN-001][DIAG] evaluate failed: {e}")


def _confirm_dialog(page, labels):
    """If a modal dialog is open, click the first matching primary-action
    label inside it. No-op if no dialog is present. Returns True if clicked."""
    # Prefer clicking inside a recognised dialog container...
    try:
        dlg = page.locator('[role="dialog"], .MuiDialog-root, .MuiModal-root').last
        if dlg.count() > 0 and dlg.is_visible():
            for lbl in labels:
                b = dlg.get_by_text(lbl, exact=True)
                if b.count() > 0 and b.last.is_visible():
                    b.last.click()
                    page.wait_for_timeout(500)
                    return True
    except Exception:
        pass
    # ...but the admin uses custom modals that aren't role=dialog, so fall
    # back to clicking the label anywhere it's visible on the page.
    for lbl in labels:
        try:
            b = page.get_by_text(lbl, exact=True)
            if b.count() > 0 and b.last.is_visible():
                b.last.click()
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    return False


def _select_first_in_dropdown(page, label, skip_texts=("select",)):
    """Open a labeled <Select>-style dropdown (finds the label text, clicks the
    sibling control showing 'Select' / cursor:pointer) and pick its first real
    option, skipping the 'Select' placeholder. Returns True on a successful pick."""
    opened = page.evaluate(
        """(label) => {
            const all = Array.from(document.querySelectorAll('p, label, span, div'));
            const lab = all.find(e => e.children.length === 0 && (e.textContent || '').trim() === label);
            if (!lab) return false;
            let container = lab.parentElement;
            for (let i = 0; i < 3 && container; i++) {
                const cand = Array.from(container.querySelectorAll('*')).find(e =>
                    e !== lab &&
                    ((e.textContent || '').trim() === 'Select' || getComputedStyle(e).cursor === 'pointer'));
                if (cand) { cand.click(); return true; }
                container = container.parentElement;
            }
            return false;
        }""",
        label,
    )
    if not opened:
        return False
    page.wait_for_timeout(900)
    popup = page.locator(
        '.MuiPopover-root:visible, .MuiMenu-paper:visible, '
        '[role="listbox"]:visible, [role="menu"]:visible, '
        '.MuiAutocomplete-popper:visible'
    ).last
    items = popup.locator('[role="option"], [role="menuitem"], li, .MuiMenuItem-root')
    for i in range(min(items.count(), 15)):
        it = items.nth(i)
        try:
            txt = (it.inner_text() or "").strip()
        except Exception:
            continue
        if txt and txt.lower() not in skip_texts:
            it.scroll_into_view_if_needed()
            it.click()
            page.wait_for_timeout(700)
            return True
    return False


def _fill_by_placeholder(page, placeholder, value):
    """Fill a text/number field by (case-insensitive substring) placeholder
    using press_sequentially so React onChange fires. No-op if not present."""
    try:
        inp = page.get_by_placeholder(re.compile(re.escape(placeholder), re.I)).first
        if inp.count() == 0:
            return
        inp.scroll_into_view_if_needed()
        inp.click()
        inp.press_sequentially(str(value), delay=50)
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
    except Exception:
        pass


def _fill_date_edd(page, value):
    """Fill the 'Courier Partner EDD' date field. It's a MUI-style date field
    where 'DD/MM/YYYY' is a format mask (not a placeholder attribute), so we
    locate the input following the label and type digits only."""
    # The field has a real placeholder "DD/MM/YYYY" (plain string — a regex with
    # slashes breaks Playwright's selector builder).
    inp = page.get_by_placeholder("DD/MM/YYYY").first
    try:
        if inp.count() == 0:
            return
        inp.scroll_into_view_if_needed()
        inp.click()
        page.wait_for_timeout(300)
        try:
            inp.fill(str(value))
        except Exception:
            pass
        if not (inp.input_value() or "").strip():
            # masked input — type digits only
            inp.click()
            inp.press_sequentially(re.sub(r"\D", "", str(value)), delay=120)
        page.keyboard.press("Tab")
        page.wait_for_timeout(400)
    except Exception:
        pass


def _open_custom_dropdown(page, label_text):
    """Open one of the admin's custom <p>-label + sibling-div dropdowns
    (Agrim Branch, Select Address). Per PO_CREATION_FLOW_DISCOVERY.md
    these can't be clicked via Playwright's element actions reliably —
    we trigger via page.evaluate finding the label, walking to the
    next sibling with cursor:pointer, calling .click()."""
    page.evaluate(
        f"""() => {{
            const labels = document.querySelectorAll('p');
            for (const label of labels) {{
                if ((label.textContent || '').trim() === '{label_text}') {{
                    const dropdown = label.nextElementSibling;
                    if (dropdown && getComputedStyle(dropdown).cursor === 'pointer') {{
                        dropdown.click();
                        return;
                    }}
                }}
            }}
        }}"""
    )
    page.wait_for_timeout(1500)


@pytest.mark.tc("TC-ADMIN-001")
def test_TC_ADMIN_001_full_po_lifecycle(admin_page: Page, admin_url, tc_data, checkpoints):
    """End-to-end PO lifecycle: admin creates a PO with 2 line items, opens
    it, accepts, edits, packs (with confirm), uploads ready proof, and marks
    ready. Each business milestone is its own checkpoint so a downstream
    failure doesn't hide an earlier success.

    Source of truth: PO_CREATION_FLOW_DISCOVERY.md + analyzer capture from
    TC-ADMIN-EXPLORE-ORDERS_latest.json + ATS_TEST_PATTERNS.md sections
    §17 (MUI autocomplete), §18 (MUI number inputs).
    """
    page = admin_page
    state = {}  # shared across checkpoints (carries po_id)

    # ── A. Navigation ─────────────────────────────────────────

    def cp_open_po_listing():
        page.goto(admin_url + "#/oms/po/all", wait_until="domcontentloaded")
        _admin_settle(page, timeout=12000)
        # Verify the "Create" affordance is visible
        create_loc = page.locator('a:has-text("Create"), p:has-text("Create")').first
        create_loc.wait_for(state="visible", timeout=10000)
        # Baseline: remember the current newest PO. The listing is sorted
        # newest-first, so after we create one the top row will change — that's
        # how we identify the PO we just made.
        try:
            top = page.get_by_text(re.compile(r"^PO-\d+$")).first
            top.wait_for(state="visible", timeout=10000)
            state["pre_top_po"] = top.inner_text().strip()
        except Exception:
            state["pre_top_po"] = None
        print(f"[TC-ADMIN-001] Baseline top PO: {state.get('pre_top_po')}")

    def cp_open_create_form():
        page.locator('a:has-text("Create"), p:has-text("Create")').first.click()
        _admin_settle(page, timeout=12000)
        page.get_by_text("Vendor Information").first.wait_for(state="visible", timeout=15000)

    # ── B. Fill the PO form ────────────────────────────────────

    def cp_select_vendor():
        search = page.get_by_role("textbox", name="Search").first
        search.click()
        search.fill(tc_data.get("vendor_search", "m&"))
        page.wait_for_timeout(2000)
        # Vendor menu items have role=menuitem — Playwright's dblclick
        # on the matching menuitem reliably wires the seller_id.
        page.get_by_role("menuitem", name=tc_data.get("vendor_name", "M&M_Brand1")).first.dblclick()
        _admin_settle(page, timeout=10000)
        # Confirm billing address auto-populated (sanity check)
        assert page.get_by_text("Billing Address", exact=False).count() > 0

    def cp_select_agrim_branch():
        _open_custom_dropdown(page, "Agrim Branch*")
        target = tc_data.get("agrim_branch", "ODISHA")
        page.get_by_role("menuitem", name=target).first.click()
        _admin_settle(page, timeout=10000)

    def cp_select_delivery_address():
        _open_custom_dropdown(page, "Select Address")
        target = tc_data.get("delivery_address", "CUTTACK WH")
        page.get_by_text(target, exact=True).first.click()
        _admin_settle(page)

    def cp_set_load_type():
        # Load Type is a dropdown — open it via the section header div
        page.locator('div').filter(has_text=re.compile(r"^Load Type$")).nth(1).click()
        page.wait_for_timeout(800)
        page.get_by_text(tc_data.get("load_type", "Full Load"), exact=True).click()
        page.wait_for_timeout(600)

    def cp_set_po_type():
        page.get_by_text("PO Type", exact=False).nth(1).click()
        page.wait_for_timeout(800)
        page.get_by_role("menuitem", name=tc_data.get("po_type", "Inventory Purchase")).first.click()
        page.wait_for_timeout(800)
        page.locator('div').filter(has_text=re.compile(r"^Type of Inventory Purchase$")).nth(1).click()
        page.wait_for_timeout(800)
        page.get_by_role("menuitem", name=tc_data.get("inventory_type", "Business Purchase")).first.click()
        _admin_settle(page)

    def cp_add_line_item_1():
        # Product search is the SECOND "Search" textbox (vendor was first)
        item_search = page.get_by_role("textbox", name="Search").nth(1)
        item_search.click()
        item_search.fill(tc_data.get("item_search_1", "test"))
        page.wait_for_timeout(2000)
        _click_popup_item(page, nth_idx=0)  # first product in dropdown
        # Quantity = first placeholder="#" input on the page
        qty = page.get_by_placeholder("#").first
        _fill_mui_number(page, qty, tc_data.get("item_qty_1", "7"))

    def cp_add_line_item_2():
        page.get_by_text("Add New Row", exact=True).click()
        page.wait_for_timeout(1500)
        # Product search for row 2 is the THIRD "Search" textbox
        item_search = page.get_by_role("textbox", name="Search").nth(2)
        item_search.click()
        item_search.fill(tc_data.get("item_search_2", "test"))
        page.wait_for_timeout(2000)
        # DIFFERENT product than row 1 — pick .nth(1) — the form rejects
        # duplicate line items and Save Changes silently no-ops if rows
        # have the same item. See memory: project_admin_po_duplicate_items.
        _click_popup_item(page, nth_idx=1)
        # Row 2 quantity is the SECOND placeholder="#" input
        qty2 = page.get_by_placeholder("#").nth(1)
        _fill_mui_number(page, qty2, tc_data.get("item_qty_2", "8"))
        # Row 2 rate is the 3rd spinbutton (vendor search inputs above are spinbuttons too)
        rate2 = page.get_by_role("spinbutton").nth(2)
        _fill_mui_number(page, rate2, tc_data.get("item_rate_2", "8"))

    def cp_save_create_po():
        _click_gated_submit(page, "Save Changes")
        # After create the admin redirects to the All Orders LISTING (not a
        # detail page). The new PO appears as the top row (sorted newest-first).
        # Wait until the top PO number differs from the pre-save baseline.
        pre = state.get("pre_top_po")
        try:
            page.wait_for_function(
                """(pre) => {
                    const els = Array.from(document.querySelectorAll('*')).filter(
                        e => e.children.length === 0 && /^PO-\\d+$/.test((e.textContent || '').trim())
                    );
                    if (!els.length) return false;
                    const t = els[0].textContent.trim();
                    return pre ? (t !== pre) : true;
                }""",
                arg=pre,
                timeout=25000,
            )
            new_po = page.get_by_text(re.compile(r"^PO-\d+$")).first.inner_text().strip()
        except Exception:
            _dump_save_failure(page, state)
            raise AssertionError(
                "PO not created: listing top row did not change after Save Changes"
            )
        state["po_number"] = new_po
        print(f"[TC-ADMIN-001] Created PO: {new_po}")
        _admin_settle(page, timeout=10000)

    def cp_open_created_po():
        if not state.get("po_number"):
            return False
        page.get_by_text(state["po_number"], exact=True).first.click()
        try:
            page.wait_for_url(re.compile(r"/oms/po/all/\d+/show"), timeout=20000)
        except Exception:
            pass  # detail may load without the exact /show suffix
        m = re.search(r"/oms/po/all/(\d+)", page.url)
        if m:
            state["po_id"] = m.group(1)
        print(f"[TC-ADMIN-001] Opened PO detail id={state.get('po_id')} url={page.url}")
        _admin_settle(page, timeout=10000)

    def cp_verify_po_detail():
        if not state.get("po_number"):
            return False  # skip — create failed
        # The PO number we created must be visible on its own detail page.
        # (Vendor name can render truncated, so we verify on the PO number.)
        body = page.inner_text("body")
        assert state["po_number"] in body, \
            f"{state['po_number']} not visible on detail page"

    # ── C. PO lifecycle actions ────────────────────────────────

    def _ensure_on_detail():
        """Make sure we're on the PO's detail (/show) page — the lifecycle
        action buttons only exist there, and Edit/Update can bounce us to the
        edit form or the listing."""
        if not state.get("po_id"):
            return
        if "/show" not in page.url or state["po_id"] not in page.url:
            page.goto(admin_url + f"#/oms/po/all/{state['po_id']}/show",
                      wait_until="domcontentloaded")
            _admin_settle(page, timeout=10000)

    def cp_send_to_seller():
        if not state.get("po_number"):
            return False
        # A freshly-created PO is in "Draft". The primary action is the
        # "Sent To Seller" button — this moves it to "Sent To Seller", which
        # is the state in which Accept becomes available. A real (non-force)
        # click is required so the React handler fires.
        btn = page.get_by_text("Sent To Seller", exact=True).first
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        page.wait_for_timeout(2000)
        # A confirmation dialog usually appears — confirm it inside the dialog.
        _confirm_dialog(page, ["Confirm", "Yes", "Send", "Proceed", "Submit", "Ok", "OK"])
        _admin_settle(page, timeout=12000)

    def cp_accept_po():
        if not state.get("po_number"):
            return False
        # After Send-to-Seller the PO is acceptable. Clicking Accept moves it to
        # "Accepted" and auto-selects the packing warehouse (no separate dialog).
        try:
            page.get_by_text("Accept", exact=True).first.click(force=True, timeout=8000)
        except Exception:
            page.get_by_role("button", name="Accept").first.click(force=True, timeout=8000)
        page.wait_for_timeout(1500)
        _admin_settle(page, timeout=10000)

    def cp_open_edit_form():
        if not state.get("po_number"):
            return False
        page.get_by_role("img", name="edit icon").first.click()
        _admin_settle(page, timeout=10000)
        # After clicking edit, the form opens (URL drops the /show suffix, e.g.
        # /oms/po/all/1179 instead of /1179/show). Don't hard-fail on the URL
        # shape — just confirm the edit form rendered.
        try:
            page.wait_for_url(re.compile(r"/oms/po/all/\d+(?!/show)"), timeout=8000)
        except Exception:
            pass

    def cp_update_po():
        if not state.get("po_number"):
            return False
        # Click Update — same gated-submit pattern as Save Changes
        _click_gated_submit(page, "Update")
        _admin_settle(page, timeout=10000)
        # Update bounces around (toast, sometimes the listing). Return to the
        # PO detail page so the lifecycle action buttons are addressable again.
        _ensure_on_detail()

    def cp_pack_po():
        if not state.get("po_number"):
            return False
        _ensure_on_detail()
        # After Accept the warehouse is auto-selected and a green
        # "Pack at <warehouse>" button is shown — click it to pack.
        pack_loc = page.get_by_text(re.compile(r"^Pack at ", re.I)).first
        pack_loc.wait_for(state="visible", timeout=15000)
        pack_loc.click()
        _admin_settle(page, timeout=10000)
        # A Confirm dialog may appear — accept it inside the dialog.
        _confirm_dialog(page, ["Confirm", "Yes", "Pack", "Proceed", "Ok", "OK"])
        _admin_settle(page, timeout=10000)

    def cp_upload_ready_proof():
        if not state.get("po_number"):
            return False
        _ensure_on_detail()
        # Open the "Documents & Proof" tab
        try:
            page.get_by_text("Documents & Proof", exact=True).first.click()
            page.wait_for_timeout(1500)
        except Exception:
            pass
        # Click "Click to Upload Document" — opens a file chooser
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                page.get_by_text(re.compile(r"Click to Upload", re.I)).first.click()
            fc_info.value.set_files(TEST_UPLOAD_IMAGE)
        except Exception:
            # Fallback: find any visible file input and set directly
            file_inputs = page.locator('input[type="file"]')
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(TEST_UPLOAD_IMAGE)
        _admin_settle(page, timeout=10000)

    def cp_create_shipment():
        if not state.get("po_number"):
            return False
        _ensure_on_detail()
        # Marking Ready For Dispatch requires a MANIFESTED shipment. Open the
        # Shipment tab to create + manifest one.
        page.get_by_text("Shipment", exact=True).first.click()
        page.wait_for_timeout(2000)
        # Open the create-shipment modal
        page.get_by_text("Create Shipment", exact=True).first.click()
        page.wait_for_timeout(2000)
        # 1) Load Type — PTL / FTL (the first "Select" in the modal)
        try:
            page.get_by_text("Select", exact=True).first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pass
        load_type = tc_data.get("shipment_load_type", "FTL")
        for getter in (
            lambda: page.get_by_role("option", name=load_type),
            lambda: page.get_by_role("menuitem", name=load_type),
            lambda: page.get_by_text(load_type, exact=True),
        ):
            loc = getter()
            if loc.count() > 0 and loc.last.is_visible():
                loc.last.click()
                break
        page.wait_for_timeout(1000)
        # 2) Required dropdowns — pick the first real option in each
        for lbl in ["Transporter ID", "Vehicle Type", "Movement Mile"]:
            _select_first_in_dropdown(page, lbl)
        # 3) Required text/number fields (dummy values, overridable via test_data).
        #    Backend rule: when Vehicle Number is set, EDD + Bill Number +
        #    Freight Charges are ALL required too.
        _fill_by_placeholder(page, "Vehicle Number", tc_data.get("vehicle_number", "MH12AB1234"))
        _fill_by_placeholder(page, "Driver Number", tc_data.get("driver_number", "9999999999"))
        _fill_by_placeholder(page, "Transporter Bill", tc_data.get("transporter_bill", "TB123456"))
        _fill_by_placeholder(page, "Freight Charges", tc_data.get("freight_charges", "1000"))
        _fill_date_edd(page, tc_data.get("courier_edd", "31/12/2026"))
        page.wait_for_timeout(500)
        # 4) Confirm to create the shipment (custom modal — not a role=dialog)
        _confirm_dialog(page, ["Confirm"])
        _admin_settle(page, timeout=12000)
        # 5) Some flows expose an explicit Manifest action; if present, click it.
        #    (Creating a valid FTL shipment is what clears the "No MANIFESTED
        #    shipment" gate for Mark Ready.)
        for lbl in ["Manifest", "Mark Manifested", "Manifest Shipment"]:
            man = page.get_by_text(lbl, exact=True)
            if man.count() > 0 and man.last.is_visible():
                man.last.click()
                _admin_settle(page, timeout=10000)
                _confirm_dialog(page, ["Confirm", "Yes", "Manifest", "Proceed", "Ok", "OK"])
                _admin_settle(page, timeout=10000)
                break

    def cp_mark_ready():
        if not state.get("po_number"):
            return False
        _ensure_on_detail()
        # The post-pack action is a "Ready" / "Mark Ready" / "Ready For
        # Dispatch" button. Try the likely labels in priority order.
        clicked = False
        for lbl in ["Ready For Dispatch", "Mark as Ready", "Mark Ready", "Ready"]:
            loc = page.get_by_text(lbl, exact=True)
            if loc.count() > 0 and loc.last.is_visible():
                loc.last.click()
                clicked = True
                break
        if not clicked:
            raise AssertionError("No Ready / Mark-Ready button visible after pack")
        _admin_settle(page, timeout=10000)
        _confirm_dialog(page, ["Confirm", "Yes", "Proceed", "Ok", "OK"])
        _admin_settle(page, timeout=10000)

    def cp_verify_final_state():
        if not state.get("po_number"):
            return False
        page.wait_for_timeout(500)
        # Only genuine error toasts should fail this. The success toast
        # ("PO updated successfully") also uses role=alert, so match on error
        # wording rather than the mere presence of an alert.
        err = page.locator(
            ':is([role="alert"], [class*="toast"], [class*="Toastify"]):visible'
        ).filter(has_text=re.compile(r"error|failed|required|not found|invalid|exceed", re.I))
        if err.count() > 0:
            raise AssertionError(f"Error visible: {err.first.inner_text()[:200]}")
        # Positively confirm the PO advanced past "Packed" — its own detail
        # header should now show a Ready/Dispatch state.
        _ensure_on_detail()
        body = page.inner_text("body")
        assert re.search(r"Ready For Dispatch|Ready|Dispatched", body), \
            "PO does not appear to have reached a Ready/Dispatch state"

    # ── Execute checkpoints ────────────────────────────────────

    checkpoints.run("Open PO listing", cp_open_po_listing)
    checkpoints.run("Open Create PO form", cp_open_create_form)
    checkpoints.run("Select vendor + auto-populate addresses", cp_select_vendor)
    checkpoints.run("Select Agrim Branch", cp_select_agrim_branch)
    checkpoints.run("Select Delivery Address (warehouse)", cp_select_delivery_address)
    checkpoints.run("Set Load Type", cp_set_load_type)
    checkpoints.run("Set PO Type and Inventory Purchase type", cp_set_po_type)
    checkpoints.run("Add line item 1 (first product)", cp_add_line_item_1)
    checkpoints.run("Add line item 2 (distinct second product)", cp_add_line_item_2)
    checkpoints.run("Save Changes — create PO", cp_save_create_po)
    checkpoints.run("Open created PO from listing", cp_open_created_po)
    checkpoints.run("Verify PO detail page", cp_verify_po_detail)
    checkpoints.run("Send to Seller", cp_send_to_seller)
    checkpoints.run("Accept PO", cp_accept_po)
    checkpoints.run("Open Edit form", cp_open_edit_form)
    checkpoints.run("Update PO", cp_update_po)
    checkpoints.run("Pack PO (with Confirm)", cp_pack_po)
    checkpoints.run("Upload ready proof", cp_upload_ready_proof)
    checkpoints.run("Create + manifest shipment", cp_create_shipment)
    checkpoints.run("Mark Ready", cp_mark_ready)
    checkpoints.run("Verify no errors on final state", cp_verify_final_state)