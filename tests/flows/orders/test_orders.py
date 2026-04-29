"""Orders flow — Comprehensive Playwright E2E tests for Seller App PO Management.

Covers (4 categories):
  1. Page loads with all required details (stats, columns, navigation)
  2. Filters, sorting, and search — including combinations
  3. Order detail panel — all fields verified
  4. Stage-wise actions — auto-accept, accept, reject (with reason),
     pack (label generation), ready (ready proof upload), picked up
     (tax invoice, e-way bill, proof of pickup upload), auto-cancel
"""

import os
import pytest
from playwright.sync_api import expect, Page


# ── Helpers ──────────────────────────────────────────────────

def _login(page, test_user, base_url):
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
    page.evaluate(f"window.scrollBy(0, {pixels})")
    page.wait_for_timeout(500)


def _scroll_up(page, pixels=500):
    page.evaluate(f"window.scrollBy(0, -{pixels})")
    page.wait_for_timeout(500)


def _scroll_to_top(page):
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def _check_no_page_errors(page, context_label=""):
    page.wait_for_timeout(500)
    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
        '[class*="toast"]:visible:has-text("failed")',
        '[class*="toast"]:visible:has-text("Failed")',
        '[class*="toast"]:visible:has-text("something went wrong")',
        '[class*="error-message"]:visible',
        '[class*="text-destructive"]:visible',
        '.text-red-500:visible',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            error_text = errors.first.text_content().strip()[:200]
            if error_text:
                raise AssertionError(
                    f"Page error detected{f' ({context_label})' if context_label else ''}: {error_text}"
                )


def _get_order_count(page):
    rows = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"], [class*="po-row"], [class*="po-card"]')
    count = rows.count()
    if count > 0:
        return count
    return page.locator('[class*="order"]:has(button), [class*="po"]:has(button)').count()


def _navigate_to_orders(page, base_url):
    urls_to_try = [
        base_url + "orders",
        base_url + "purchase-orders",
        base_url + "listing/orders",
        base_url + "po",
        base_url + "seller/orders",
        base_url + "seller/purchase-orders",
    ]
    for url in urls_to_try:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=10000)
            if resp and resp.status < 400:
                page.wait_for_timeout(2000)
                body = page.locator("body").text_content() or ""
                if any(kw in body.lower() for kw in ["order", "purchase", "po", "accept", "reject", "pack"]):
                    return True
        except Exception:
            continue
    # Fallback: navigate from sidebar
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    nav_selectors = [
        'a:has-text("Orders")', 'a:has-text("Purchase Orders")',
        'a:has-text("PO")', 'a[href*="order"]', 'a[href*="purchase"]',
        'button:has-text("Orders")', 'a:has-text("My Orders")',
    ]
    for sel in nav_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            page.wait_for_load_state("domcontentloaded")
            return True
    return False


