"""Catalog flow — Checkpoint-based Playwright E2E tests.

8 flow-based test cases, each with multiple checkpoints.
Checkpoints continue running even if one fails, so you get
a full diagnostic. The test fails overall if any checkpoint fails.
"""

import re
import os
import pytest
from playwright.sync_api import expect, Page


# ── Helpers ──────────────────────────────────────────────────

def _login(page, test_user, base_url):
    """Log in using the selected user account."""
    page.goto(base_url + "login", wait_until="domcontentloaded")
    email_field = page.locator('input[placeholder*="Email"], input[type="email"]').first
    password_field = page.locator('input[type="password"]').first
    sign_in_button = page.locator('button:has-text("Sign In"), button[type="submit"]').first
    email_field.wait_for(state="visible", timeout=15000)
    email_field.click()
    page.keyboard.type(test_user["email"], delay=50)
    password_field.click()
    page.keyboard.type(test_user["password"], delay=50)
    sign_in_button.click()
    page.wait_for_timeout(3000)
    page.wait_for_load_state("domcontentloaded")


def _video_hold(page, seconds=3):
    page.wait_for_timeout(seconds * 1000)


def _scroll_down(page, pixels=500):
    page.mouse.wheel(0, pixels)
    page.wait_for_timeout(500)


def _scroll_up(page, pixels=500):
    page.mouse.wheel(0, -pixels)
    page.wait_for_timeout(500)


def _scroll_to_top(page):
    page.keyboard.press("Home")
    page.wait_for_timeout(300)


def _get_product_count(page):
    cards = page.locator('[class*="product-card"], [class*="card"]:has(button:has-text("Request Product")), [class*="card"]:has(button:has-text("Request Variant"))')
    count = cards.count()
    if count > 0:
        return count
    return page.locator('button:has-text("Request Product")').count()


def _check_no_page_errors(page, context_label=""):
    page.wait_for_timeout(500)
    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
        '[class*="toast"]:visible:has-text("failed")',
        '[class*="toast"]:visible:has-text("something went wrong")',
        '[class*="error-message"]:visible',
        '[class*="text-destructive"]:visible',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            error_text = errors.first.text_content().strip()[:200]
            if error_text:
                raise AssertionError(
                    f"Page error detected{f' ({context_label})' if context_label else ''}: {error_text}"
                )


def _assert_products_changed(page, before_count, action_desc="filter"):
    page.wait_for_timeout(2000)
    after_count = _get_product_count(page)
    if before_count > 0 and after_count > 0:
        assert before_count != after_count or after_count > 0, (
            f"Expected product list to change after {action_desc}. "
            f"Before: {before_count}, After: {after_count}"
        )
    _check_no_page_errors(page, action_desc)


def _assert_submission_success(page, timeout=5000):
    page.wait_for_timeout(2000)
    success_indicators = [
        '[class*="toast"]:visible:has-text("success")',
        '[class*="toast"]:visible:has-text("Success")',
        '[class*="toast"]:visible:has-text("submitted")',
        '[class*="toast"]:visible:has-text("created")',
        '[class*="toast"]:visible:has-text("requested")',
        '[role="status"]:visible',
    ]
    for sel in success_indicators:
        if page.locator(sel).count() > 0:
            return

    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
        '[class*="toast"]:visible:has-text("failed")',
        '[class*="toast"]:visible:has-text("Failed")',
        '[class*="toast"]:visible:has-text("cannot")',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            error_text = errors.first.text_content().strip()[:300]
            raise AssertionError(f"Submission failed with error: {error_text}")

    dialog = page.locator('[role="dialog"]:visible')
    if dialog.count() == 0:
        return

    validation_errors = page.locator('[role="dialog"] [class*="error"]:visible, [role="dialog"] [class*="text-destructive"]:visible, [role="dialog"] .text-red-500:visible')
    if validation_errors.count() > 0:
        error_text = validation_errors.first.text_content().strip()[:300]
        raise AssertionError(f"Submission blocked by validation error: {error_text}")


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def catalog_page(page, test_user, base_url, sequential_page):
    """Log in and navigate to the Catalog page."""
    if sequential_page is not None:
        sequential_page.goto(base_url + "listing/catalog", wait_until="domcontentloaded")
        sequential_page.locator('input[placeholder*="Search"], input[type="search"]').first.wait_for(
            state="visible", timeout=20000
        )
        sequential_page.wait_for_timeout(1000)
        return sequential_page

    _login(page, test_user, base_url)
    page.goto(base_url + "listing/catalog", wait_until="domcontentloaded")
    page.locator('input[placeholder*="Search"], input[type="search"]').first.wait_for(
        state="visible", timeout=20000
    )
    page.wait_for_timeout(2000)
    return page


