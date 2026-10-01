"""Versioned public-demo task contracts; no private accounts or checkout."""
import hashlib
import json

VERSION = 1
TASKS = {
    "ops-transfer": {
        "contract_version": 2,
        "id": "PILOT-OPS-001", "tc_id": "TC-OPS-001", "flow_id": "bench_ops_transfer",
        "app": "disposable-operations", "difficulty": "dependent-fields-modal-async", "target_label": "Dispatch Desk",
        "base_url": "http://disposable-operations.invalid/", "disposable_operations": True, "session_local_forms": True,
        "prompt": "Use only the disposable Dispatch Desk URL in project settings. Create transfer QA Dispatch Cedar with Region North, Destination Jaipur, Units 12, Notes Cedar delivery, and service Express chosen through the service dialog. Verify exact name, destination, units and notes input values before saving. Verify saved Transfer details shows QA Dispatch Cedar, Jaipur, Units: 12, Service: Express, Cedar delivery and Status: Scheduled. Search transfers for QA Dispatch Cedar, verify search input and matching record; reopen it and verify the same saved detail values. Preserve all actions and checkpoints in TC-OPS-001, flow bench_ops_transfer, then run_test_case. Do not checkout, purchase, modify unrelated records or weaken outcomes. No external systems. Stop on terminal model errors."},
    "ops-validation": {
        "id": "PILOT-OPS-002", "tc_id": "TC-OPS-002", "flow_id": "bench_ops_validation",
        "app": "disposable-operations", "difficulty": "validation-dependent-fields", "target_label": "Dispatch Desk",
        "base_url": "http://disposable-operations.invalid/", "disposable_operations": True, "session_local_forms": True,
        "prompt": "Use only the disposable Dispatch Desk URL in settings. On a new transfer, attempt saving with every field empty and verify Transfer name is required. Supply transfer name QA Validation Pine, Region South and Destination Kochi, Units 0, Notes Pine validation. Attempt saving and verify Units must be greater than zero while the form retains QA Validation Pine. Correct Units to 7; verify exact name, destination and units input values before saving. Verify saved Transfer details contains QA Validation Pine, Kochi, Units: 7, Pine validation, Status: Scheduled, and lacks Units must be greater than zero. Return to Transfers and reopen this same record; verify the same saved values again. Preserve the intentional validation failures and later successful save in TC-OPS-002, flow bench_ops_validation; run_test_case. Do not checkout, purchase or modify unrelated records. Stop on terminal model errors."},
    "ops-allocation": {
        "id": "PILOT-OPS-003", "tc_id": "TC-OPS-003", "flow_id": "bench_ops_allocation",
        "app": "disposable-operations", "difficulty": "pagination-modal-dependent-fields", "target_label": "Dispatch Desk", "held_out": True,
        "base_url": "http://disposable-operations.invalid/", "disposable_operations": True, "session_local_forms": True,
        "prompt": "Use only the disposable Dispatch Desk URL in settings. Find Batch 024 through Inventory pagination; assert Page 4 of 4 and Batch 024 on the list. Open that exact batch and assert Fertilizer, Warehouse South, Available units: 24 and No allocation. Allocate quantity 3 to customer CityGrow with tier Priority through the allocation dialog; verify exact customer, tier and quantity input values before confirmation. Verify persisted batch detail shows Batch 024 and Allocated 3 to CityGrow (Priority), with No allocation absent. Return to Inventory and reopen Batch 024, verifying that same allocation and absence again. Preserve all navigation and checkpoints in TC-OPS-003, flow bench_ops_allocation, then run_test_case. Do not checkout, purchase, allocate other batches or weaken assertions. Stop on terminal model errors."},
    "sort-detail": {
        "contract_version": 2,
        "id": "PILOT-SAUCE-004", "tc_id": "TC-PILOT-004", "flow_id": "bench_sort_detail",
        "app": "public-demo", "difficulty": "auth-select-detail-roundtrip", "session_local_forms": True, "held_out": True,
        "base_url": "https://www.saucedemo.com/", "prompt": "Use only the public SauceDemo QA app. Log in with public account standard_user and password secret_sauce. Set product sort to Name (Z to A), and verify the exact selected value za on the inventory page. Open Test.allTheThings() T-Shirt (Red) and assert its detail name, price $15.99 and the description containing Super-soft and comfy ringspun combed cotton. Return to Products and verify inventory URL and Products; open Sauce Labs Bike Light and assert its detail name and price $9.99. Preserve login, selection, both detail visits and checkpoints in TC-PILOT-004, flow bench_sort_detail, then run_test_case. Do not checkout, purchase or add products to cart. Stop on terminal model errors."},
    "crm-related-contact": {
        "id": "PILOT-CRM-003", "tc_id": "TC-CRM-003", "flow_id": "bench_crm_related_contact",
        "app": "public-crm-demo", "difficulty": "complex-related-records", "target_label": "Atomic CRM demo",
        "base_url": "https://marmelab.com/atomic-crm-demo/", "session_local_forms": True, "contract_version": 3,
        "prompt": (
            "Use only https://marmelab.com/atomic-crm-demo/, its isolated browser-local FakeRest demo. "
            "Create one synthetic company, edit it, and add one related contact in this disposable session. "
            "No network writes, private login, import/export, deletion, uploads, messages, or external navigation. "
            "Company: name QAMATE Relationship 9f58c327, website https://example.com, city Qamate Test City, "
            "description Qamate original description, sector Industrials. Assert exact name, website, city and "
            "description input values before creation. Then assert the saved company's DETAIL page shows the "
            "name, original city, original description and sector; a toast or list heading is insufficient. "
            "Edit the same company: city Qamate Revised City and description Qamate revised description. "
            "Save and assert the detail page shows name, revised city, revised description, sector and lacks "
            "the old city and description. Return to Companies, search by the exact company name, assert the "
            "search value and matching visible record, reopen it and assert the edited detail state again: "
            "name, revised city, revised description and sector present; original city and original description absent. "
            "Next create a contact RELATED TO THIS SAME COMPANY: first name Qamate, last name RelationTest, "
            "email qamate.relation@example.com, title QA Engineer. Before saving the contact, verify exact first name, last name, email, title and selected company values on its form. On the saved contact detail page assert "
            "the full name Qamate RelationTest, exact email, title and company name. Follow the company "
            "relationship back to its detail page, open its Contacts tab and assert Qamate RelationTest "
            "appears under this exact company, together with QA Engineer. Preserve the whole flow and every "
            "required checkpoint in TC-CRM-003, flow bench_crm_related_contact, then run_test_case. "
            "Do not reload (demo data resets); do not invent random existing records as substitutes. "
            "Do not checkout, purchase, or submit personal data. Do not weaken checks, create other tests, "
            "or retry terminal model errors.")},
    "crm-lifecycle": {
        "id": "PILOT-CRM-002", "tc_id": "TC-CRM-002", "flow_id": "bench_crm_lifecycle",
        "app": "public-crm-demo", "difficulty": "complex-business-ui", "target_label": "Atomic CRM demo", "contract_version": 3,
        "base_url": "https://marmelab.com/atomic-crm-demo/", "session_local_forms": True,
        "prompt": (
            "Use only https://marmelab.com/atomic-crm-demo/, an isolated browser-local FakeRest demo. "
            "Create and edit ONE synthetic company in this disposable session. No network writes, "
            "private login, import/export, deletion, uploads, external navigation, or messages are authorized. "
            "Create company QAMATE Lifecycle 9f58c327 with website https://example.com, "
            "city Qamate Test City, description Qamate original description, sector Industrials, and "
            "size 10-49 employees. Before submitting, assert the exact company name, website, city and "
            "description input values. After creation, verify the actual company detail page shows the "
            "exact company name, city, description, sector and size; a toast or list heading is insufficient. "
            "Edit that same company: change city to Qamate Revised City and description to Qamate revised "
            "description, preserving its name, sector and size. Save and assert its detail page shows all "
            "five correct values and no longer shows the old city or old description. "
            "Return to the Companies list, search by the exact company name, assert that search value "
            "and that the matching record is visible, reopen that exact record and assert the same edited "
            "detail state again. Preserve the whole creation/edit/search/reopen flow and assertions in "
            "TC-CRM-002, flow bench_crm_lifecycle, then run_test_case. Do not use a page reload: the "
            "demo resets data on reload. Compile outcome milestones, not click instructions. "
            "Do not checkout, purchase, or submit personal data. "
            "Do not weaken the required checks, create other tests or retry terminal model errors.")},
    "crm-search": {
        "id": "PILOT-CRM-001", "tc_id": "TC-CRM-001", "flow_id": "bench_crm_search",
        "app": "public-crm-demo", "difficulty": "business-ui", "target_label": "Atomic CRM demo",
        "base_url": "https://marmelab.com/atomic-crm-demo/", "prompt": (
            "Use only https://marmelab.com/atomic-crm-demo/, the public Atomic CRM demo. "
            "This is a read-only UI search test, not permission to create, edit or delete business records. "
            "Explore the Companies view and identify its search control from the actual page. "
            "Search for the supplied unique string QAMATE-NOMATCH-9f58c327. Assert the search input retains "
            "that exact value and that the companies result list is empty, using the actual visible empty-state "
            "message as an observed-behavior checkpoint. Also assert the Companies page identity so a wrong "
            "page cannot satisfy this check. Clear the search and assert that the company list is populated again. "
            "Demo record names can vary between fresh sessions: do not hard-code a randomly generated company "
            "name or count as an expected value. Preserve navigation, search, assertions and clearing in "
            "TC-CRM-001, flow bench_crm_search, then run_test_case. Do not checkout, purchase, submit personal "
            "data, authenticate with private accounts, import/export data, send messages, or create other tests. "
            "If login or an unsupported control blocks the task, report it rather than inventing credentials. "
            "Stop on terminal model errors.")},
    "smoke": {
        "id": "PILOT-SAUCE-001", "tc_id": "TC-PILOT-001", "flow_id": "bench_public_pilot",
        "app": "public-demo", "difficulty": "smoke", "prompt": (
            "Use only https://www.saucedemo.com, the public QA demo. Log in using its public demo account "
            "standard_user and password secret_sauce. Sort products by Price (low to high), and use "
            "add_checkpoint with element_has_value, the sort ref, and value lohi to verify that selection. "
            "Add Sauce Labs Onesie to cart, open cart and assert that exact product is present and URL contains /cart.html. "
            "Do not checkout, purchase, or submit personal data. Create test TC-PILOT-001 in flow "
            "bench_public_pilot with outcome checkpoints, then run_test_case. Preserve login in the test. "
            "Do not broaden the task or create any other tests. Stop on terminal model errors.")},
    "cart-edit": {
        "id": "PILOT-SAUCE-002", "tc_id": "TC-PILOT-002", "flow_id": "bench_cart_edit",
        "app": "public-demo", "difficulty": "medium", "prompt": (
            "Use only https://www.saucedemo.com, the public QA demo. Log in with its public account "
            "standard_user and password secret_sauce. Open Sauce Labs Backpack details and verify the "
            "product name. Add it to cart, return to inventory, add Sauce Labs Onesie, then open cart. "
            "Assert BOTH products are present before removal. Remove only Sauce Labs Backpack from the cart. "
            "Assert the Backpack text is absent and Sauce Labs Onesie is still present on the cart page. "
            "Continue shopping, return to cart, and assert the same final state again to verify persistence. "
            "Use page_not_contains_text for absence and page_contains_text for presence, paired with "
            "url_contains /cart.html so a wrong page cannot satisfy the absence check. "
            "Create test TC-PILOT-002 in flow bench_cart_edit with these outcome checkpoints, preserve all "
            "login and cart editing steps, then run_test_case. Do not checkout, purchase, submit personal "
            "data, or create other tests. Removing this demo cart item is explicitly authorized. "
            "Stop on terminal model errors.")},
    "negative-login": {
        "id": "PILOT-SAUCE-003", "tc_id": "TC-PILOT-003", "flow_id": "bench_negative_login",
        "app": "public-demo", "difficulty": "medium", "prompt": (
            "Use only https://www.saucedemo.com, the public QA demo. Start on the login page and click "
            "Login with both fields empty. Observe and assert the actual missing-username validation text. "
            "Then fill username standard_user only, click Login, and observe and assert the actual "
            "missing-password validation text. These two validation failures are intentional test steps, "
            "not a reason to skip the test. Finally fill the public demo password secret_sauce, click Login, "
            "and assert inventory URL /inventory.html and Products text. Preserve both negative submissions "
            "and the successful login in one test TC-PILOT-003 in flow bench_negative_login. Create the test "
            "with outcome checkpoints and run_test_case. Do not use invalid passwords, checkout, purchase, "
            "submit personal data, or create other tests. Stop on terminal model errors.")},
}