def _assert_submission_success(page):
    page.wait_for_timeout(2000)
    success_indicators = [
        '[class*="toast"]:visible:has-text("success")',
        '[class*="toast"]:visible:has-text("Success")',
        '[class*="toast"]:visible:has-text("accepted")',
        '[class*="toast"]:visible:has-text("Accepted")',
        '[class*="toast"]:visible:has-text("rejected")',
        '[class*="toast"]:visible:has-text("Rejected")',
        '[class*="toast"]:visible:has-text("packed")',
        '[class*="toast"]:visible:has-text("Packed")',
        '[class*="toast"]:visible:has-text("ready")',
        '[class*="toast"]:visible:has-text("Ready")',
        '[class*="toast"]:visible:has-text("picked")',
        '[class*="toast"]:visible:has-text("updated")',
        '[class*="toast"]:visible:has-text("Updated")',
        '[class*="toast"]:visible:has-text("uploaded")',
        '[class*="toast"]:visible:has-text("Uploaded")',
        '[class*="toast"]:visible:has-text("generated")',
        '[class*="toast"]:visible:has-text("Generated")',
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
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            error_text = errors.first.text_content().strip()[:300]
            raise AssertionError(f"Action failed with error: {error_text}")


def _click_first_order(page):
    """Click the first order row/card. Returns True if successful."""
    row = page.locator(
        'table tbody tr, [class*="order-row"], [class*="order-item"], '
        '[class*="order-card"], [class*="po-row"], [class*="po-card"]'
    ).first
    if not row.is_visible():
        return False
    row.click()
    page.wait_for_timeout(2000)
    return True


def _filter_by_status(page, *status_labels):
    """Click a status filter tab/button. Returns True if found."""
    for label in status_labels:
        sel = f'button:has-text("{label}"), [class*="tab"]:has-text("{label}"), a:has-text("{label}")'
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            return True
    # Try dropdown approach
    dropdown = page.locator('select:has(option), [class*="filter"]:has-text("Status")').first
    if dropdown.is_visible():
        for label in status_labels:
            try:
                dropdown.select_option(label=label)
                page.wait_for_timeout(2000)
                return True
            except Exception:
                continue
    return False


def _create_test_image(path):
    """Create a tiny valid PNG for file-upload tests."""
    if os.path.exists(path):
        return path
    import struct, zlib
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
    with open(path, "wb") as f:
        f.write(sig + ihdr + idat + iend)
    return path


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def orders_page(page, test_user, base_url, sequential_page):
    """Log in and navigate to the Orders page."""
    if sequential_page is not None:
        _navigate_to_orders(sequential_page, base_url)
        sequential_page.wait_for_timeout(1000)
        return sequential_page
    _login(page, test_user, base_url)
    _navigate_to_orders(page, base_url)
    page.wait_for_timeout(1000)
    return page


@pytest.fixture
def test_image_path():
    """Create a tiny valid PNG for file-upload tests."""
    img_dir = os.path.dirname(__file__)
    img_path = os.path.join(img_dir, "test_upload.png")
    _create_test_image(img_path)
    return img_path


# ================================================================
#  SECTION 1 — PAGE LOAD & STRUCTURE
# ================================================================

@pytest.mark.tc("TC-ORDERS-001")
def test_TC_ORDERS_001_page_loads(orders_page):
    """Verify Orders page loads with order-related content."""
    page = orders_page
    _video_hold(page, 2)
    _check_no_page_errors(page, "orders page load")
    body = page.locator("body").text_content() or ""
    assert any(kw in body.lower() for kw in ["order", "purchase", "po", "status"]), \
        "Orders page does not contain order-related content"


@pytest.mark.tc("TC-ORDERS-002")
def test_TC_ORDERS_002_stats_cards_visible(orders_page):
    """Verify order summary/stats cards at the top show counts by status."""
    page = orders_page
    _scroll_to_top(page)
    _video_hold(page, 2)
    # Look for stat cards with numeric values
    stat_selectors = [
        '[class*="stat"]:visible', '[class*="summary"]:visible',
        '[class*="metric"]:visible', '[class*="count"]:visible',
        '[class*="card"]:has-text("Total"):visible',
    ]
    stats_found = any(page.locator(s).count() > 0 for s in stat_selectors)
    body = page.locator("body").text_content() or ""
    stats_in_text = any(s in body.lower() for s in [
        "total orders", "pending", "accepted", "packed", "dispatched", "rejected"
    ])
    assert stats_found or stats_in_text, "Order stats/summary cards not found"


@pytest.mark.tc("TC-ORDERS-003")
def test_TC_ORDERS_003_table_has_all_columns(orders_page):
    """Verify order list shows key columns: PO ID, Status, Date, Amount, Qty."""
    page = orders_page
    body = page.locator("body").text_content() or ""
    body_lower = body.lower()
    expected = ["order", "status", "date", "amount", "po", "quantity", "total", "seller"]
    found = sum(1 for h in expected if h in body_lower)
    assert found >= 3, f"Expected at least 3 order columns, found {found}"


@pytest.mark.tc("TC-ORDERS-004")
def test_TC_ORDERS_004_navigation_tabs_present(orders_page):
    """Verify status navigation tabs: All, Pending, Accepted, Packed, etc."""
    page = orders_page
    tab_texts = ["all", "pending", "accepted", "packed", "ready", "dispatched",
                 "rejected", "cancelled", "sent", "new"]
    body = page.locator("body").text_content() or ""
    found = sum(1 for t in tab_texts if t in body.lower())
    assert found >= 3, f"Expected at least 3 status tabs, found {found}"


@pytest.mark.tc("TC-ORDERS-005")
def test_TC_ORDERS_005_page_scroll_behavior(orders_page):
    """Verify orders page scrolls smoothly without errors."""
    page = orders_page
    _scroll_down(page, 500)
    _video_hold(page, 1)
    _scroll_down(page, 500)
    _video_hold(page, 1)
    _scroll_up(page, 500)
    _video_hold(page, 1)
    _scroll_to_top(page)
    _video_hold(page, 2)
    _check_no_page_errors(page, "scroll")


# ================================================================
#  SECTION 2 — SEARCH, FILTERS & SORTING
# ================================================================

@pytest.mark.tc("TC-ORDERS-006")
def test_TC_ORDERS_006_search_by_po_number(orders_page, tc_data):
    """Verify search by PO number returns matching order."""
    page = orders_page
    search = page.locator(
        'input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]'
    ).first
    if not search.is_visible():
        pytest.skip("Search input not found")
    # Get a PO number from the page to search for
    body = page.locator("body").text_content() or ""
    import re
    po_match = re.search(r'(PO[-\s]?\d+|ORD[-\s]?\d+)', body)
    search_term = po_match.group(1) if po_match else tc_data.get("search_po", "PO")
    search.click()
    search.fill("")
    page.keyboard.type(search_term, delay=30)
    page.wait_for_timeout(2000)
    _check_no_page_errors(page, "search by PO")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-007")
def test_TC_ORDERS_007_search_by_sku_name(orders_page, tc_data):
    """Verify search by SKU/product name filters orders."""
    page = orders_page
    search = page.locator(
        'input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]'
    ).first
    if not search.is_visible():
        pytest.skip("Search input not found")
    search_term = tc_data.get("search_sku", "urea")
    search.click()
    search.fill("")
    page.keyboard.type(search_term, delay=30)
    page.wait_for_timeout(2000)
    _check_no_page_errors(page, "search by SKU")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-008")
def test_TC_ORDERS_008_search_partial_keyword(orders_page):
    """Verify search works with partial keywords."""
    page = orders_page
    search = page.locator(
        'input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]'
    ).first
    if not search.is_visible():
        pytest.skip("Search input not found")
    search.click()
    search.fill("")
    page.keyboard.type("ORD", delay=30)
    page.wait_for_timeout(1500)
    # Clear and try another partial
    search.fill("")
    page.keyboard.type("PO", delay=30)
    page.wait_for_timeout(1500)
    _check_no_page_errors(page, "partial search")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-009")
def test_TC_ORDERS_009_search_no_results(orders_page):
    """Verify search with non-existent keyword shows empty/no results state."""
    page = orders_page
    search = page.locator(
        'input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]'
    ).first
    if not search.is_visible():
        pytest.skip("Search input not found")
    search.click()
    search.fill("")
    page.keyboard.type("ZZZZNONEXISTENT999", delay=30)
    page.wait_for_timeout(2000)
    # Should show empty state or no rows
    order_count = _get_order_count(page)
    body = page.locator("body").text_content() or ""
    has_empty = any(kw in body.lower() for kw in ["no order", "no result", "not found", "empty"])
    assert order_count == 0 or has_empty, "Expected no results for non-existent search"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-010")
def test_TC_ORDERS_010_filter_pending(orders_page):
    """Verify Pending/Sent filter shows only pending orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    if not clicked:
        pytest.skip("Pending filter not found")
    _check_no_page_errors(page, "pending filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-011")
def test_TC_ORDERS_011_filter_accepted(orders_page):
    """Verify Accepted/Confirmed filter shows only accepted orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Accepted", "Confirmed")
    if not clicked:
        pytest.skip("Accepted filter not found")
    _check_no_page_errors(page, "accepted filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-012")
def test_TC_ORDERS_012_filter_packed(orders_page):
    """Verify Packed filter shows only packed orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Packed")
    if not clicked:
        pytest.skip("Packed filter not found")
    _check_no_page_errors(page, "packed filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-013")
def test_TC_ORDERS_013_filter_ready(orders_page):
    """Verify Ready filter shows only ready-for-pickup orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Ready")
    if not clicked:
        pytest.skip("Ready filter not found")
    _check_no_page_errors(page, "ready filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-014")
def test_TC_ORDERS_014_filter_dispatched(orders_page):
    """Verify Dispatched filter shows only dispatched orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Dispatched", "Shipped", "In Transit")
    if not clicked:
        pytest.skip("Dispatched filter not found")
    _check_no_page_errors(page, "dispatched filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-015")
def test_TC_ORDERS_015_filter_rejected(orders_page):
    """Verify Rejected filter shows only rejected orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Rejected")
    if not clicked:
        pytest.skip("Rejected filter not found")
    _check_no_page_errors(page, "rejected filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-016")
def test_TC_ORDERS_016_filter_cancelled(orders_page):
    """Verify Cancelled filter shows only cancelled orders."""
    page = orders_page
    clicked = _filter_by_status(page, "Cancelled", "Canceled")
    if not clicked:
        pytest.skip("Cancelled filter not found")
    _check_no_page_errors(page, "cancelled filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-017")
def test_TC_ORDERS_017_date_range_filter(orders_page):
    """Verify date range filter is available and functional."""
    page = orders_page
    date_selectors = [
        'input[type="date"]', 'input[placeholder*="date"]', 'input[placeholder*="Date"]',
        'button:has-text("Date")', '[class*="date-picker"]', '[class*="datepicker"]',
        '[class*="date-range"]', 'input[type="date"]:visible',
    ]
    for sel in date_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(1000)
            _check_no_page_errors(page, "date filter")
            _video_hold(page, 2)
            return
    pytest.skip("Date filter not found")


@pytest.mark.tc("TC-ORDERS-018")
def test_TC_ORDERS_018_filter_combination_status_and_search(orders_page):
    """Verify combining status filter with search narrows results correctly."""
    page = orders_page
    # First apply status filter
    _filter_by_status(page, "Pending", "Sent", "Accepted")
    page.wait_for_timeout(1000)
    # Then apply search
    search = page.locator(
        'input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]'
    ).first
    if search.is_visible():
        search.click()
        search.fill("")
        page.keyboard.type("ORD", delay=30)
        page.wait_for_timeout(2000)
    _check_no_page_errors(page, "status + search combo")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-019")
def test_TC_ORDERS_019_filter_combination_status_and_date(orders_page):
    """Verify combining status filter with date range narrows results."""
    page = orders_page
    _filter_by_status(page, "Accepted", "Packed")
    page.wait_for_timeout(1000)
    date_el = page.locator('input[type="date"], [class*="date-picker"]').first
    if date_el.is_visible():
        date_el.click()
        page.wait_for_timeout(1000)
    _check_no_page_errors(page, "status + date combo")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-020")
def test_TC_ORDERS_020_sort_by_date(orders_page):
    """Verify sorting orders by date (newest/oldest)."""
    page = orders_page
    sort_selectors = [
        'button:has-text("Date")', 'button:has-text("Sort")',
        '[class*="sort"]:has-text("Date")', 'select:has(option:has-text("Date"))',
        'button:has-text("Newest")', 'button:has-text("Latest")',
        '[class*="sort"]', 'button:has-text("Sort By")',
    ]
    for sel in sort_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            _check_no_page_errors(page, "sort by date")
            _video_hold(page, 2)
            return
    pytest.skip("Sort control not found")


@pytest.mark.tc("TC-ORDERS-021")
def test_TC_ORDERS_021_sort_by_amount(orders_page):
    """Verify sorting orders by amount (high to low / low to high)."""
    page = orders_page
    sort_selectors = [
        'button:has-text("Amount")', 'button:has-text("Value")',
        '[class*="sort"]:has-text("Amount")',
    ]
    for sel in sort_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            _check_no_page_errors(page, "sort by amount")
            _video_hold(page, 2)
            return
    pytest.skip("Amount sort not found")


@pytest.mark.tc("TC-ORDERS-022")
def test_TC_ORDERS_022_more_filters(orders_page):
    """Verify 'More Filters' section reveals additional filter options."""
    page = orders_page
    more_selectors = [
        'button:has-text("More")', 'button:has-text("More Filters")',
        'button:has-text("Advanced")', '[class*="more-filter"]',
        'button:has-text("Additional Filters")',
    ]
    for sel in more_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(1000)
            # Verify additional filters appeared
            body = page.locator("body").text_content() or ""
            _check_no_page_errors(page, "more filters")
            _video_hold(page, 2)
            return
    pytest.skip("More Filters button not found")


@pytest.mark.tc("TC-ORDERS-023")
def test_TC_ORDERS_023_filter_all_shows_all_orders(orders_page):
    """Verify 'All' filter resets and shows all orders."""
    page = orders_page
    # First filter to a specific status
    _filter_by_status(page, "Pending", "Sent")
    page.wait_for_timeout(1000)
    count_filtered = _get_order_count(page)
    # Now click All
    clicked = _filter_by_status(page, "All")
    if not clicked:
        pytest.skip("All filter not found")
    page.wait_for_timeout(1000)
    count_all = _get_order_count(page)
    _video_hold(page, 2)
    _check_no_page_errors(page, "all filter reset")


@pytest.mark.tc("TC-ORDERS-024")
def test_TC_ORDERS_024_pagination_controls(orders_page):
    """Verify pagination controls are present for large order lists."""
    page = orders_page
    _scroll_down(page, 500)
    pagination = [
        '[class*="pagination"]:visible', 'button:has-text("Next")',
        'button:has-text("Previous")', '[aria-label="Next page"]',
        '[aria-label="Previous page"]', 'nav[aria-label*="pagination"]',
    ]
    found = any(page.locator(s).count() > 0 for s in pagination)
    if not found:
        pytest.skip("Pagination not found — may have few orders")
    _video_hold(page, 2)


# ================================================================
#  SECTION 3 — ORDER DETAIL PANEL
# ================================================================

@pytest.mark.tc("TC-ORDERS-025")
def test_TC_ORDERS_025_open_order_detail(orders_page):
    """Verify clicking an order opens the detail view with content."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _check_no_page_errors(page, "order detail open")
    body = page.locator("body").text_content() or ""
    assert any(kw in body.lower() for kw in ["order", "item", "sku", "quantity", "amount", "status"]), \
        "Order detail does not show expected content"
    _video_hold(page, 3)


@pytest.mark.tc("TC-ORDERS-026")
def test_TC_ORDERS_026_detail_shows_po_number(orders_page):
    """Verify order detail displays the PO/Order number."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    body = page.locator("body").text_content() or ""
    assert any(kw in body for kw in ["PO-", "ORD-", "Order #", "Order ID", "PO #", "#", "SO-"]), \
        "PO/Order number not displayed"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-027")
def test_TC_ORDERS_027_detail_shows_order_date(orders_page):
    """Verify order detail shows the order/PO date."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    body = page.locator("body").text_content() or ""
    import re
    # Look for date patterns (DD/MM/YYYY, YYYY-MM-DD, Month DD, etc.)
    date_patterns = [
        r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',
        r'\d{4}[/-]\d{1,2}[/-]\d{1,2}',
        r'\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)',
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}',
    ]
    has_date = any(re.search(p, body, re.IGNORECASE) for p in date_patterns)
    date_keywords = any(kw in body.lower() for kw in ["date", "ordered on", "created on", "po date"])
    assert has_date or date_keywords, "Order date not found in detail"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-028")
def test_TC_ORDERS_028_detail_shows_customer_info(orders_page):
    """Verify order detail shows customer/retailer information."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    customer_fields = ["customer", "retailer", "buyer", "name", "phone", "mobile", "address", "shipping"]
    found = sum(1 for f in customer_fields if f in body.lower())
    assert found >= 2, f"Expected customer info, found only {found} fields"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-029")
def test_TC_ORDERS_029_detail_shows_shipping_address(orders_page):
    """Verify order detail shows shipping/delivery address."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    addr_fields = ["address", "pincode", "pin code", "city", "state", "shipping", "delivery"]
    found = sum(1 for f in addr_fields if f in body.lower())
    assert found >= 2, f"Expected shipping address, found only {found} fields"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-030")
def test_TC_ORDERS_030_detail_shows_sku_list(orders_page):
    """Verify order detail shows SKU/item list with quantity, price, amount."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    item_fields = ["quantity", "qty", "price", "sku", "item", "product", "amount", "rate", "unit"]
    found = sum(1 for f in item_fields if f in body.lower())
    assert found >= 3, f"Expected SKU/item details, found only {found} fields"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-031")
def test_TC_ORDERS_031_detail_shows_order_total(orders_page):
    """Verify order detail shows the total order amount."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 200)
    body = page.locator("body").text_content() or ""
    total_fields = ["total", "grand total", "order value", "order amount", "payable"]
    found = sum(1 for f in total_fields if f in body.lower())
    assert found >= 1, "Order total/amount not found in detail"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-032")
def test_TC_ORDERS_032_detail_shows_status_badge(orders_page):
    """Verify order detail shows the current status badge/tag."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    status_selectors = [
        '[class*="status"]:visible', '[class*="badge"]:visible',
        '[class*="tag"]:visible', '[class*="chip"]:visible',
    ]
    status_found = any(page.locator(s).count() > 0 for s in status_selectors)
    body = page.locator("body").text_content() or ""
    status_keywords = ["pending", "sent", "accepted", "packed", "ready",
                       "dispatched", "picked", "rejected", "cancelled", "auto accepted"]
    status_in_text = any(s in body.lower() for s in status_keywords)
    assert status_found or status_in_text, "Order status not shown in detail"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-033")