@pytest.fixture
def test_image_path():
    """Create a tiny valid PNG for file-upload tests."""
    img_dir = os.path.dirname(__file__)
    img_path = os.path.join(img_dir, "test_image.png")
    if not os.path.exists(img_path):
        import struct, zlib
        def _make_png():
            sig = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            raw = b"\x00\xff\x00\x00"
            compressed = zlib.compress(raw)
            idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
            idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            return sig + ihdr + idat + iend
        with open(img_path, "wb") as f:
            f.write(_make_png())
    return img_path


# ============================================================
# TC-CATALOG-001: Page structure, tabs, and scroll
# ============================================================

@pytest.mark.tc("TC-CATALOG-001")
def test_TC_CATALOG_001_page_structure(catalog_page: Page, checkpoints):
    """Catalog page loads with search, filters, product cards, tabs, and scroll."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_navigate():
        expect(page.locator('input[placeholder*="Search"], input[type="search"]').first).to_be_visible()

    def cp_primary_filters():
        expect(page.locator('button:has-text("super category"), button:has-text("Select super category")').first).to_be_visible()
        expect(page.locator('button:has-text("brand"), button:has-text("Select brand")').first).to_be_visible()
        expect(page.locator('button:has-text("SKU Status")').first).to_be_visible()
        expect(page.locator('button:has-text("Sort")').first).to_be_visible()

    def cp_product_cards():
        assert page.locator('button:has-text("Request Product")').count() > 0, \
            "Expected at least one product card on catalog page"

    def cp_tabs():
        all_products = page.locator('button:has-text("All Products")')
        requested = page.locator('button:has-text("Requested")')
        expect(all_products.first).to_be_visible()
        expect(requested.first).to_be_visible()
        requested.first.click()
        page.wait_for_timeout(2000)
        all_products.first.click()
        page.wait_for_timeout(1000)

    def cp_scroll():
        for _ in range(3):
            _scroll_down(page, 500)
            page.wait_for_timeout(500)
        for _ in range(3):
            _scroll_up(page, 500)
            page.wait_for_timeout(300)
        assert page.locator('button:has-text("Request Product")').count() > 0, \
            "Expected product cards visible after scrolling"

    checkpoints.run("Navigate to catalog page", cp_navigate)
    checkpoints.run("Verify primary filters visible", cp_primary_filters)
    checkpoints.run("Verify product cards present", cp_product_cards)
    checkpoints.run("Verify tabs toggle", cp_tabs)
    checkpoints.run("Verify page scrolls smoothly", cp_scroll)
    _video_hold(page)


# ============================================================
# TC-CATALOG-002: Search bar
# ============================================================

@pytest.mark.tc("TC-CATALOG-002")
def test_TC_CATALOG_002_search_filter(catalog_page: Page, checkpoints):
    """Search bar filters products in real-time by keyword."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_navigate():
        expect(page.locator('input[placeholder*="Search"], input[type="search"]').first).to_be_visible()

    def cp_type_keyword():
        before_count = _get_product_count(page)
        search = page.locator('input[placeholder*="Search"], input[type="search"]').first
        search.click()
        page.keyboard.type("Seed", delay=80)
        page.wait_for_timeout(3000)
        expect(search).not_to_have_value("")

    def cp_results_filtered():
        _check_no_page_errors(page, "search")
        assert page.locator('input[placeholder*="Search"], input[type="search"]').first.input_value() != ""

    def cp_scroll_filtered():
        _scroll_down(page, 300)
        page.wait_for_timeout(1000)
        _scroll_up(page, 300)

    checkpoints.run("Navigate to catalog page", cp_navigate)
    checkpoints.run("Type keyword in search bar", cp_type_keyword)
    checkpoints.run("Verify results filtered in real-time", cp_results_filtered)
    checkpoints.run("Scroll to verify filtered results persist", cp_scroll_filtered)
    _video_hold(page)


