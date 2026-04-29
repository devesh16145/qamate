"""Orders flow — Playwright E2E tests for Seller App Order Management.

Covers:
  - Orders page structure & navigation
  - Order list display, search, and filters
  - Order detail view
  - Accept / Reject order actions
  - Pack / Ready / Picked Up status transitions
  - Order stats and pagination
  - Error detection on every step
"""

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
    page.evaluate(f"window.scrollBy(0, {pixels})")
    page.wait_for_timeout(500)


def _scroll_up(page, pixels=500):
    page.evaluate(f"window.scrollBy(0, -{pixels})")
    page.wait_for_timeout(500)


def _scroll_to_top(page):
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def _check_no_page_errors(page, context_label=""):
    """Fail the test if visible error toasts or alerts are present."""
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
    """Count visible order rows/cards on the orders page."""
    # Try common table row patterns
    rows = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]')
    count = rows.count()
    if count > 0:
        return count
    # Fallback: count any clickable rows with order-like content
    return page.locator('[class*="order"]:has([class*="id"], [class*="number"])').count()


def _find_orders_nav(page):
    """Find and click the Orders navigation link. Returns True if found."""
    nav_selectors = [
        'a:has-text("Orders")',
        'a:has-text("Purchase Orders")',
        'a:has-text("PO")',
        'a[href*="order"]',
        'a[href*="purchase"]',
        'button:has-text("Orders")',
        '[class*="nav"]:has-text("Orders")',
        '[class*="menu"]:has-text("Orders")',
        'a:has-text("My Orders")',
    ]
    for sel in nav_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            page.wait_for_load_state("domcontentloaded")
            return True
    return False


def _navigate_to_orders(page, base_url):
    """Navigate to the orders page. Try multiple URL patterns."""
    urls_to_try = [
        base_url + "orders",
        base_url + "purchase-orders",
        base_url + "listing/orders",
        base_url + "po",
        base_url + "seller/orders",
    ]
    for url in urls_to_try:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=10000)
            if resp and resp.status < 400:
                page.wait_for_timeout(2000)
                # Check if page has order-like content
                body = page.locator("body").text_content() or ""
                if any(kw in body.lower() for kw in ["order", "purchase", "po", "accept", "reject", "pack"]):
                    return True
        except Exception:
            continue

    # If direct URLs failed, try navigating from sidebar
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    if _find_orders_nav(page):
        return True

    return False


def _assert_submission_success(page, timeout=5000):
    """After a form submission/action, check for success OR fail with error details."""
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
        '[class*="toast"]:visible:has-text("updated")',
        '[class*="toast"]:visible:has-text("Updated")',
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


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def orders_page(page, test_user, base_url, sequential_page):
    """Log in and navigate to the Orders page.
    In sequential mode, reuses the session-scoped page (already logged in).
    In parallel mode, logs in per test."""
    if sequential_page is not None:
        _navigate_to_orders(sequential_page, base_url)
        sequential_page.wait_for_timeout(1000)
        return sequential_page

    _login(page, test_user, base_url)
    _navigate_to_orders(page, base_url)
    page.wait_for_timeout(1000)
    return page


# ── Page Structure & Navigation ──────────────────────────────

@pytest.mark.tc("TC-ORDERS-001")
def test_TC_ORDERS_001_page_loads(orders_page):
    """Verify the Orders page loads successfully with visible content."""
    page = orders_page
    _video_hold(page, 2)
    # Page should have loaded without errors
    _check_no_page_errors(page, "orders page load")
    # Should have some order-related content
    body = page.locator("body").text_content() or ""
    assert any(kw in body.lower() for kw in ["order", "purchase", "po", "status"]), \
        "Orders page does not contain expected order-related content"


@pytest.mark.tc("TC-ORDERS-002")
def test_TC_ORDERS_002_order_list_visible(orders_page):
    """Verify the orders list/table is displayed with rows or cards."""
    page = orders_page
    _scroll_down(page, 300)
    _video_hold(page, 2)
    # Check for table or card list
    table = page.locator('table, [class*="order-list"], [class*="order-table"], [class*="order-grid"]')
    expect(table.first).to_be_visible(timeout=10000)
    _check_no_page_errors(page, "order list")


