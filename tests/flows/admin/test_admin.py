"""Admin Panel flow — Checkpoint-based Playwright E2E tests.

Tests target the Agrim Admin Panel (separate URL + separate login from the
Seller App). Each test uses the `admin_page` fixture, which:
  - creates its own browser context (can coexist with seller_page in the
    same test if a TC needs cross-platform coverage)
  - applies the admin storage_state (one login per session)
  - returns a Page already pointing at the admin panel URL

URL + user are picked from config.json `platforms.admin` (default: admin-dev.agrim.app,
amit.dalal@agrim.app). The admin uses hash routing (`/#/login`), so all
in-app navigation goes through `admin_url + "#/<route>"`.

Test cases are authored from analyzer captures of the admin panel — see
ATS_TEST_PATTERNS.md for the patterns used across the seller app and
admin panel.
"""

import re
import os
import pytest
from playwright.sync_api import expect, Page


# ── Helpers ──────────────────────────────────────────────────

def _admin_goto(page, admin_url, route):
    """Navigate within the admin panel using its hash-based routing.

    `route` is the part after `#/` (e.g. "purchase-orders", "vendors/new").
    Leading slashes / hash prefixes are stripped so callers can be sloppy.
    """
    route = route.lstrip("#").lstrip("/")
    base = admin_url if admin_url.endswith("/") else admin_url + "/"
    page.goto(base + "#/" + route, wait_until="domcontentloaded")
    # Admin panel (React SPA + hash routing) needs a settling beat
    page.wait_for_timeout(800)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


def _scroll_down(page, pixels=500):
    """SPA-safe scroll — see ATS_TEST_PATTERNS.md §1."""
    page.mouse.wheel(0, pixels)
    page.wait_for_timeout(400)


def _scroll_to_top(page):
    page.keyboard.press("Home")
    page.wait_for_timeout(300)


def _check_no_admin_errors(page, context_label=""):
    """Assert no error toast / alert is visible on the admin panel."""
    page.wait_for_timeout(400)
    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
        '[class*="toast"]:visible:has-text("failed")',
        '[class*="text-destructive"]:visible',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            text = errors.first.text_content().strip()[:200]
            if text:
                raise AssertionError(
                    f"Admin error detected{f' ({context_label})' if context_label else ''}: {text}"
                )


def _wait_for_admin_ready(page, timeout=15000):
    """Wait for any signal that the admin SPA has mounted past the splash/
    initial loader. The admin uses Ant Design-style top nav — looking for
    typical sidebar/menu landmarks is reliable across routes."""
    candidates = [
        '[class*="ant-layout-sider"]',
        '[class*="MuiDrawer"]',
        'nav, [role="navigation"]',
        '[class*="sidebar"]',
        'aside',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout // len(candidates))
            return
        except Exception:
            continue
    # Last-resort: any visible button (means SPA mounted something)
    page.locator('button:visible').first.wait_for(state="visible", timeout=5000)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def admin_home(admin_page: Page, admin_url):
    """Land on the admin panel's home/dashboard route after login state
    has been applied. Most TCs should start here."""
    admin_page.goto(admin_url, wait_until="domcontentloaded")
    _wait_for_admin_ready(admin_page)
    admin_page.wait_for_timeout(800)
    return admin_page


# ── Test cases ───────────────────────────────────────────────
#
# Test cases will be added after the UI walkthrough. Each TC follows the
# checkpoint pattern (see catalog flow for reference):
#
# @pytest.mark.tc("TC-ADMIN-001")
# def test_TC_ADMIN_001_<short_name>(admin_home: Page, admin_url, tc_data, checkpoints):
#     """One-line summary of the scenario."""
#     page = admin_home
#
#     def cp_<step>():
#         ...assertions...
#
#     checkpoints.run("Step description", cp_<step>)