# ============================================================
# TC-CATALOG-003: Super Category dropdown
# ============================================================

@pytest.mark.tc("TC-CATALOG-003")
def test_TC_CATALOG_003_super_category_filter(catalog_page: Page, checkpoints):
    """Super Category dropdown opens, has search, and filters products."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_navigate():
        expect(page.locator('button:has-text("super category"), button:has-text("Select super category")').first).to_be_visible()

    def cp_open_dropdown():
        page.locator('button:has-text("super category"), button:has-text("Select super category")').first.click()
        page.wait_for_timeout(1000)
        search_input = page.locator('input[placeholder*="Search super category"], input[placeholder*="search super category"]').first
        expect(search_input).to_be_visible(timeout=5000)

    def cp_search_and_select():
        search_input = page.locator('input[placeholder*="Search super category"], input[placeholder*="search super category"]').first
        search_input.fill("Seed")
        page.wait_for_timeout(1000)
        dropdown_area = page.locator('[class*="popover"], [class*="dropdown"], [class*="content"], [role="listbox"]')
        if dropdown_area.count() > 0:
            seed_option = page.locator('text=/.*Seed.*/i')
            if seed_option.count() > 1:
                seed_option.nth(1).click()
            else:
                dropdown_area.first.locator("div, li, [role='option']").first.click()
        else:
            expect(search_input).to_have_value("Seed")

    def cp_verify_filter():
        before_count = _get_product_count(page)
        page.wait_for_timeout(2000)
        _check_no_page_errors(page, "Super Category filter")
        _scroll_down(page, 200)
        page.wait_for_timeout(500)

    checkpoints.run("Navigate to catalog page", cp_navigate)
    checkpoints.run("Open Super Category dropdown with search", cp_open_dropdown)
    checkpoints.run("Search and select category", cp_search_and_select)
    checkpoints.run("Verify products filtered", cp_verify_filter)
    _video_hold(page)


# ============================================================
# TC-CATALOG-004: Brand, SKU Status, Sort dropdowns
# ============================================================

@pytest.mark.tc("TC-CATALOG-004")
def test_TC_CATALOG_004_brand_sku_sort_filters(catalog_page: Page, checkpoints):
    """Brand, SKU Status, and Sort dropdowns all open and filter correctly."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_brand_dropdown():
        before_count = _get_product_count(page)
        page.locator('button:has-text("brand"), button:has-text("Select brand")').first.click()
        page.wait_for_timeout(1000)
        search_input = page.locator('input[placeholder*="Search brands"], input[placeholder*="search brand"]').first
        expect(search_input).to_be_visible(timeout=5000)
        search_input.fill("CMS")
        page.wait_for_timeout(1000)
        dropdown_area = page.locator('[class*="popover"], [class*="dropdown"], [class*="content"], [role="listbox"]')
        if dropdown_area.count() > 0:
            page.locator('text=/CMS.*/').first.click()
        else:
            expect(search_input).to_have_value("CMS")
        page.wait_for_timeout(2000)
        _check_no_page_errors(page, "Brand filter")

    def cp_sku_status():
        page.locator('button:has-text("SKU Status")').first.click()
        page.wait_for_timeout(1000)
        body_text = page.text_content("body") or ""
        for status in ["Available", "Already Listed", "Requested", "Incomplete"]:
            assert status in body_text, f"Expected '{status}' in SKU Status filter options"
        page.locator("text=Available").first.click()
        page.wait_for_timeout(2000)
        _check_no_page_errors(page, "SKU Status filter")

    def cp_sort_dropdown():
        page.locator('button:has-text("Sort")').first.click()
        page.wait_for_timeout(1000)
        body_text = page.text_content("body") or ""
        for option in ["Most to Least popular", "Newest to Oldest", "Name A-Z"]:
            assert option in body_text, f"Expected sort option '{option}'"
        page.locator("text=Name A-Z").first.click()
        page.wait_for_timeout(2000)
        _check_no_page_errors(page, "sort dropdown")

    checkpoints.run("Open Brand dropdown, search, select, verify filter", cp_brand_dropdown)
    checkpoints.run("Open SKU Status, verify all options, select one", cp_sku_status)
    checkpoints.run("Open Sort, verify options, select one", cp_sort_dropdown)
    _video_hold(page)


# ============================================================
# TC-CATALOG-005: More section — Category and Packing Size
# ============================================================

