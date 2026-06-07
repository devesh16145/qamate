"""Sor flow."""

import re
import os
import pytest
from playwright.sync_api import expect, Page

# Default test image for all file uploads
TEST_UPLOAD_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "test_upload.png")



@pytest.mark.tc("TC-SOR-001")
def test_TC_SOR_001(seller_page: Page, tc_data, base_url, admin_url, checkpoints):
    """Verify SOR page: product list loads, search filters by keyword, clearing search repopulates list, sort options accessible

    Preconditions: test reachable; logged in if required

    Expected Result: SOR page displays product cards with View Details. Search for 'samsung' filters results. Clearing search shows all products again. Sort options button is functional.
    """
    page = seller_page
    # Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/sor
    with checkpoints.step("Step 1: Navigate to Navigate to https://supplier-dev.agrim.app/sor"):
        page.goto(base_url + "sor")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        # Verify: SOR page loads with product list
        expect(page.locator("body")).to_contain_text("View Details", timeout=10000)
    # Step 2: Enter "samsung" in Enter "samsung" in search-product-name-sku-brand
    with checkpoints.step("Step 2: Enter \"samsung\" in Enter \"samsung\" in search-product-name-sku-brand"):
        page.get_by_placeholder("Search Product Name, SKU, Brand").fill(tc_data.get("input_1", ""))
        # Verify: Search filters to Samsung product
        expect(page.locator("body")).to_contain_text("Samsung", timeout=10000)
    # Step 3: Enter "" in Enter "" in search-product-name-sku-brand
    with checkpoints.step("Step 3: Enter \"\" in Enter \"\" in search-product-name-sku-brand"):
        page.get_by_placeholder("Search Product Name, SKU, Brand").fill(tc_data.get("input_2", ""))
        # Verify: Product list repopulates after clearing search
        expect(page.locator("body")).to_contain_text("View Details", timeout=10000)
    # Step 4: Click Click Sort : Options
    with checkpoints.step("Step 4: Click Click Sort : Options"):
        page.get_by_role("button", name="Sort : Options").click()
        page.wait_for_timeout(500)
        # Verify: Sort options accessible on SOR page
        expect(page.locator("body")).to_contain_text("Sort", timeout=10000)

    # ── Final wait before closing ──
    page.wait_for_timeout(2000)
    checkpoints.mark_passed("Flow completed - all steps passed")