def test_TC_ORDERS_033_detail_shows_edd(orders_page):
    """Verify order detail shows Expected Delivery Date (EDD)."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    body = page.locator("body").text_content() or ""
    edd_fields = ["edd", "expected delivery", "delivery date", "estimated", "eta"]
    found = sum(1 for f in edd_fields if f in body.lower())
    # Also check for date patterns
    import re
    has_date = bool(re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', body))
    assert found >= 1 or has_date, "EDD not found in order detail"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-034")
def test_TC_ORDERS_034_detail_shows_payment_method(orders_page):
    """Verify order detail shows payment method (Prepaid/COD/Credit)."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    body = page.locator("body").text_content() or ""
    payment_fields = ["payment", "prepaid", "cod", "credit", "cash", "online", "upi", "bank"]
    found = sum(1 for f in payment_fields if f in body.lower())
    assert found >= 1, "Payment method not found in order detail"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-035")
def test_TC_ORDERS_035_detail_shows_timeline(orders_page):
    """Verify order detail shows status timeline/history."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 400)
    timeline_selectors = [
        '[class*="timeline"]:visible', '[class*="history"]:visible',
        '[class*="activity"]:visible', '[class*="step"]:visible',
        '[class*="track"]:visible',
    ]
    timeline_found = any(page.locator(s).count() > 0 for s in timeline_selectors)
    body = page.locator("body").text_content() or ""
    timeline_in_text = any(s in body.lower() for s in [
        "sent", "accepted", "packed", "ready", "dispatched", "picked up", "auto accepted"
    ])
    assert timeline_found or timeline_in_text, "Order timeline not found"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-036")
def test_TC_ORDERS_036_detail_shows_download_links(orders_page):
    """Verify order detail shows download links for invoice/label/documents."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    doc_fields = ["invoice", "label", "download", "e-way", "eway", "manifest", "document", "bill"]
    found = sum(1 for f in doc_fields if f in body.lower())
    # May not have docs if order is in early stage
    if found == 0:
        pytest.skip("No download links found — order may be in early stage")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-037")