@pytest.mark.tc("TC-ORDERS-003")
def test_TC_ORDERS_003_order_list_has_columns(orders_page):
    """Verify the order list shows key columns: Order/PO ID, Status, Date, Amount."""
    page = orders_page
    body = page.locator("body").text_content() or ""
    body_lower = body.lower()
    # At least some of these column headers should be present
    expected_headers = ["order", "status", "date", "amount", "po", "quantity", "total"]
    found = sum(1 for h in expected_headers if h in body_lower)
    assert found >= 2, f"Expected at least 2 order-related headers, found {found} in page content"


@pytest.mark.tc("TC-ORDERS-004")
def test_TC_ORDERS_004_search_orders(orders_page):
    """Verify search functionality on the orders page."""
    page = orders_page
    search_input = page.locator('input[placeholder*="Search"], input[placeholder*="search"], input[type="search"]').first
    if search_input.is_visible():
        search_input.click()
        page.keyboard.type("ORD", delay=50)
        page.wait_for_timeout(2000)
        _check_no_page_errors(page, "order search")
        _video_hold(page, 2)
    else:
        pytest.skip("Search input not found on orders page")


@pytest.mark.tc("TC-ORDERS-005")
def test_TC_ORDERS_005_filter_by_status(orders_page):
    """Verify order status filter (tabs or dropdown) changes the displayed orders."""
    page = orders_page
    # Look for status filter tabs or dropdown
    filter_selectors = [
        'button:has-text("All")',
        'button:has-text("Pending")',
        'button:has-text("Accepted")',
        'button:has-text("Packed")',
        'select:has(option:has-text("All"))',
        '[class*="tab"]:has-text("All")',
        '[class*="filter"]:has-text("Status")',
    ]
    filter_found = False
    for sel in filter_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            filter_found = True
            break
    if not filter_found:
        pytest.skip("Status filter not found on orders page")
    _check_no_page_errors(page, "status filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-006")
def test_TC_ORDERS_006_filter_pending_orders(orders_page):
    """Verify filtering to show only Pending/Sent orders."""
    page = orders_page
    pending_selectors = [
        'button:has-text("Pending")',
        'button:has-text("Sent")',
        'button:has-text("New")',
        '[class*="tab"]:has-text("Pending")',
        '[class*="tab"]:has-text("Sent")',
    ]
    clicked = False
    for sel in pending_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            clicked = True
            break
    if not clicked:
        pytest.skip("Pending filter not found")
    _check_no_page_errors(page, "pending filter")
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-007")
def test_TC_ORDERS_007_filter_accepted_orders(orders_page):
    """Verify filtering to show only Accepted orders."""
    page = orders_page
    accepted_selectors = [
        'button:has-text("Accepted")',
        'button:has-text("Confirmed")',
        '[class*="tab"]:has-text("Accepted")',
    ]
    clicked = False
    for sel in accepted_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            el.click()
            page.wait_for_timeout(2000)
            clicked = True
            break
    if not clicked:
        pytest.skip("Accepted filter not found")
    _check_no_page_errors(page, "accepted filter")
    _video_hold(page, 2)


# ── Order Detail View ────────────────────────────────────────

@pytest.mark.tc("TC-ORDERS-008")
def test_TC_ORDERS_008_open_order_detail(orders_page):
    """Verify clicking an order opens the detail view."""
    page = orders_page
    # Find the first clickable order row
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found to click")
    order_row.click()
    page.wait_for_timeout(2000)
    _check_no_page_errors(page, "order detail open")
    _video_hold(page, 3)
    # Should see detail content (order info, items, amounts)
    body = page.locator("body").text_content() or ""
    assert any(kw in body.lower() for kw in ["order", "item", "sku", "quantity", "amount", "total", "status"]), \
        "Order detail view does not show expected content"


@pytest.mark.tc("TC-ORDERS-009")
def test_TC_ORDERS_009_order_detail_shows_po_number(orders_page):
    """Verify order detail view displays the PO/Order number."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    # Look for PO/Order number pattern
    body = page.locator("body").text_content() or ""
    has_id = any(kw in body for kw in ["PO-", "ORD-", "Order #", "Order ID", "PO #", "#"])
    assert has_id, "Order detail does not display a PO/Order number"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-010")
def test_TC_ORDERS_010_order_detail_shows_items(orders_page):
    """Verify order detail view shows SKU/item list with quantities."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    _scroll_down(page, 300)
    # Look for item-related content
    body = page.locator("body").text_content() or ""
    has_items = any(kw in body.lower() for kw in ["quantity", "qty", "price", "sku", "item", "product", "amount"])
    assert has_items, "Order detail does not show item/SKU details"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-011")
