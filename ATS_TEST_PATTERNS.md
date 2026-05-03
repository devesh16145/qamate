# ATS Test Patterns — Proven Playwright Patterns for Agrim Seller App

> "ATS success patterns" — Reference this file to replicate successful test patterns.
> Proven in TC-CATALOG-010 (Create New Product — 9 checkpoints, all passing).

---

## 1. Scrolling — ALWAYS use `page.mouse.wheel()`

The Agrim Seller App is a React SPA with custom scrollable containers (`div.custom-scrollbar`).
**`window.scrollBy()` and `element.scrollTop` DO NOT WORK.** Only mouse wheel triggers the React scroll handler.

```python
def _scroll_down(page, pixels=500):
    page.mouse.wheel(0, pixels)
    page.wait_for_timeout(500)

def _scroll_up(page, pixels=500):
    page.mouse.wheel(0, -pixels)
    page.wait_for_timeout(500)

def _scroll_to_top(page):
    page.keyboard.press("Home")
    page.wait_for_timeout(300)
```

**Scrolling to find elements** — loop with mouse wheel, check each iteration:
```python
for i in range(20):
    _scroll_down(page, 500)
    page.wait_for_timeout(600)
    if page.locator('button:has-text("Target Text")').count() > 0:
        print(f"  Found after {i+1} scrolls", flush=True)
        return True
```

---

## 2. Radix UI Combobox (Brand, Packing, In Stock)

These use `button[role="combobox"]` triggers. When clicked, a popover opens as a **second** `div[role="dialog"]`.

**Critical: Use `force=True`** — the modal dialog content div intercepts pointer events.

**Critical: Scope to the modal dialog** — `div[role="dialog"][data-state="open"]` to avoid matching comboboxes outside the modal.

**Critical: Options are `<span>` elements**, not `[role="option"]` or `<li>`.

```python
dialog = page.locator('div[role="dialog"][data-state="open"]').last

# Click the combobox
brand_btn = dialog.locator('button[role="combobox"]').first
brand_btn.click(force=True)
page.wait_for_timeout(800)

# Select first option from the popover
option = page.locator('div[role="dialog"].bg-popover span').first
option.click(force=True)
page.wait_for_timeout(500)
```

For the 2nd and 3rd comboboxes, use `.nth(1)` and `.nth(2)`:
```python
packing_btn = dialog.locator('button[role="combobox"]').nth(1)
in_stock_btn = dialog.locator('button[role="combobox"]').nth(2)
```

---

## 3. Radix Calendar/Date Picker (Expiry Date)

The Expiry Date uses a Radix month/year calendar picker — a `button:has-text("Month/Year")` trigger that opens a popover with:
- A year `<select>` dropdown at top
- A grid of month `<button>` elements (Jan, Feb, Mar, ..., Dec)
- A "Today" button

**Critical: Use `button:has-text("Dec")` NOT `:text("Dec")`** — the year `<select>` has hidden `<option>` elements that `:text()` matches instead.

**Critical: Scope to the calendar popover dialog.**

```python
expiry_btn = dialog.locator('button:has-text("Month/Year")')
expiry_btn.first.click(force=True)
page.wait_for_timeout(800)

# Scope to the calendar popover (last visible dialog)
cal_popover = page.locator('div[role="dialog"].bg-popover:visible').last
if cal_popover.count() == 0:
    cal_popover = page.locator('div[role="dialog"]:visible').last

# Map month number to abbreviation
month_names = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
               "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
               "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
month_abbr = month_names.get(target_month, "Dec")

month_btn = cal_popover.locator(f'button:has-text("{month_abbr}")')
month_btn.first.click(force=True)
page.wait_for_timeout(500)
```

---

## 4. File/Image Upload

```python
file_input = dialog.locator('input[type="file"]').first
file_input.set_input_files(image_path)
page.wait_for_timeout(1000)
```

---

## 5. Dialog Scoping — Always Scope Locators to the Modal

There are multiple `role="dialog"` elements on the page (the main modal + popovers).
**Always scope locators** to avoid matching wrong elements:

```python
# For the main modal:
dialog = page.locator('div[role="dialog"][data-state="open"]').last

# For popovers (Brand, Packing, calendar):
popover = page.locator('div[role="dialog"].bg-popover:visible').last
```

---

## 6. Checkpoint Pattern — Gate Dependent Steps

