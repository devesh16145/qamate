"""Sign In flow — Playwright E2E tests."""

import pytest
from playwright.sync_api import expect, Page


@pytest.mark.tc("TC-SIGNIN-001")
def test_TC_SIGNIN_001_page_loads(page: Page, base_url):
    """Verify Sign In page loads with email and password fields."""
    page.goto(base_url + "login", wait_until="domcontentloaded")

    email_field = page.locator('input[type="email"], input[placeholder*="Email"]').first
    password_field = page.locator('input[type="password"]').first

    expect(email_field).to_be_visible(timeout=15000)
    expect(password_field).to_be_visible(timeout=5000)


@pytest.mark.tc("TC-SIGNIN-002")
def test_TC_SIGNIN_002_button_disabled(page: Page, base_url):
    """Verify Sign In button is disabled when fields are empty."""
    page.goto(base_url + "login", wait_until="domcontentloaded")

    sign_in_button = page.locator('button:has-text("Sign In"), button[type="submit"]').first
    # Wait for the button to be present in DOM first
    sign_in_button.wait_for(state="visible", timeout=15000)
    expect(sign_in_button).to_be_disabled()