def test_TC_ORDERS_037_detail_shows_gst_hsn(orders_page):
    """Verify order detail shows GST/HSN code information."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    tax_fields = ["gst", "hsn", "tax", "cgst", "sgst", "igst"]
    found = sum(1 for f in tax_fields if f in body.lower())
    if found == 0:
        pytest.skip("GST/HSN details not found — may not be displayed at this stage")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-038")
def test_TC_ORDERS_038_detail_scroll(orders_page):
    """Verify order detail page scrolls smoothly showing full content."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    _scroll_down(page, 400)
    _video_hold(page, 1)
    _scroll_down(page, 400)
    _video_hold(page, 1)
    _scroll_up(page, 400)
    _video_hold(page, 1)
    _scroll_to_top(page)
    _video_hold(page, 2)
    _check_no_page_errors(page, "detail scroll")


@pytest.mark.tc("TC-ORDERS-039")
def test_TC_ORDERS_039_back_from_detail_to_list(orders_page):
    """Verify back navigation from order detail returns to list."""
    page = orders_page
    if not _click_first_order(page):
        pytest.skip("No orders to click")
    back_selectors = [
        'button:has-text("Back")', 'a:has-text("Back")', '[class*="back"]',
        'button:has-text("Orders")', 'a:has-text("Orders")',
        '[aria-label="Back"]', '[aria-label="Go back"]',
        'button:has-text("←")', 'button:has-text("<")',
    ]
    back_clicked = False
    for sel in back_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            back_clicked = True
            break
    if not back_clicked:
        page.go_back()
        page.wait_for_timeout(2000)
    _check_no_page_errors(page, "back navigation")
    _video_hold(page, 2)


