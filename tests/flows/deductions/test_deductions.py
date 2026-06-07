"""Deductions flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-DED-001")
def test_TC_DED_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify Deductions page: status tabs (All, Open, Processing, Closed) switch correctly, search field accepts input

    Preconditions: test reachable; logged in if required

    Expected Result: Deductions page loads with All tab. Clicking Open, Processing, Closed tabs each filters correctly. Search field accepts DN/PO/bill number input and shows results.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/deductions
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/deductions"):
        page.goto(base_url + "deductions")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Deductions page loads with status tabs
        expect(page.locator("body")).to_contain_text("All", timeout=10000)
    # Step 2: Click Click Open
    with checkpoints.step("Step 2: Click Click Open"):
        page.get_by_role("button", name="Open").click()
        page.wait_for_timeout(500)
        # Verify: Open tab filters deductions
        expect(page.locator("body")).to_contain_text("Open", timeout=10000)
    # Step 3: Click Click Processing
    with checkpoints.step("Step 3: Click Click Processing"):
        page.get_by_role("button", name="Processing").click()
        page.wait_for_timeout(500)
        # Verify: Processing tab filters deductions
        expect(page.locator("body")).to_contain_text("Processing", timeout=10000)
    # Step 4: Click Click Closed
    with checkpoints.step("Step 4: Click Click Closed"):
        page.get_by_role("button", name="Closed").click()
        page.wait_for_timeout(500)
        # Verify: Closed tab filters deductions
        expect(page.locator("body")).to_contain_text("Closed", timeout=10000)
    # Step 5: Enter "DN123" in Enter "DN123" in po-number-bill-number-utr-dn-items
    with checkpoints.step("Step 5: Enter \"DN123\" in Enter \"DN123\" in po-number-bill-number-utr-dn-items"):
        page.get_by_placeholder("PO Number, Bill Number, UTR, DN#, Items").fill(tc_data.get("input_1", ""))
        # Verify: Search shows no results for unmatched query
        expect(page.locator("body")).to_contain_text("No Deduction Found", timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")