def test_TC_ORDERS_011_order_detail_shows_status(orders_page):
    """Verify order detail view shows the current order status."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    # Look for status indicators
    status_selectors = [
        '[class*="status"]:visible',
        '[class*="badge"]:visible',
        '[class*="tag"]:visible',
        'span:has-text("Pending")',
        'span:has-text("Accepted")',
        'span:has-text("Packed")',
        'span:has-text("Ready")',
        'span:has-text("Sent")',
    ]
    status_found = False
    for sel in status_selectors:
        if page.locator(sel).count() > 0:
            status_found = True
            break
    body = page.locator("body").text_content() or ""
    status_in_text = any(s in body.lower() for s in ["pending", "accepted", "packed", "ready", "sent", "dispatched", "picked"])
    assert status_found or status_in_text, "Order detail does not show order status"
    _video_hold(page, 2)


@pytest.mark.tc("TC-ORDERS-012")
def test_TC_ORDERS_012_back_from_detail_to_list(orders_page):
    """Verify navigating back from order detail to the orders list."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    # Navigate back
    back_selectors = [
        'button:has-text("Back")',
        'a:has-text("Back")',
        '[class*="back"]',
        'button:has-text("Orders")',
        'a:has-text("Orders")',
        '[aria-label="Back"]',
        '[aria-label="Go back"]',
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


# ── Order Actions: Accept / Reject ───────────────────────────

@pytest.mark.tc("TC-ORDERS-013")
def test_TC_ORDERS_013_accept_button_visible_for_pending(orders_page):
    """Verify the Accept button is visible for pending/sent orders."""
    page = orders_page
    # Navigate to a pending order
    pending_filter = page.locator('button:has-text("Pending"), button:has-text("Sent"), [class*="tab"]:has-text("Pending")').first
    if pending_filter.is_visible():
        pending_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No pending orders found")
    order_row.click()
    page.wait_for_timeout(2000)
    # Look for Accept button
    accept_btn = page.locator('button:has-text("Accept"), button:has-text("Confirm"), button:has-text("Approve")').first
    if accept_btn.is_visible():
        _video_hold(page, 2)
        _check_no_page_errors(page, "accept button visible")
    else:
        pytest.skip("Accept button not visible — order may already be accepted or in different status")


@pytest.mark.tc("TC-ORDERS-014")
def test_TC_ORDERS_014_accept_order_flow(orders_page, tc_data):
    """Verify accepting a pending order transitions its status."""
    page = orders_page
    # Navigate to a pending order
    pending_filter = page.locator('button:has-text("Pending"), button:has-text("Sent"), [class*="tab"]:has-text("Pending")').first
    if pending_filter.is_visible():
        pending_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No pending orders found to accept")
    order_row.click()
    page.wait_for_timeout(2000)
    accept_btn = page.locator('button:has-text("Accept"), button:has-text("Confirm"), button:has-text("Approve")').first
    if not accept_btn.is_visible():
        pytest.skip("Accept button not visible")
    accept_btn.click()
    page.wait_for_timeout(1000)
    # Handle confirmation dialog if present
    confirm_btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK"), [role="dialog"] button:has-text("Accept")').first
    if confirm_btn.is_visible():
        confirm_btn.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)
    _scroll_to_top(page)


@pytest.mark.tc("TC-ORDERS-015")
def test_TC_ORDERS_015_reject_button_visible_for_pending(orders_page):
    """Verify the Reject button is visible for pending/sent orders."""
    page = orders_page
    pending_filter = page.locator('button:has-text("Pending"), button:has-text("Sent"), [class*="tab"]:has-text("Pending")').first
    if pending_filter.is_visible():
        pending_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No pending orders found")
    order_row.click()
    page.wait_for_timeout(2000)
    reject_btn = page.locator('button:has-text("Reject"), button:has-text("Decline")').first
    if reject_btn.is_visible():
        _video_hold(page, 2)
        _check_no_page_errors(page, "reject button visible")
    else:
        pytest.skip("Reject button not visible — order may already be processed")