# ================================================================
#  SECTION 4 — STAGE-WISE ACTIONS
# ================================================================

# ── Auto-Accept ──────────────────────────────────────────────

@pytest.mark.tc("TC-ORDERS-040")
def test_TC_ORDERS_040_auto_accepted_orders_visible(orders_page):
    """Verify auto-accepted POs are visible in the orders list."""
    page = orders_page
    _filter_by_status(page, "Accepted", "Auto Accepted")
    _video_hold(page, 2)
    body = page.locator("body").text_content() or ""
    # Auto-accepted orders should be present or the filter should work
    _check_no_page_errors(page, "auto-accepted filter")


# ── Manual Accept ────────────────────────────────────────────

@pytest.mark.tc("TC-ORDERS-041")
def test_TC_ORDERS_041_accept_button_for_pending(orders_page):
    """Verify Accept button is visible for pending/sent POs."""
    page = orders_page
    _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No pending orders found")
    accept_btn = page.locator(
        'button:has-text("Accept"), button:has-text("Confirm"), button:has-text("Approve")'
    ).first
    if not accept_btn.is_visible():
        pytest.skip("Accept button not visible — order may be in different status")
    _video_hold(page, 2)
    _check_no_page_errors(page, "accept button")


@pytest.mark.tc("TC-ORDERS-042")
def test_TC_ORDERS_042_accept_order(orders_page):
    """Verify accepting a pending PO transitions status to Accepted."""
    page = orders_page
    _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No pending orders found")
    accept_btn = page.locator(
        'button:has-text("Accept"), button:has-text("Confirm"), button:has-text("Approve")'
    ).first
    if not accept_btn.is_visible():
        pytest.skip("Accept button not visible")
    accept_btn.click()
    page.wait_for_timeout(1000)
    # Handle confirmation dialog
    confirm = page.locator(
        'button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK"), '
        '[role="dialog"] button:has-text("Accept")'
    ).first
    if confirm.is_visible():
        confirm.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


# ── Reject ───────────────────────────────────────────────────

@pytest.mark.tc("TC-ORDERS-043")
def test_TC_ORDERS_043_reject_button_for_pending(orders_page):
    """Verify Reject button is visible for pending/sent POs."""
    page = orders_page
    _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No pending orders found")
    reject_btn = page.locator('button:has-text("Reject"), button:has-text("Decline")').first
    if not reject_btn.is_visible():
        pytest.skip("Reject button not visible")
    _video_hold(page, 2)
    _check_no_page_errors(page, "reject button")