When a step might not find a section (e.g., scroll to find a button), return `False` to skip all dependent steps:

```python
def cp_find_section():
    for i in range(20):
        _scroll_down(page, 500)
        if page.locator('button:has-text("Target")').count() > 0:
            return True
    checkpoints.skip("Find section", "Not found after scrolling")
    return False

# Gate dependent steps
found = checkpoints.run("Find section", cp_find_section)
if found:
    checkpoints.run("Open modal", cp_open_modal)
    checkpoints.run("Fill fields", cp_fill_fields)
    checkpoints.run("Submit", cp_submit)
```

`checkpoints.run()` returns `False` when the function returns `False` (marks as SKIP).

---

## 7. Checkpoint Logging — Use ASCII Only

Windows cp1252 encoding breaks with Unicode. Use ASCII:
```python
print(f"[{self.tc_id}] >> {name}", flush=True)   # Start
print(f"[{self.tc_id}] OK {name}", flush=True)    # Pass
print(f"[{self.tc_id}] FAIL {name}: {err}", flush=True)  # Fail
print(f"[{self.tc_id}] SKIP {name}: {reason}", flush=True)  # Skip
```

---

## 8. Radix UI Text Inputs

Standard `input[placeholder="Field Name"]` locators work for text inputs. Use `force=True` if needed:

```python
inp = dialog.locator('input[placeholder="Product Name"]').first
inp.click()
inp.fill("Test Product")
```

For small dimension fields (L, B, H):
```python
for dim in ["L", "B", "H"]:
    inp = dialog.locator(f'input[placeholder="{dim}"]').first
    if inp.count() > 0 and inp.is_visible():
        inp.click()
        inp.fill("10")
```

---

## 9. Login Pattern

```python
def _login(page, test_user, base_url):
    page.goto(base_url + "login", wait_until="domcontentloaded")
    email = page.locator('input[placeholder*="Email"], input[type="email"]').first
    pw = page.locator('input[type="password"]').first
    sign_in = page.locator('button:has-text("Sign In"), button[type="submit"]').first
    email.wait_for(state="visible", timeout=15000)
    email.click()
    page.keyboard.type(test_user["email"], delay=50)
    pw.click()
    page.keyboard.type(test_user["password"], delay=50)
    sign_in.click()
    page.wait_for_timeout(3000)
    page.wait_for_load_state("domcontentloaded")
```

---

## 10. Catalog Page Navigation

```python
def catalog_page(page, test_user, base_url, sequential_page):
    if sequential_page is not None:
        sequential_page.goto(base_url + "listing/catalog", wait_until="domcontentloaded")
        sequential_page.locator('input[placeholder*="Search"]').first.wait_for(state="visible", timeout=20000)
        return sequential_page
    _login(page, test_user, base_url)
    page.goto(base_url + "listing/catalog", wait_until="domcontentloaded")
    page.locator('input[placeholder*="Search"]').first.wait_for(state="visible", timeout=20000)
    page.wait_for_timeout(2000)
    return page
```

---

## 11. Error Detection After Actions

```python
def _check_no_page_errors(page, context_label=""):
    page.wait_for_timeout(500)
    error_selectors = [
        '[role="alert"]:visible',
        '[class*="toast"]:visible:has-text("error")',
        '[class*="toast"]:visible:has-text("Error")',
    ]
    for sel in error_selectors:
        errors = page.locator(sel)
        if errors.count() > 0:
            error_text = errors.first.text_content().strip()[:300]
            raise AssertionError(f"Page error: {error_text}")
```

---

## Quick Reference — Element → Locator Pattern

| Element | Locator |
|---------|---------|
| Modal dialog | `page.locator('div[role="dialog"][data-state="open"]').last` |
| Combobox trigger | `dialog.locator('button[role="combobox"]').first` |
| Combobox option | `page.locator('div[role="dialog"].bg-popover span').first` |
| Calendar trigger | `dialog.locator('button:has-text("Month/Year")')` |
| Month button | `cal_popover.locator('button:has-text("Dec")')` |
| File input | `dialog.locator('input[type="file"]').first` |
| Text input | `dialog.locator('input[placeholder="Field Name"]').first` |
| Submit button | `dialog.locator('button:has-text("Request Product")')` |
| Scroll | `page.mouse.wheel(0, 500)` |
| Force click | `element.click(force=True)` |