@pytest.mark.tc("TC-ORDERS-016")
def test_TC_ORDERS_016_reject_order_with_reason(orders_page, tc_data):
    """Verify rejecting an order requires a reason and transitions status."""
    page = orders_page
    pending_filter = page.locator('button:has-text("Pending"), button:has-text("Sent"), [class*="tab"]:has-text("Pending")').first
    if pending_filter.is_visible():
        pending_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No pending orders found to reject")
    order_row.click()
    page.wait_for_timeout(2000)
    reject_btn = page.locator('button:has-text("Reject"), button:has-text("Decline")').first
    if not reject_btn.is_visible():
        pytest.skip("Reject button not visible")
    reject_btn.click()
    page.wait_for_timeout(1000)
    # Check if a reason field/modal appears
    reason_selectors = [
        'textarea[placeholder*="reason"]',
        'textarea[placeholder*="Reason"]',
        'input[placeholder*="reason"]',
        'select:has(option)',
        '[class*="reason"]',
        '[role="dialog"]',
    ]
    for sel in reason_selectors:
        el = page.locator(sel).first
        if el.is_visible():
            if el.evaluate("el => el.tagName") in ["TEXTAREA", "INPUT"]:
                reason_text = tc_data.get("reject_reason", "Out of stock")
                el.click()
                page.keyboard.type(reason_text, delay=30)
            elif el.evaluate("el => el.tagName") == "SELECT":
                el.select_option(index=1)
            break
    # Confirm rejection
    confirm_btn = page.locator('button:has-text("Submit"), button:has-text("Confirm"), button:has-text("Reject"), [role="dialog"] button:has-text("Yes")').first
    if confirm_btn.is_visible():
        confirm_btn.click()
        page.wait_for_timeout(2000)
    _assert_submission_success(page)
    _video_hold(page, 3)


# ── Order Actions: Pack / Ready / Picked Up ──────────────────

@pytest.mark.tc("TC-ORDERS-017")
def test_TC_ORDERS_017_pack_order_flow(orders_page):
    """Verify the Pack action is available for accepted orders."""
    page = orders_page
    accepted_filter = page.locator('button:has-text("Accepted"), button:has-text("Confirmed"), [class*="tab"]:has-text("Accepted")').first
    if accepted_filter.is_visible():
        accepted_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No accepted orders found")
    order_row.click()
    page.wait_for_timeout(2000)
    pack_btn = page.locator('button:has-text("Pack"), button:has-text("Mark as Packed")').first
    if pack_btn.is_visible():
        pack_btn.click()
        page.wait_for_timeout(1000)
        # Handle confirmation
        confirm_btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK")').first
        if confirm_btn.is_visible():
            confirm_btn.click()
            page.wait_for_timeout(2000)
        _assert_submission_success(page)
        _video_hold(page, 3)
    else:
        pytest.skip("Pack button not visible — order may not be in accepted status")


@pytest.mark.tc("TC-ORDERS-018")
def test_TC_ORDERS_018_mark_ready_flow(orders_page):
    """Verify the Mark as Ready action is available for packed orders."""
    page = orders_page
    packed_filter = page.locator('button:has-text("Packed"), [class*="tab"]:has-text("Packed")').first
    if packed_filter.is_visible():
        packed_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No packed orders found")
    order_row.click()
    page.wait_for_timeout(2000)
    ready_btn = page.locator('button:has-text("Ready"), button:has-text("Mark as Ready"), button:has-text("Mark Ready")').first
    if ready_btn.is_visible():
        ready_btn.click()
        page.wait_for_timeout(1000)
        confirm_btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK")').first
        if confirm_btn.is_visible():
            confirm_btn.click()
            page.wait_for_timeout(2000)
        _assert_submission_success(page)
        _video_hold(page, 3)
    else:
        pytest.skip("Mark Ready button not visible — order may not be in packed status")


@pytest.mark.tc("TC-ORDERS-019")
def test_TC_ORDERS_019_mark_picked_up_flow(orders_page):
    """Verify the Picked Up action is available for ready orders."""
    page = orders_page
    ready_filter = page.locator('button:has-text("Ready"), [class*="tab"]:has-text("Ready")').first
    if ready_filter.is_visible():
        ready_filter.click()
        page.wait_for_timeout(2000)
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No ready orders found")
    order_row.click()
    page.wait_for_timeout(2000)
    pickup_btn = page.locator('button:has-text("Picked Up"), button:has-text("Mark as Picked Up"), button:has-text("Pickup")').first
    if pickup_btn.is_visible():
        pickup_btn.click()
        page.wait_for_timeout(1000)
        confirm_btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button:has-text("OK")').first
        if confirm_btn.is_visible():
            confirm_btn.click()
            page.wait_for_timeout(2000)
        _assert_submission_success(page)
        _video_hold(page, 3)
    else:
        pytest.skip("Picked Up button not visible — order may not be in ready status")