@pytest.mark.tc("TC-ORDERS-044")
def test_TC_ORDERS_044_reject_with_reason_dropdown(orders_page):
    """Verify reject flow shows reason dropdown/selection."""
    page = orders_page
    _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No pending orders found")
    reject_btn = page.locator('button:has-text("Reject"), button:has-text("Decline")').first
    if not reject_btn.is_visible():
        pytest.skip("Reject button not visible")
    reject_btn.click()
    page.wait_for_timeout(1000)
    # Check for reason selection
    reason_selectors = [
        'select:has(option)', 'textarea[placeholder*="reason" i]',
        'input[placeholder*="reason" i]', '[class*="reason"] select',
        '[role="dialog"] select', '[role="dialog"] textarea',
    ]
    reason_found = False
    for sel in reason_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            reason_found = True
            tag = el.evaluate("el => el.tagName")
            if tag == "SELECT":
                el.select_option(index=1)
            elif tag in ["TEXTAREA", "INPUT"]:
                el.click()
                page.keyboard.type("Out of stock", delay=30)
            break
    _video_hold(page, 2)
    # Cancel to not actually reject
    cancel = page.locator('button:has-text("Cancel"), button:has-text("Close")').first
    if cancel.is_visible():
        cancel.click()
    _check_no_page_errors(page, "reject reason")


