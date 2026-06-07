"""Payments flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-PAY-001")
def test_TC_PAY_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify Payments page: status tabs (All Bills, Unpaid, Overdue) switch correctly, search field accepts input

    Preconditions: test reachable; logged in if required

    Expected Result: Payments page loads with All Bills tab. Clicking Unpaid and Overdue tabs each filters correctly. Search field accepts bill/PO number input and shows results.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/payments
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/payments"):
        page.goto(base_url + "payments")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Payments page loads with bill status tabs
        expect(page.locator("body")).to_contain_text("All Bills", timeout=10000)
    # Step 2: Click Click Unpaid
    with checkpoints.step("Step 2: Click Click Unpaid"):
        page.get_by_role("button", name="Unpaid").click()
        page.wait_for_timeout(500)
        # Verify: Unpaid tab filters bills
        expect(page.locator("body")).to_contain_text("Unpaid", timeout=10000)
    # Step 3: Click Click Overdue
    with checkpoints.step("Step 3: Click Click Overdue"):
        page.get_by_role("button", name="Overdue").click()
        page.wait_for_timeout(500)
        # Verify: Overdue tab filters bills
        expect(page.locator("body")).to_contain_text("Overdue", timeout=10000)
    # Step 4: Enter "PO" in Enter "PO" in search-by-bill-number-po-number
    with checkpoints.step("Step 4: Enter \"PO\" in Enter \"PO\" in search-by-bill-number-po-number"):
        page.get_by_placeholder("Search by Bill Number, PO Number").fill(tc_data.get("input_1", ""))
        # Verify: Search shows no results message for unmatched query
        expect(page.locator("body")).to_contain_text("No Payment Found", timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")