"""Fixture integrity only; never counted as agent authoring evidence."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from disposable_operations import operations_app


def test_transfer_fixture_validates_delays_persists_and_isolates_contexts():
    with operations_app() as url, sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)
        page.get_by_role('link', name='New transfer').click()
        page.get_by_role('button', name='Save transfer').click()
        expect(page.get_by_role('alert')).to_have_text('Transfer name is required')
        expect(page.get_by_label('Destination')).to_be_disabled()
        page.get_by_label('Transfer name').fill('Fixture test')
        page.get_by_label('Region').select_option('North')
        page.get_by_label('Destination').select_option('Delhi')
        page.get_by_label('Units').fill('0')
        page.get_by_role('button', name='Save transfer').click()
        expect(page.get_by_role('alert')).to_have_text('Units must be greater than zero')
        page.get_by_label('Units').fill('5')
        page.get_by_role('button', name='Choose service').click()
        page.get_by_role('button', name='Express', exact=True).click()
        page.get_by_role('button', name='Save transfer').click()
        expect(page.get_by_role('alert')).to_have_text('Saving transfer…')
        expect(page.get_by_role('heading', name='Transfer details')).to_be_visible()
        page.reload()
        expect(page.locator('main')).to_contain_text('Service: Express')
        other = browser.new_context().new_page()
        other.goto(url)
        expect(other.locator('main')).to_contain_text('No transfers found')
        browser.close()