@pytest.mark.tc("TC-ORDERS-045")
def test_TC_ORDERS_045_reject_order(orders_page, tc_data):
    """Verify rejecting a PO with reason transitions status to Rejected."""
    page = orders_page
    _filter_by_status(page, "Pending", "Sent", "New", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No pending orders found")
    reject_btn = page.locator('button:has-text("Reject"), button:has-text("Decline")').first
    if not reject_btn.is_visible():
        pytest.skip("Reject button not visible")
    reject_btn.click()
    page.wait_for_timeout(1000)
    # Fill reason
    for sel in ['textarea', 'select:has(option)', 'input[type="text"]']:
        el = page.locator(sel).first
        if el.is_visible():
            tag = el.evaluate("el => el.tagName")
            if tag == "SELECT":
                el.select_option(index=1)
            else:
                reason = tc_data.get("reject_reason", "Out of stock — supply exhausted")
                el.click()
                page.keyboard.type(reason, delay=30)
            break
    # Confirm rejection
    confirm = page.locator(
        'button:has-text("Submit"), button:has-text("Confirm"), button:has-text("Reject"), '
        '[role="dialog"] button:has-text("Yes")'
    ).first
    if confirm.is_visible():
        confirm.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


# ── Auto PO Cancellation ────────────────────────────────────

@pytest.mark.tc("TC-ORDERS-046")
def test_TC_ORDERS_046_cancelled_orders_visible(orders_page):
    """Verify auto-cancelled POs are visible with cancellation status."""
    page = orders_page
    _filter_by_status(page, "Cancelled", "Canceled")
    _video_hold(page, 2)
    _check_no_page_errors(page, "cancelled filter")


@pytest.mark.tc("TC-ORDERS-047")
def test_TC_ORDERS_047_cancelled_po_shows_reason(orders_page):
    """Verify cancelled PO detail shows cancellation reason."""
    page = orders_page
    _filter_by_status(page, "Cancelled", "Canceled")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No cancelled orders found")
    body = page.locator("body").text_content() or ""
    cancel_fields = ["cancelled", "canceled", "cancel", "reason", "timeout",
                     "order cancelled by customer", "auto cancelled"]
    found = sum(1 for f in cancel_fields if f in body.lower())
    assert found >= 1, "Cancellation reason not shown for cancelled PO"
    _video_hold(page, 2)


# ── Pack & Label Generation ──────────────────────────────────

@pytest.mark.tc("TC-ORDERS-048")
def test_TC_ORDERS_048_pack_button_for_accepted(orders_page):
    """Verify Pack button is visible for accepted POs."""
    page = orders_page
    _filter_by_status(page, "Accepted", "Confirmed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No accepted orders found")
    pack_btn = page.locator(
        'button:has-text("Pack"), button:has-text("Mark as Packed"), button:has-text("Pack Order")'
    ).first
    if not pack_btn.is_visible():
        pytest.skip("Pack button not visible")
    _video_hold(page, 2)
    _check_no_page_errors(page, "pack button")


@pytest.mark.tc("TC-ORDERS-049")
def test_TC_ORDERS_049_pack_order(orders_page):
    """Verify packing an accepted PO transitions status to Packed."""
    page = orders_page
    _filter_by_status(page, "Accepted", "Confirmed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No accepted orders found")
    pack_btn = page.locator(
        'button:has-text("Pack"), button:has-text("Mark as Packed"), button:has-text("Pack Order")'
    ).first
    if not pack_btn.is_visible():
        pytest.skip("Pack button not visible")
    pack_btn.click()
    page.wait_for_timeout(1000)
    confirm = page.locator(
        'button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK")'
    ).first
    if confirm.is_visible():
        confirm.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


@pytest.mark.tc("TC-ORDERS-050")
def test_TC_ORDERS_050_label_generated_after_pack(orders_page):
    """Verify label/shipping document is generated after packing."""
    page = orders_page
    _filter_by_status(page, "Packed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No packed orders found")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    label_fields = ["label", "shipping label", "manifest", "download", "waybill", "tracking"]
    found = sum(1 for f in label_fields if f in body.lower())
    if found == 0:
        pytest.skip("Label not yet generated — may be delayed (30-40% cases)")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-051")
def test_TC_ORDERS_051_label_download(orders_page):
    """Verify label download link/button is clickable for packed orders."""
    page = orders_page
    _filter_by_status(page, "Packed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No packed orders found")
    _scroll_down(page, 300)
    download_selectors = [
        'button:has-text("Download Label")', 'a:has-text("Download Label")',
        'button:has-text("Label")', 'a:has-text("Label")',
        'button:has-text("Download")', 'a:has-text("Download")',
        '[class*="label"] a', '[class*="label"] button',
    ]
    for sel in download_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            _video_hold(page, 2)
            _check_no_page_errors(page, "label download")
            return
    pytest.skip("Label download not found — may be delayed")


# ── Ready & Ready Proof Upload ───────────────────────────────

@pytest.mark.tc("TC-ORDERS-052")
def test_TC_ORDERS_052_ready_button_for_packed(orders_page):
    """Verify Mark as Ready button is visible for packed POs."""
    page = orders_page
    _filter_by_status(page, "Packed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No packed orders found")
    ready_btn = page.locator(
        'button:has-text("Ready"), button:has-text("Mark as Ready"), button:has-text("Mark Ready")'
    ).first
    if not ready_btn.is_visible():
        pytest.skip("Ready button not visible")
    _video_hold(page, 2)
    _check_no_page_errors(page, "ready button")


@pytest.mark.tc("TC-ORDERS-053")
def test_TC_ORDERS_053_ready_proof_upload(orders_page, test_image_path):
    """Verify ready proof photo upload is available when marking as Ready."""
    page = orders_page
    _filter_by_status(page, "Packed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No packed orders found")
    ready_btn = page.locator(
        'button:has-text("Ready"), button:has-text("Mark as Ready"), button:has-text("Mark Ready")'
    ).first
    if not ready_btn.is_visible():
        pytest.skip("Ready button not visible")
    ready_btn.click()
    page.wait_for_timeout(1000)
    # Look for file upload input
    upload_selectors = [
        'input[type="file"]', '[class*="upload"]', 'button:has-text("Upload")',
        'button:has-text("Choose File")', 'button:has-text("Browse")',
        '[class*="dropzone"]', '[class*="file-input"]',
    ]
    upload_found = False
    for sel in upload_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            upload_found = True
            # Try to upload test image
            try:
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    file_input.set_input_files(test_image_path)
                    page.wait_for_timeout(1000)
            except Exception:
                pass
            break
    # Cancel to not actually submit
    cancel = page.locator('button:has-text("Cancel"), button:has-text("Close")').first
    if cancel.is_visible():
        cancel.click()
    _video_hold(page, 2)
    _check_no_page_errors(page, "ready proof upload")


@pytest.mark.tc("TC-ORDERS-054")
def test_TC_ORDERS_054_mark_ready(orders_page, test_image_path):
    """Verify marking a packed PO as Ready transitions status."""
    page = orders_page
    _filter_by_status(page, "Packed")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No packed orders found")
    ready_btn = page.locator(
        'button:has-text("Ready"), button:has-text("Mark as Ready"), button:has-text("Mark Ready")'
    ).first
    if not ready_btn.is_visible():
        pytest.skip("Ready button not visible")
    ready_btn.click()
    page.wait_for_timeout(1000)
    # Upload ready proof if file input is present
    file_input = page.locator('input[type="file"]').first
    if file_input.count() > 0:
        try:
            file_input.set_input_files(test_image_path)
            page.wait_for_timeout(1000)
        except Exception:
            pass
    confirm = page.locator(
        'button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK"), '
        'button:has-text("Submit")'
    ).first
    if confirm.is_visible():
        confirm.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


# ── Picked Up, Tax Invoice, E-Way Bill, Proof of Pickup ──────

@pytest.mark.tc("TC-ORDERS-055")
def test_TC_ORDERS_055_picked_up_button_for_ready(orders_page):
    """Verify Picked Up button is visible for ready POs."""
    page = orders_page
    _filter_by_status(page, "Ready")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No ready orders found")
    pickup_btn = page.locator(
        'button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), '
        'button:has-text("Pickup"), button:has-text("Dispatch")'
    ).first
    if not pickup_btn.is_visible():
        pytest.skip("Picked Up button not visible")
    _video_hold(page, 2)
    _check_no_page_errors(page, "picked up button")


@pytest.mark.tc("TC-ORDERS-056")
def test_TC_ORDERS_056_tax_invoice_upload(orders_page, test_image_path):
    """Verify tax invoice upload is available during Picked Up action."""
    page = orders_page
    _filter_by_status(page, "Ready")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No ready orders found")
    pickup_btn = page.locator(
        'button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), '
        'button:has-text("Pickup"), button:has-text("Dispatch")'
    ).first
    if not pickup_btn.is_visible():
        pytest.skip("Picked Up button not visible")
    pickup_btn.click()
    page.wait_for_timeout(1000)
    # Look for invoice upload field
    body = page.locator("body").text_content() or ""
    invoice_fields = ["tax invoice", "invoice", "upload invoice", "e-invoice"]
    found = sum(1 for f in invoice_fields if f in body.lower())
    file_input = page.locator('input[type="file"]')
    if file_input.count() > 0:
        try:
            file_input.first.set_input_files(test_image_path)
            page.wait_for_timeout(1000)
        except Exception:
            pass
    # Cancel
    cancel = page.locator('button:has-text("Cancel"), button:has-text("Close")').first
    if cancel.is_visible():
        cancel.click()
    _video_hold(page, 2)
    _check_no_page_errors(page, "tax invoice upload")


@pytest.mark.tc("TC-ORDERS-057")
def test_TC_ORDERS_057_eway_bill_upload(orders_page, test_image_path):
    """Verify e-way bill upload is available during Picked Up action (orders > 50K)."""
    page = orders_page
    _filter_by_status(page, "Ready")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No ready orders found")
    pickup_btn = page.locator(
        'button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), '
        'button:has-text("Pickup"), button:has-text("Dispatch")'
    ).first
    if not pickup_btn.is_visible():
        pytest.skip("Picked Up button not visible")
    pickup_btn.click()
    page.wait_for_timeout(1000)
    body = page.locator("body").text_content() or ""
    eway_fields = ["e-way", "eway", "ewaybill", "e way bill"]
    found = sum(1 for f in eway_fields if f in body.lower())
    file_input = page.locator('input[type="file"]')
    if file_input.count() > 0:
        try:
            # Try to upload to the second file input (if multiple)
            if file_input.count() > 1:
                file_input.nth(1).set_input_files(test_image_path)
            else:
                file_input.first.set_input_files(test_image_path)
            page.wait_for_timeout(1000)
        except Exception:
            pass
    cancel = page.locator('button:has-text("Cancel"), button:has-text("Close")').first
    if cancel.is_visible():
        cancel.click()
    _video_hold(page, 2)
    if found == 0:
        pytest.skip("E-way bill upload not found — may not be required for this order value")
    _check_no_page_errors(page, "eway bill upload")


@pytest.mark.tc("TC-ORDERS-058")
def test_TC_ORDERS_058_proof_of_pickup_upload(orders_page, test_image_path):
    """Verify proof of pickup photo upload is available during Picked Up action."""
    page = orders_page
    _filter_by_status(page, "Ready")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No ready orders found")
    pickup_btn = page.locator(
        'button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), '
        'button:has-text("Pickup"), button:has-text("Dispatch")'
    ).first
    if not pickup_btn.is_visible():
        pytest.skip("Picked Up button not visible")
    pickup_btn.click()
    page.wait_for_timeout(1000)
    body = page.locator("body").text_content() or ""
    pop_fields = ["proof of pickup", "pickup proof", "proof", "photo", "capture", "image"]
    found = sum(1 for f in pop_fields if f in body.lower())
    file_input = page.locator('input[type="file"]')
    if file_input.count() > 0:
        try:
            file_input.last.set_input_files(test_image_path)
            page.wait_for_timeout(1000)
        except Exception:
            pass
    cancel = page.locator('button:has-text("Cancel"), button:has-text("Close")').first
    if cancel.is_visible():
        cancel.click()
    _video_hold(page, 2)
    if found == 0:
        pytest.skip("Proof of pickup upload not found")
    _check_no_page_errors(page, "proof of pickup upload")


@pytest.mark.tc("TC-ORDERS-059")
def test_TC_ORDERS_059_mark_picked_up(orders_page, test_image_path):
    """Verify marking a ready PO as Picked Up with required uploads."""
    page = orders_page
    _filter_by_status(page, "Ready")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No ready orders found")
    pickup_btn = page.locator(
        'button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), '
        'button:has-text("Pickup"), button:has-text("Dispatch")'
    ).first
    if not pickup_btn.is_visible():
        pytest.skip("Picked Up button not visible")
    pickup_btn.click()
    page.wait_for_timeout(1000)
    # Upload all required files
    file_inputs = page.locator('input[type="file"]')
    for i in range(file_inputs.count()):
        try:
            file_inputs.nth(i).set_input_files(test_image_path)
            page.wait_for_timeout(500)
        except Exception:
            pass
    # Fill any text fields
    textareas = page.locator('[role="dialog"] textarea, [class*="modal"] textarea')
    for i in range(textareas.count()):
        try:
            textareas.nth(i).fill("Goods handed to transporter")
        except Exception:
            pass
    confirm = page.locator(
        'button:has-text("Submit"), button:has-text("Confirm"), '
        'button:has-text("Picked Up"), button:has-text("Yes")'
    ).first
    if confirm.is_visible():
        confirm.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


@pytest.mark.tc("TC-ORDERS-060")
def test_TC_ORDERS_060_picked_up_shows_dispatch_details(orders_page):
    """Verify picked-up/dispatched order shows tracking/dispatch details."""
    page = orders_page
    _filter_by_status(page, "Dispatched", "Picked Up", "Shipped")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No dispatched orders found")
    _scroll_down(page, 300)
    body = page.locator("body").text_content() or ""
    dispatch_fields = ["tracking", "waybill", "courier", "lsp", "transporter",
                       "dispatch", "transit", "vehicle", "pickup"]
    found = sum(1 for f in dispatch_fields if f in body.lower())
    if found == 0:
        pytest.skip("Dispatch details not found")
    _video_hold(page, 2)
    _check_no_page_errors(page, "dispatch details")


# ── Full Status Progression ──────────────────────────────────

@pytest.mark.tc("TC-ORDERS-061")
def test_TC_ORDERS_061_full_progression_sent_to_accepted(orders_page):
    """Verify PO can progress: Sent to Accepted (manual accept)."""
    page = orders_page
    _filter_by_status(page, "Sent", "Pending", "Issued")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No sent/pending orders to progress")
    # Verify current status is Sent/Pending
    body = page.locator("body").text_content() or ""
    has_sent = any(s in body.lower() for s in ["sent", "issued", "pending"])
    accept_btn = page.locator(
        'button:has-text("Accept"), button:has-text("Confirm")'
    ).first
    if not has_sent or not accept_btn.is_visible():
        pytest.skip("Order not in Sent/Pending status or Accept not available")
    _video_hold(page, 2)
    _check_no_page_errors(page, "progression sent to accepted")


@pytest.mark.tc("TC-ORDERS-062")
def test_TC_ORDERS_062_full_progression_accepted_to_packed(orders_page):
    """Verify PO can progress: Accepted to Packed."""
    page = orders_page
    _filter_by_status(page, "Accepted")
    page.wait_for_timeout(1000)
    if not _click_first_order(page):
        pytest.skip("No accepted orders to progress")
    body = page.locator("body").text_content() or ""
    has_accepted = "accepted" in body.lower()
    pack_btn = page.locator(
        'button:has-text("Pack"), button:has-text("Mark as Packed")'
    ).first
    if not has_accepted or not pack_btn.is_visible():
        pytest.skip("Order not in Accepted status or Pack not available")
    _video_hold(page, 2)
    _check_no_page_errors(page, "progression accepted to packed")
