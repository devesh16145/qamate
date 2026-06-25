"""Demo flow — public Playwright documentation site (no login required).

Shipped with QAmate as a minimal example. Uses absolute URLs so it runs
without platform credentials in config.json.
"""

import re

import pytest
from playwright.sync_api import expect, Page

DOCS_HOME = "https://playwright.dev/"
DOCS_INTRO = "https://playwright.dev/docs/intro"


@pytest.mark.tc("TC-DEMO-001")
def test_TC_DEMO_001_homepage_loads(page: Page, checkpoints):
    """Verify playwright.dev homepage loads with Get started link."""
    checkpoints.run(
        "Navigate to Playwright docs",
        lambda: page.goto(DOCS_HOME, wait_until="domcontentloaded"),
    )
    checkpoints.run(
        "Verify Get started link visible",
        lambda: expect(page.get_by_role("link", name="Get started").first).to_be_visible(timeout=15000),
    )


@pytest.mark.tc("TC-DEMO-002")
def test_TC_DEMO_002_get_started_navigation(page: Page, checkpoints):
    """Click Get started and verify navigation to docs."""
    checkpoints.run(
        "Open Playwright docs homepage",
        lambda: page.goto(DOCS_HOME, wait_until="domcontentloaded"),
    )
    checkpoints.run(
        "Click Get started",
        lambda: page.get_by_role("link", name="Get started").first.click(),
    )
    checkpoints.run(
        "Verify URL navigates to the docs section",
        lambda: expect(page).to_have_url(re.compile(r".*playwright\.dev/docs/.*"), timeout=15000),
    )


@pytest.mark.tc("TC-DEMO-003")
def test_TC_DEMO_003_docs_intro_search(page: Page, checkpoints):
    """Docs intro page shows heading and search."""
    checkpoints.run(
        "Navigate to Playwright docs intro",
        lambda: page.goto(DOCS_INTRO, wait_until="domcontentloaded"),
    )
    checkpoints.run(
        "Verify page heading is visible",
        lambda: expect(page.get_by_role("heading", level=1).first).to_be_visible(timeout=15000),
    )
    checkpoints.run(
        "Verify search input is present",
        lambda: expect(page.locator('input[type="search"], [role="searchbox"]').first).to_be_visible(timeout=10000),
    )