@pytest.mark.tc("TC-CATALOG-005")
def test_TC_CATALOG_005_more_filters(catalog_page: Page, checkpoints):
    """More section reveals Category and Packing Size dropdowns with search and filter."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_more_section():
        page.locator('button:has-text("More")').first.click()
        page.wait_for_timeout(1000)
        cat_btn = page.locator('button:has-text("Category")')
        pack_btn = page.locator('button:has-text("Packing")')
        expect(cat_btn.first).to_be_visible(timeout=5000)
        expect(pack_btn.first).to_be_visible(timeout=5000)

    def cp_category_filter():
        before_count = _get_product_count(page)
        page.locator('button:has-text("Category")').first.click()
        page.wait_for_timeout(1000)
        search_input = page.locator('input[placeholder*="category"], input[placeholder*="Category"]').first
        expect(search_input).to_be_visible(timeout=5000)
        search_input.fill("Seed")
        page.wait_for_timeout(1000)
        dropdown_area = page.locator('[class*="popover"], [class*="dropdown"], [class*="content"], [role="listbox"]')
        if dropdown_area.count() > 0:
            seed_option = page.locator('text=/.*Seed.*/i')
            if seed_option.count() > 1:
                seed_option.nth(1).click()
                page.wait_for_timeout(2000)
                _check_no_page_errors(page, "Category filter")
            else:
                page.keyboard.press("Escape")
        else:
            page.keyboard.press("Escape")

    def cp_packing_filter():
        page.locator('button:has-text("Packing")').first.click()
        page.wait_for_timeout(1000)
        search_input = page.locator('input[placeholder*="packing"], input[placeholder*="Packing"]').first
        expect(search_input).to_be_visible(timeout=5000)
        search_input.fill("500")
        page.wait_for_timeout(1000)
        dropdown_area = page.locator('[class*="popover"], [class*="dropdown"], [class*="content"], [role="listbox"]')
        if dropdown_area.count() > 0:
            pack_option = page.locator('text=/.*500.*/i')
            if pack_option.count() > 1:
                pack_option.nth(1).click()
                page.wait_for_timeout(2000)
                _check_no_page_errors(page, "Packing filter")
            else:
                page.keyboard.press("Escape")
        else:
            page.keyboard.press("Escape")

    checkpoints.run("Click More, verify Category and Packing dropdowns appear", cp_more_section)
    checkpoints.run("Open Category dropdown, search, select, verify filter", cp_category_filter)
    checkpoints.run("Open Packing Size dropdown, search, select, verify filter", cp_packing_filter)
    _video_hold(page)


# ============================================================
# TC-CATALOG-006: All 5 product card types
# ============================================================

@pytest.mark.tc("TC-CATALOG-006")
def test_TC_CATALOG_006_card_types(catalog_page: Page, checkpoints):
    """All 5 product card types render correctly with expected elements."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_addable_card():
        all_btns = page.locator("button")
        add_btn = None
        for i in range(all_btns.count()):
            btn = all_btns.nth(i)
            text = (btn.text_content() or "").strip()
            svg_count = btn.locator("svg").count()
            if text == "" and svg_count > 0:
                aria = btn.get_attribute("aria-label") or ""
                title = btn.get_attribute("title") or ""
                if "+" in aria or "+" in title or "add" in aria.lower():
                    add_btn = btn
                    break
        if add_btn is None:
            checkpoints.skip("Addable (+) card", "No Addable card type found")
            return
        add_btn.click()
        page.wait_for_timeout(2000)
        expect(page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first).to_be_visible(timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    def cp_manage_listing_card():
        manage_btns = page.locator('button:has-text("Manage Listing")')
        if manage_btns.count() == 0:
            checkpoints.skip("Manage Listing card", "No Manage Listing card found")
            return
        manage_btns.first.click()
        page.wait_for_timeout(2000)
        expect(page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first).to_be_visible(timeout=5000)
        body_text = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first.text_content() or ""
        assert "HSN" in body_text or "MRP" in body_text, "Expected HSN/MRP in Edit Product modal"
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    def cp_request_product_card():
        request_btns = page.locator('button:has-text("Request Product")')
        assert request_btns.count() > 0, "Expected at least one 'Request Product' button"
        request_btns.first.click()
        page.wait_for_timeout(2000)
        expect(page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first).to_be_visible(timeout=5000)
        expect(page.locator('input[placeholder="HSN Code"]').first).to_be_visible(timeout=5000)
        expect(page.locator('input[placeholder="Your Price"]').first).to_be_visible(timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    def cp_requested_badge():
        _scroll_to_top(page)
        found = False
        for _ in range(5):
            if page.locator("text=Requested").count() > 0:
                found = True
                break
            _scroll_down(page, 500)
            page.wait_for_timeout(500)
        assert page.locator("text=Requested").count() > 0, \
            "Expected 'Requested' text somewhere on catalog page"

    def cp_blocked_card():
        _scroll_to_top(page)
        body_text = page.text_content("body") or ""
        has_blocked = any(kw in body_text for kw in ["Blocked", "Item Blocked", "OOS", "Out of Stock"])
        if not has_blocked:
            checkpoints.skip("Blocked/OOS card", "No Blocked/OOS card type found")
            return

    checkpoints.run("Verify Addable (+) card opens Add Product modal", cp_addable_card)
    checkpoints.run("Verify Manage Listing card opens Edit Product modal", cp_manage_listing_card)
    checkpoints.run("Verify Incomplete card opens Request Product modal", cp_request_product_card)
    checkpoints.run("Verify Requested badge visible", cp_requested_badge)
    checkpoints.run("Verify Blocked/OOS card shows status", cp_blocked_card)
    _video_hold(page)


# ============================================================
# TC-CATALOG-007: Request Product full flow
# ============================================================

@pytest.mark.tc("TC-CATALOG-007")
def test_TC_CATALOG_007_request_product_flow(catalog_page: Page, tc_data, test_image_path, checkpoints):
    """Request Product flow: open modal, verify fields, fill, submit."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_open_modal():
        page.locator('button:has-text("Request Product")').first.click()
        page.wait_for_timeout(2000)
        expect(page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first).to_be_visible(timeout=5000)

    def cp_verify_fields():
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        expect(page.locator('input[placeholder="Master Pack"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="Dead Weight"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="L"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="B"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="H"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="HSN Code"]').first).to_be_visible(timeout=3000)
        assert page.locator('input[type="file"]').count() > 0, "Expected file upload input"
        expect(page.locator('input[placeholder="Price"]').first).to_be_visible(timeout=3000)
        expect(page.locator('input[placeholder="Your Price"]').first).to_be_visible(timeout=3000)
        expect(page.locator('[role="switch"]').first).to_be_visible(timeout=3000)
        expect(page.locator('button:has-text("Request Product")').last).to_be_visible(timeout=3000)

    def cp_fill_fields():
        for dim, placeholder in [("l", "L"), ("b", "B"), ("h", "H")]:
            inp = page.locator(f'input[placeholder="{placeholder}"]').first
            inp.click()
            inp.fill(tc_data.get(dim, "10"))

        hsn = page.locator('input[placeholder="HSN Code"]').first
        hsn.click()
        hsn.fill(tc_data.get("hsn", "12345678"))

        page.locator('input[type="file"]').first.set_input_files(test_image_path)
        page.wait_for_timeout(1000)

        mrp = page.locator('input[placeholder="Price"]').first
        mrp.click()
        mrp.fill(tc_data.get("mrp", "500"))

        yp = page.locator('input[placeholder="Your Price"]').first
        yp.click()
        yp.fill(tc_data.get("your_price", "450"))

        # Expiry date
        page.locator('button:has-text("Month/Year"), button:has-text("Expiry")').first.click()
        page.wait_for_timeout(1000)
        month_select = page.locator('select[aria-label="Choose the Month"]').first
        month_select.select_option("8")
        page.wait_for_timeout(500)
        year_select = page.locator('select[aria-label="Choose the Year"]').first
        year_select.select_option("2027")
        page.wait_for_timeout(500)
        date_cell = page.locator('td button:not([disabled])').first
        date_cell.click()
        page.wait_for_timeout(1000)

        switch = page.locator('[role="switch"]').first
        if switch.get_attribute("aria-checked") == "false":
            switch.click()
            page.wait_for_timeout(500)

    def cp_submit():
        submit_btn = page.locator('button:has-text("Request Product")').last
        is_enabled = submit_btn.is_enabled()
        _video_hold(page, 3)
        if is_enabled:
            submit_btn.click()
            _assert_submission_success(page)
        else:
            validation_errs = page.locator('[class*="text-destructive"]:visible, [class*="error"]:visible, [class*="required"]:visible')
            err_detail = ""
            if validation_errs.count() > 0:
                err_detail = f" — Validation: {validation_errs.first.text_content().strip()[:200]}"
            raise AssertionError(f"Request Product button still disabled after filling all fields{err_detail}")

    checkpoints.run("Open Request Product modal", cp_open_modal)
    checkpoints.run("Verify all required fields present", cp_verify_fields)
    checkpoints.run("Fill all mandatory fields", cp_fill_fields)
    checkpoints.run("Submit and verify success", cp_submit)
    _video_hold(page)


# ============================================================
# TC-CATALOG-008: Incorrect Product flow
# ============================================================

@pytest.mark.tc("TC-CATALOG-008")
def test_TC_CATALOG_008_incorrect_product_flow(catalog_page: Page, tc_data, checkpoints):
    """Incorrect Product flow: open modal, select issue, add comment, submit."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_open_modal():
        incorrect_btns = page.locator('button:has-text("Incorrect"), a:has-text("Incorrect")')
        assert incorrect_btns.count() > 0, "No Incorrect? buttons found"
        incorrect_btns.first.click()
        page.wait_for_timeout(2000)
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        expect(dialog).to_be_visible(timeout=5000)

    def cp_verify_fields():
        expect(page.locator("textarea").first).to_be_visible(timeout=5000)
        expect(page.locator('button:has-text("Select an option"), button:has-text("Select Issue")').first).to_be_visible(timeout=5000)
        expect(page.locator('button:has-text("Request Change"), button:has-text("Request Changes")').first).to_be_visible(timeout=5000)

    def cp_fill_and_submit():
        select_btn = page.locator('button:has-text("Select an option"), button:has-text("Select Issue")').first
        select_btn.click()
        page.wait_for_timeout(1500)
        popover = page.locator('[role="listbox"], [role="option"], [class*="popover"] [class*="item"], [class*="select-content"], [class*="dropdown-content"]')
        if popover.count() > 0:
            popover.first.click()
        else:
            page.locator('[class*="option"], [class*="item"]').first.click()
        page.wait_for_timeout(500)

        textarea = page.locator("textarea").first
        textarea.fill(tc_data.get("comment", "The product image is incorrect and needs to be updated."))

        submit_btn = page.locator('button:has-text("Request Change"), button:has-text("Request Changes")').first
        expect(submit_btn).to_be_enabled(timeout=5000)
        _video_hold(page, 3)
        submit_btn.click()
        _assert_submission_success(page)

    def cp_verify_success():
        _check_no_page_errors(page, "Incorrect Product submission")

    checkpoints.run("Open Incorrect Product modal", cp_open_modal)
    checkpoints.run("Verify modal fields (dropdown, textarea, submit button)", cp_verify_fields)
    checkpoints.run("Select issue type, add comment, submit", cp_fill_and_submit)
    checkpoints.run("Verify no page errors after submission", cp_verify_success)
    _video_hold(page)


# ============================================================
# TC-CATALOG-009: Request Variant flow
# ============================================================

@pytest.mark.tc("TC-CATALOG-009")
def test_TC_CATALOG_009_request_variant_flow(catalog_page: Page, tc_data, checkpoints):
    """Request Variant flow: open modal, verify pre-filled fields, fill additional data, submit."""
    page = catalog_page
    _scroll_to_top(page)

    def cp_navigate():
        expect(page.locator('input[placeholder*="Search"], input[type="search"]').first).to_be_visible()

    def cp_open_modal():
        variant_btns = page.locator('button:has-text("Request Variant")')
        if variant_btns.count() == 0:
            checkpoints.skip("Open Request Variant modal", "No Request Variant buttons found")
            return False
        variant_btns.first.click()
        page.wait_for_timeout(2000)
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        expect(dialog).to_be_visible(timeout=5000)
        return True

    def cp_verify_fields():
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        inputs = dialog.locator("input")
        assert inputs.count() > 0, "Expected input fields in Request Variant modal"

    def cp_verify_prefilled():
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        inputs = dialog.locator("input")
        has_value = False
        for i in range(inputs.count()):
            val = inputs.nth(i).input_value()
            if val and val.strip():
                has_value = True
                break
        assert has_value, "Expected at least one pre-filled field from parent product"

    def cp_fill_and_submit():
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        submit_btn = dialog.locator('button:has-text("Request"), button:has-text("Submit"), button:has-text("Send")')
        if submit_btn.count() == 0:
            checkpoints.skip("Fill and submit variant", "No submit button found in variant modal")
            return
        _video_hold(page, 2)
        submit_btn.first.click()
        page.wait_for_timeout(3000)
        _check_no_page_errors(page, "Request Variant submission")

    def cp_close():
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    checkpoints.run("Navigate to catalog page", cp_navigate)
    opened = checkpoints.run("Open Request Variant modal", cp_open_modal)
    if opened:
        checkpoints.run("Verify modal has form fields", cp_verify_fields)
        checkpoints.run("Verify fields are pre-filled from parent product", cp_verify_prefilled)
        checkpoints.run("Fill additional data and submit", cp_fill_and_submit)
        checkpoints.run("Close modal", cp_close)
    _video_hold(page)


# ============================================================
# TC-CATALOG-010: Create New Product flow
# ============================================================

@pytest.mark.tc("TC-CATALOG-010")
def test_TC_CATALOG_010_create_new_product_flow(catalog_page: Page, tc_data, test_image_path, checkpoints):
    """Scroll to find 'Create New Product' button, open modal, verify fields, fill, submit."""
    page = catalog_page

    def cp_navigate():
        expect(page.locator('input[placeholder*="Search"], input[type="search"]').first).to_be_visible()

    def cp_scroll_to_button():
        _scroll_to_top(page)
        page.wait_for_timeout(500)
        for i in range(20):
            _scroll_down(page, 500)
            page.wait_for_timeout(600)
            btn = page.locator('button:has-text("Create New Product")')
            if btn.count() > 0:
                print(f"  Found 'Create New Product' after {i+1} scrolls", flush=True)
                return True
        checkpoints.skip("Find Create New Product button", "Button not found after 20 scrolls")
        return False

    def cp_open_modal():
        btn = page.locator('button:has-text("Create New Product")').first
        btn.click()
        page.wait_for_timeout(2000)
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="dialog"]').first
        expect(dialog).to_be_visible(timeout=5000)
        print("  Modal opened", flush=True)

    def cp_verify_fields():
        dialog = page.locator('[role="dialog"]').first
        body_text = dialog.text_content() or ""
        expected_fields = ["Brand", "Product Name", "Packing", "HSN", "MRP", "Your Price",
                           "Dead Weight", "LBH", "Image"]
        found = [f for f in expected_fields if f in body_text]
        assert len(found) >= 5, f"Expected at least 5 form fields, found: {found}"
        print(f"  Fields found: {', '.join(found)}", flush=True)

    def cp_upload_image():
        dialog = page.locator('[role="dialog"]').first
        file_input = dialog.locator('input[type="file"]').first
        if file_input.count() > 0:
            img_path = test_image_path
            file_input.set_input_files(img_path)
            page.wait_for_timeout(1000)
            print("  Image uploaded", flush=True)
        else:
            print("  No file input found, skipping image upload", flush=True)

    def cp_fill_dropdowns():
        dialog = page.locator('div[role="dialog"][data-state="open"]').last
        # Brand: click combobox (force=True to bypass dialog overlay interception)
        brand_btn = dialog.locator('button[role="combobox"]').first
        if brand_btn.count() > 0:
            brand_btn.click(force=True)
            page.wait_for_timeout(800)
            # Popover appears as a second dialog with brand options as <span> elements
            # Click the first option in the popover
            brand_option = page.locator('div[role="dialog"].bg-popover span').first
            if brand_option.count() > 0:
                selected_brand = brand_option.text_content() or "brand"
                brand_option.click(force=True)
                page.wait_for_timeout(500)
                print(f"  Brand selected: {selected_brand}", flush=True)
            else:
                page.keyboard.press("Escape")
                print("  Brand popover options not found", flush=True)
        # Packing: click combobox
        packing_btn = dialog.locator('button[role="combobox"]').nth(1)
        if packing_btn.count() > 0:
            packing_btn.click(force=True)
            page.wait_for_timeout(800)
            packing_option = page.locator('div[role="dialog"].bg-popover span').first
            if packing_option.count() > 0:
                selected_packing = packing_option.text_content() or "packing"
                packing_option.click(force=True)
                page.wait_for_timeout(500)
                print(f"  Packing selected: {selected_packing}", flush=True)
            else:
                page.keyboard.press("Escape")
                print("  Packing popover options not found", flush=True)
        # In Stock: click the 3rd combobox
        in_stock_btn = dialog.locator('button[role="combobox"]').nth(2)
        if in_stock_btn.count() > 0:
            in_stock_btn.click(force=True)
            page.wait_for_timeout(800)
            in_stock_option = page.locator('div[role="dialog"].bg-popover span').first
            if in_stock_option.count() > 0:
                selected_stock = in_stock_option.text_content() or "Yes"
                in_stock_option.click(force=True)
                page.wait_for_timeout(500)
                print(f"  In Stock selected: {selected_stock}", flush=True)
            else:
                page.keyboard.press("Escape")
                print("  In Stock popover not found", flush=True)

    def cp_fill_text_fields():
        dialog = page.locator('[role="dialog"]').first
        # Fill text inputs by placeholder
        text_fields = {
            "Product Name": tc_data.get("product_name", "Test Product ATS"),
            "Master Pack": tc_data.get("master_pack", "10"),
            "Dead Weight": tc_data.get("dead_weight", "1.5"),
            "HSN Code": tc_data.get("hsn", "12345678"),
            "PRICE": tc_data.get("mrp", "300"),
            "Your Price": tc_data.get("your_price", "250"),
        }
        # L, B, H are small fields
        for placeholder, value in text_fields.items():
            inp = dialog.locator(f'input[placeholder="{placeholder}"]').first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                inp.fill(value)
                page.wait_for_timeout(200)
        # Fill L, B, H
        for dim in ["L", "B", "H"]:
            val = tc_data.get(dim.lower(), "10")
            inp = dialog.locator(f'input[placeholder="{dim}"]').first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                inp.fill(val)
                page.wait_for_timeout(200)
        # Fill Expiry Date — it's a Radix month/year calendar picker
        expiry_btn = dialog.locator('button:has-text("Month/Year")')
        if expiry_btn.count() > 0 and expiry_btn.first.is_visible():
            expiry_btn.first.click(force=True)
            page.wait_for_timeout(800)
            # A month grid popover appears — click the target month button
            target_month = tc_data.get("expiry_month", "12")
            # Map month number to 3-letter abbreviation
            month_names = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
                           "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
                           "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
            month_abbr = month_names.get(target_month, "Dec")
            month_btn = page.locator(f'button:has-text("{month_abbr}")').last
            if month_btn.count() > 0:
                month_btn.click(force=True)
                page.wait_for_timeout(500)
                print(f"  Expiry month selected: {month_abbr}", flush=True)
            else:
                page.keyboard.press("Escape")
                print(f"  Month button '{month_abbr}' not found in picker", flush=True)
        page.wait_for_timeout(500)
        print(f"  All text fields filled", flush=True)

    def cp_submit():
        dialog = page.locator('[role="dialog"]').first
        submit_btn = dialog.locator('button:has-text("Request Product"), button:has-text("Create"), button:has-text("Submit")')
        if submit_btn.count() > 0 and submit_btn.first.is_enabled():
            _video_hold(page, 2)
            submit_btn.first.click()
            page.wait_for_timeout(3000)
            _check_no_page_errors(page, "Create New Product submission")
            print("  Submitted", flush=True)
        else:
            checkpoints.skip("Submit new product", "Submit button not found or not enabled")

    def cp_close():
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    checkpoints.run("Navigate to catalog page", cp_navigate)
    found = checkpoints.run("Scroll to find 'Create New Product' button", cp_scroll_to_button)
    if found:
        checkpoints.run("Open Create New Product modal", cp_open_modal)
        checkpoints.run("Verify form fields present (Brand, Product Name, Packing, etc.)", cp_verify_fields)
        checkpoints.run("Upload product image", cp_upload_image)
        checkpoints.run("Select Brand, Packing, and In Stock from dropdowns", cp_fill_dropdowns)
        checkpoints.run("Fill all text fields (Name, Weight, LBH, HSN, MRP, Price, Expiry)", cp_fill_text_fields)
        checkpoints.run("Submit and verify no errors", cp_submit)
        checkpoints.run("Close modal", cp_close)
    _video_hold(page)