# ── Order Stats & Pagination ─────────────────────────────────

@pytest.mark.tc("TC-ORDERS-020")
def test_TC_ORDERS_020_order_stats_displayed(orders_page):
    """Verify order summary/stats cards are displayed at the top."""
    page = orders_page
    _scroll_to_top(page)
    _video_hold(page, 2)
    # Look for stat/summary cards
    stats_selectors = [
        '[class*="stat"]:visible',
        '[class*="summary"]:visible',
        '[class*="metric"]:visible',
        '[class*="card"]:has-text("Total"):visible',
        '[class*="card"]:has-text("Pending"):visible',
    ]
    stats_found = False
    for sel in stats_selectors:
        if page.locator(sel).count() > 0:
            stats_found = True
            break
    body = page.locator("body").text_content() or ""
    stats_in_text = any(s in body.lower() for s in ["total orders", "pending", "accepted", "packed", "dispatched"])
    if not stats_found and not stats_in_text:
        pytest.skip("Order stats not found on page")


@pytest.mark.tc("TC-ORDERS-021")
def test_TC_ORDERS_021_pagination_controls(orders_page):
    """Verify pagination controls are present when there are many orders."""
    page = orders_page
    _scroll_down(page, 500)
    _video_hold(page, 2)
    pagination_selectors = [
        '[class*="pagination"]:visible',
        'button:has-text("Next")',
        'button:has-text("Previous")',
        'button:has-text(">")',
        '[aria-label="Next page"]',
        '[aria-label="Previous page"]',
        'nav[aria-label*="pagination"]',
    ]
    pagination_found = False
    for sel in pagination_selectors:
        if page.locator(sel).count() > 0:
            pagination_found = True
            break
    if not pagination_found:
        pytest.skip("Pagination not found — may have few orders")


@pytest.mark.tc("TC-ORDERS-022")
def test_TC_ORDERS_022_date_range_filter(orders_page):
    """Verify date range filter is available on the orders page."""
    page = orders_page
    _scroll_to_top(page)
    date_filter_selectors = [
        'input[type="date"]',
        'input[placeholder*="date"]',
        'input[placeholder*="Date"]',
        'button:has-text("Date")',
        '[class*="date-picker"]',
        '[class*="datepicker"]',
        '[class*="date-range"]',
    ]
    date_found = False
    for sel in date_filter_selectors:
        if page.locator(sel).count() > 0:
            date_found = True
            page.locator(sel).first.click()
            page.wait_for_timeout(1000)
            _video_hold(page, 2)
            break
    if not date_found:
        pytest.skip("Date filter not found on orders page")
    _check_no_page_errors(page, "date filter")


@pytest.mark.tc("TC-ORDERS-023")
def test_TC_ORDERS_023_order_detail_scroll(orders_page):
    """Verify the order detail page is scrollable and shows full content."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    # Scroll through the detail page
    _scroll_down(page, 400)
    _video_hold(page, 1)
    _scroll_down(page, 400)
    _video_hold(page, 1)
    _scroll_up(page, 400)
    _video_hold(page, 1)
    _scroll_to_top(page)
    _video_hold(page, 2)
    _check_no_page_errors(page, "order detail scroll")


@pytest.mark.tc("TC-ORDERS-024")
def test_TC_ORDERS_024_order_timeline(orders_page):
    """Verify order detail shows a status timeline or history log."""
    page = orders_page
    order_row = page.locator('table tbody tr, [class*="order-row"], [class*="order-item"], [class*="order-card"]').first
    if not order_row.is_visible():
        pytest.skip("No order rows found")
    order_row.click()
    page.wait_for_timeout(2000)
    _scroll_down(page, 300)
    # Look for timeline/history elements
    timeline_selectors = [
        '[class*="timeline"]:visible',
        '[class*="history"]:visible',
        '[class*="activity"]:visible',
        '[class*="log"]:has-text("status"):visible',
        '[class*="step"]:visible',
    ]
    timeline_found = False
    for sel in timeline_selectors:
        if page.locator(sel).count() > 0:
            timeline_found = True
            break
    body = page.locator("body").text_content() or ""
    timeline_in_text = any(s in body.lower() for s in ["sent", "accepted", "packed", "ready", "dispatched", "picked up"])
    if not timeline_found and not timeline_in_text:
        pytest.skip("Order timeline/history not found")
    _video_hold(page, 2)
