"""Disposable browser-local demo reconnaissance, NOT agent benchmark evidence."""
from playwright.sync_api import sync_playwright
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route("**/*", lambda r: r.continue_() if r.request.method in {"GET", "HEAD", "OPTIONS"} else r.abort())
        page.goto("https://marmelab.com/atomic-crm-demo/")
        page.get_by_role("link", name="Companies", exact=True).click()
        print(page.locator("body").inner_text())
        page.get_by_text("New Company", exact=True).click()
        page.locator("input").first.wait_for()
        print(page.locator("body").inner_text())
        print(page.locator("input,textarea,select,button").evaluate_all("""els => els.map(e => ({
            tag:e.tagName,role:e.getAttribute('role'),id:e.id,name:e.getAttribute('name'),
            type:e.type,text:e.innerText,placeholder:e.getAttribute('placeholder')}))"""))
        if '--bridge' in sys.argv:
            from agent_chat import BrowserSession
            from decision_browser import BrowseContract, candidates
            from decision_browser_tools import BrowserBridge
            session = BrowserSession()
            session.browser, session.context, session.page = browser, page.context, page
            contract = BrowseContract.model_validate({'start_url': 'https://marmelab.com/atomic-crm-demo/',
                'goal': 'Fill name', 'inputs': [{'name': 'company_name', 'value': 'QAMATE Lifecycle 9f58c327'}],
                'milestones': [{'goal': 'Name filled', 'outcomes': [{'name': 'Name', 'kind': 'element_has_value',
                    'input_slot': 'company_name', 'value': 'QAMATE Lifecycle 9f58c327'}]}]})
            bridge = BrowserBridge(session, contract, 'session_local_forms')
            bridge.probe(contract.milestones[0])
            snapshot = bridge.snapshot()
            choices = candidates(snapshot, contract, bridge.policy, contract.milestones[0])
            action = next(c for c in choices.values() if c.kind == 'fill' and c.label.startswith('Company name'))
            print('MODEL', session.by_ref[action.ref])
            print('RECORD STRATEGY', session._record_strategy(session.by_ref[action.ref]))
            print('NODE TABLE', page.locator('input').evaluate_all("els => els.map(e => [e.name, window.__qamateNodes.document, window.__qamateNodes.ids.get(e)])"))
            print('ACT', bridge.act(action, contract.inputs[0].value))
            print('CHECK', bridge.check(contract.milestones[0]))
            browser.close()
            return
        page.locator('input[name="name"]').fill('QAMATE Lifecycle 9f58c327')
        page.locator('input[name="website"]').fill('https://example.com')
        page.locator('input[name="city"]').fill('Qamate Test City')
        page.locator('textarea[name="description"]').fill('Qamate original description')
        page.get_by_role('combobox').nth(0).click()
        page.get_by_role('option', name='Industrials', exact=True).click()
        page.get_by_role('combobox').nth(1).click()
        page.get_by_role('option', name='10-49 employees', exact=True).click()
        page.get_by_role('button', name='Create Company', exact=True).click()
        page.wait_for_url('**/#/companies/*/show')
        page.get_by_text('QAMATE Lifecycle 9f58c327', exact=True).wait_for()
        print('DETAIL URL:', page.url)
        print(page.locator('body').inner_text())
        print(page.get_by_role('link').all_text_contents())
        print(page.get_by_role('button').all_text_contents())
        print('SIZE MARKUP', page.get_by_text('Size:', exact=True).locator('..').inner_html())
        page.get_by_role('link', name='Edit company', exact=True).click()
        page.locator('input[name="name"]').wait_for()
        print('EDIT COMBOS', page.get_by_role('combobox').all_text_contents())
        print('EDIT BUTTONS', page.get_by_role('button').all_text_contents())
        page.get_by_role('button', name='Cancel', exact=True).click()
        page.get_by_role('tab', name='0 contacts', exact=True).click()
        print('CONTACTS TAB', page.locator('body').inner_text())
        page.get_by_role('link', name='Add contact', exact=True).click()
        page.locator('input').first.wait_for()
        print('CONTACT FORM', page.locator('body').inner_text())
        print('CONTACT INPUTS', page.locator('input').evaluate_all("els => els.map(e => ({name:e.name,id:e.id,placeholder:e.placeholder,labels:e.labels?.[0]?.textContent}))"))
        browser.close()


if __name__ == "__main__":
    main()
