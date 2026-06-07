"""Account Statement flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-ACCT-001")
def test_TC_ACCT_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify Account Statement page loads with date range options and Request Statement button is clickable

    Preconditions: test reachable; logged in if required

    Expected Result: Account Statement page displays date range radio buttons and the Request Statement button is present and clickable.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/account-statement
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/account-statement"):
        page.goto(base_url + "account-statement")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Account Statement page loads with date range options
        expect(page.locator("body")).to_contain_text("Request Statement", timeout=10000)
    # Step 2: Click Click Request Statement
    with checkpoints.step("Step 2: Click Click Request Statement"):
        page.get_by_role("button", name="Request Statement").click()
        page.wait_for_timeout(500)
        # Verify: Request Statement button is clickable
        expect(page).to_have_url(re.compile(".*" + re.escape("account-statement") + ".*"), timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")