# Public demo login and cart changes are browser-local. Network writes remain blocked.
for _name in ('smoke', 'cart-edit', 'negative-login'):
    TASKS[_name]['session_local_forms'] = True
    TASKS[_name]['base_url'] = 'https://www.saucedemo.com/'


def get_task(name):
    task = dict(TASKS[name])
    task["contract_version"] = task.get("contract_version", VERSION)
    task["contract_sha256"] = hashlib.sha256(
        json.dumps(TASKS[name], sort_keys=True).encode("utf-8")).hexdigest()
    return task


def required_outcome_groups(name):
    """Immutable outcome coverage for the CRM benchmark, without browser mechanics."""
    def group(label, present=(), absent=(), values=(), route=None):
        return {'name': label, 'outcomes':
                [{'kind': 'page_contains_text', 'value': v} for v in present] +
                [{'kind': 'page_not_contains_text', 'value': v} for v in absent] +
                [{'kind': 'element_has_value', 'value': v} for v in values] +
                ([{'kind': 'url_contains', 'value': route}] if route else [])}
    if name == 'ops-transfer':
        saved = ['QA Dispatch Cedar', 'Jaipur', 'Units: 12', 'Service: Express', 'Cedar delivery', 'Status: Scheduled']
        return [group('form readback including selected service', present=['Service: Express'], values=['QA Dispatch Cedar', 'Jaipur', '12', 'Cedar delivery']),
                group('saved transfer detail', present=saved, route='#/transfers/1'),
                group('exact transfer search', present=['QA Dispatch Cedar'], values=['QA Dispatch Cedar'], route='#/transfers'),
                group('reopened transfer detail', present=saved, route='#/transfers/1')]
    if name == 'ops-validation':
        saved = ['QA Validation Pine', 'Kochi', 'Units: 7', 'Pine validation', 'Status: Scheduled']
        return [group('empty validation', present=['Transfer name is required'], route='#/transfers/new'),
                group('zero validation', present=['Units must be greater than zero'], values=['QA Validation Pine'], route='#/transfers/new'),
                group('corrected inputs', values=['QA Validation Pine', 'Kochi', '7']),
                group('saved corrected transfer', present=saved, absent=['Units must be greater than zero'], route='#/transfers/1'),
                group('reopened corrected transfer', present=saved, absent=['Units must be greater than zero'], route='#/transfers/1')]
    if name == 'ops-allocation':
        saved = ['Batch 024', 'Allocated 3 to CityGrow (Priority)']
        return [group('fourth inventory page', present=['Page 4 of 4', 'Batch 024'], route='#/inventory'),
                group('unallocated batch detail', present=['Batch 024', 'Fertilizer', 'Warehouse South', 'Available units: 24', 'No allocation'], route='#/inventory/24'),
                group('allocation inputs', values=['CityGrow', 'Priority', '3']),
                group('saved allocation', present=saved, absent=['No allocation'], route='#/inventory/24'),
                group('reopened allocation', present=saved, absent=['No allocation'], route='#/inventory/24')]
    if name == 'sort-detail':
        return [group('selected reverse name order', values=['za'], route='/inventory.html'),
                group('red shirt details', present=['Test.allTheThings() T-Shirt (Red)', '$15.99', 'Super-soft and comfy ringspun combed cotton'], route='/inventory-item.html'),
                group('returned inventory', present=['Products'], route='/inventory.html'),
                group('bike light details', present=['Sauce Labs Bike Light', '$9.99'], route='/inventory-item.html')]
    if name != 'crm-related-contact':
        return []
    company = 'QAMATE Relationship 9f58c327'
    revised = [company, 'Qamate Revised City', 'Qamate revised description', 'Industrials']
    old = ['Qamate Test City', 'Qamate original description']
    return [group('pre-create input readback', values=[company, 'https://example.com', *old]),
            group('created company detail', present=[company, *old, 'Industrials']),
            group('saved edited detail', present=revised, absent=old),
            group('exact company search', present=[company], values=[company]),
            group('reopened edited detail', present=revised, absent=old),
            group('contact form and relationship readback', values=['Qamate', 'RelationTest', 'qamate.relation@example.com', 'QA Engineer', company]),
            group('saved related contact detail', present=['Qamate RelationTest', 'qamate.relation@example.com', 'QA Engineer', company]),
            group('company contact relationship', present=[company, 'Qamate RelationTest', 'QA Engineer'])]
