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
    page.wait_for_timeout(1500)  # wait for MUI autocomplete results
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    # Step 29: Click "]")
    page.locator(r"input[name=\"quantity\"]").scroll_into_view_if_needed()
    page.locator(r"input[name=\"quantity\"]").click()
    page.wait_for_timeout(500)
    # Step 30: Enter "7" in "]")
    page.locator(r"input[name=\"quantity\"]").scroll_into_view_if_needed()
    page.locator(r"input[name=\"quantity\"]").press_sequentially(tc_data.get("input_6", ""), delay=80)
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
    page.wait_for_timeout(1500)  # wait for MUI autocomplete results
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    # Step 35: Click ").nth(1)
    page.get_by_placeholder("#").nth(1).click()
    page.wait_for_timeout(500)
    # Step 36: Enter "8" in ").nth(1)
    page.get_by_placeholder("#").nth(1).press_sequentially(tc_data.get("input_8", ""), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    # Step 37: Click Spinbutton "spinbutton"
    page.get_by_role("spinbutton").nth(2).click()
    page.wait_for_timeout(500)
    # Step 38: Enter "8" in Spinbutton "spinbutton"
    page.get_by_role("spinbutton").nth(2).press_sequentially(tc_data.get("input_9", ""), delay=80)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    # Step 39: Click Text "Save Changes"
    page.get_by_text("Save Changes").click()
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
    page.get_by_text("Update").click()
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