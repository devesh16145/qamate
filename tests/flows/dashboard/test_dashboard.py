"""Dashboard flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-DASH-001")
def test_TC_DASH_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify Dashboard: all nav links (SOR, Orders, Payments, Returns, Deductions, Account Statement) navigate to correct pages

    Preconditions: test reachable; logged in if required

    Expected Result: Dashboard loads with main navigation. Clicking each nav link navigates to the corresponding page: /sor, /orders, /payments, /returns, /deductions, /account-statement.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/"):
        page.goto(base_url + "")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Dashboard loads with main navigation
        expect(page.locator("body")).to_contain_text("My Storefront", timeout=10000)
    # Step 2: Click Click SOR
    with checkpoints.step("Step 2: Click Click SOR"):
        page.get_by_role("link", name="SOR").click()
        page.wait_for_timeout(500)
        # Verify: SOR link navigates to SOR page
        expect(page).to_have_url(re.compile(".*" + re.escape("/sor") + ".*"), timeout=10000)
    # Step 3: Click Click Orders
    with checkpoints.step("Step 3: Click Click Orders"):
        page.get_by_role("link", name="Orders").click()
        page.wait_for_timeout(500)
        # Verify: Orders link navigates to Orders page
        expect(page).to_have_url(re.compile(".*" + re.escape("/orders") + ".*"), timeout=10000)
    # Step 4: Click Click Payments
    with checkpoints.step("Step 4: Click Click Payments"):
        page.get_by_role("link", name="Payments").click()
        page.wait_for_timeout(500)
        # Verify: Payments link navigates to Payments page
        expect(page).to_have_url(re.compile(".*" + re.escape("/payments") + ".*"), timeout=10000)
    # Step 5: Click Click Returns
    with checkpoints.step("Step 5: Click Click Returns"):
        page.get_by_role("link", name="Returns").click()
        page.wait_for_timeout(500)
        # Verify: Returns link navigates to Returns page
        expect(page).to_have_url(re.compile(".*" + re.escape("/returns") + ".*"), timeout=10000)
    # Step 6: Click Click Deductions
    with checkpoints.step("Step 6: Click Click Deductions"):
        page.get_by_role("link", name="Deductions").click()
        page.wait_for_timeout(500)
        # Verify: Deductions link navigates to Deductions page
        expect(page).to_have_url(re.compile(".*" + re.escape("/deductions") + ".*"), timeout=10000)
    # Step 7: Click Click Account Statement
    with checkpoints.step("Step 7: Click Click Account Statement"):
        page.get_by_role("link", name="Account Statement").click()
        page.wait_for_timeout(500)
        # Verify: Account Statement link navigates to Account Statement page
        expect(page).to_have_url(re.compile(".*" + re.escape("account-statement") + ".*"), timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")

@pytest.mark.tc("TC-DASHBOARD-001")
def test_TC_DASHBOARD_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify the Seller Dashboard landing page loads with navigation sidebar links (SOR, Orders, Payments, Returns, Deductions, Account Statement) and upload functionality

    Preconditions: test reachable; logged in if required

    Expected Result: Dashboard page displays with all nav links and upload buttons visible; My Storefront link navigates correctly
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/"):
        page.goto(base_url + "")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: Dashboard loaded with nav links
        expect(page.locator("body")).to_contain_text("My Storefront", timeout=10000)
        # Verify: Dashboard has SOR link
        expect(page.locator("body")).to_contain_text("SOR", timeout=10000)
        # Verify: Dashboard has upload functionality
        expect(page.locator("body")).to_contain_text("Upload", timeout=10000)
    # Step 2: Click Click My Storefront
    with checkpoints.step("Step 2: Click Click My Storefront"):
        page.get_by_role("link", name="My Storefront").click()
        page.wait_for_timeout(500)
        # Verify: Storefront link navigates correctly
        expect(page).to_have_url(re.compile(".*" + re.escape("supplier-dev.agrim.app") + ".*"), timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")
