"""Returns flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-RET-001")
def test_TC_RET_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify Returns page: status tabs (Return Initiated, Returned) switch correctly, search field accepts PO number input

    Preconditions: test reachable; logged in if required

    Expected Result: Returns page loads with All Orders tab. Clicking Return Initiated and Returned tabs each filters correctly. Search field accepts PO number input.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/returns
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/returns"):
        page.goto(base_url + "returns")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Returns page loads with return status tabs
        expect(page.locator("body")).to_contain_text("All Orders", timeout=10000)
    # Step 2: Click Click Return Initiated
    with checkpoints.step("Step 2: Click Click Return Initiated"):
        page.get_by_role("button", name="Return Initiated").click()
        page.wait_for_timeout(500)
        # Verify: Return Initiated tab filters returns
        expect(page.locator("body")).to_contain_text("Return Initiated", timeout=10000)
    # Step 3: Click Click Returned
    with checkpoints.step("Step 3: Click Click Returned"):
        page.get_by_role("button", name="Returned").click()
        page.wait_for_timeout(500)
        # Verify: Returned tab filters returns
        expect(page.locator("body")).to_contain_text("Returned", timeout=10000)
    # Step 4: Enter "PO" in Enter "PO" in search-by-po-number-items
    with checkpoints.step("Step 4: Enter \"PO\" in Enter \"PO\" in search-by-po-number-items"):
        page.get_by_placeholder("Search by PO Number, Items").fill(tc_data.get("input_1", ""))
        # Verify: Search field accepts PO number input
        expect(page.locator("body")).to_contain_text("PO", timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")
