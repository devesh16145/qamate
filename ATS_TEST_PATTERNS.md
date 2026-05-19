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

There are TWO different calendar implementations used in the app:

### 3a. Radix popover calendar (Create New Product modal)

The Expiry Date uses a Radix month/year calendar picker — a `button:has-text("Month/Year")` trigger that opens a popover with:
- A year `<select>` dropdown at top
- A grid of month `<button>` elements (Jan, Feb, Mar, ..., Dec)
- A "Today" button

```python
expiry_btn = dialog.locator('button:has-text("Month/Year")')
expiry_btn.first.click(force=True)
page.wait_for_timeout(800)

# Scope to the calendar popover (last visible dialog)
cal_popover = page.locator('div[role="dialog"].bg-popover:visible').last
if cal_popover.count() == 0:
    cal_popover = page.locator('div[role="dialog"]:visible').last

month_abbr = month_names.get(target_month, "Dec")
month_btn = cal_popover.locator(f'button:has-text("{month_abbr}")')
month_btn.first.click(force=True)
```

### 3b. react-day-picker calendar (Request Variant modal)

Some modals use react-day-picker which renders months as **hidden `<option>` elements** inside a `<select class="rdp-months_dropdown">` with `opacity:0`. `button:has-text("Dec")` WILL NOT WORK.

**Fix: Use `page.select_option()` on the hidden select:**
```python
expiry_btn = dialog.locator('button:has-text("Month/Year")')
expiry_btn.first.click(force=True)
page.wait_for_timeout(1000)

month_select = page.locator('select.rdp-months_dropdown')
if month_select.count() > 0:
    # value is 0-indexed: Jan=0, Feb=1, ..., Dec=11
    month_val = str(int(target_month) - 1)
    month_select.first.select_option(value=month_val)
    page.wait_for_timeout(500)
else:
    # Fallback: try button:has-text (Radix calendar)
    ...use 3a pattern...
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

## 12. Tailwind Checkbox Selection — Target `.peer`, NOT `[role=checkbox]`

The Agrim app's checkbox UI uses Tailwind's `peer` pattern: a `<input type=checkbox>` with `display:none` (or 0×0 dimensions) plus a sibling `.peer` div that React's `onChange` actually listens to.

**`page.get_by_role("checkbox").click(force=True)` bypasses React's onChange**: Playwright clicks the hidden input via DevTools protocol, but the visible `.peer` overlay never receives the click. The React component doesn't know the box was checked → downstream form state (e.g. "GST selected → mark as head → enable Verify") never propagates.

```python
# WRONG — bypasses React's onChange handler
page.get_by_role("checkbox").first.click(force=True)

# CORRECT — clicks the visible overlay React listens to
page.locator('.flex.gap-2.items-center > .peer').first.click(force=True)
```

How to identify the right selector: open Chrome DevTools, inspect the visible checkbox, find the parent `<label>` or `<div>` that has the click handler bound. Usually it's `.peer` plus a wrapper class.

**Symptom of getting this wrong:** the click "succeeds" in Playwright but a downstream Verify/Submit button stays `disabled` even after the supposedly-selected state should have enabled it. If that happens, you targeted the hidden input — switch to `.peer`.

---

## 13. Server-Generated Numeric IDs — Use Positional Locators

Recording-time IDs like `#poc-name-164096`, `#poc-contact-164097`, `#license-id-200145` are DB row IDs assigned per signup/PAN. They change every run — clicking those exact IDs at replay time fails.

**Recorder auto-rewrite (in `recorder_parser._rewrite_dynamic_ids`):** any `#prefix-<digits>` locator where the suffix has **3+ digits** is rewritten to `[id^="prefix"].nth(N)` where N is the positional index in the recording. Same `full_id` always maps to the same `.nth(N)` so click+fill on the same field don't drift. Static unique IDs like `#submit-button` are NOT rewritten.

```python
# Recording captures (brittle — IDs change every run):
page.locator('#poc-name-164096').fill("TEST_POC_1")
page.locator('#poc-contact-164096').fill("9999999999")

# Auto-rewritten to (portable across runs):
page.locator('[id^="poc-name"]').nth(1).fill("TEST_POC_1")
page.locator('[id^="poc-contact"]').nth(1).fill("9999999999")
```

If you're hand-editing a test and see hardcoded numeric-suffix IDs, rewrite them yourself.

---

## 14. Dynamic Select-All Loop — `for _i in range(locator.count())`

When the recording captures multiple consecutive `.first/.nth(N)` clicks on the same bare-positional locator (e.g. selecting all GSTINs after PAN verify), `recorder_parser._detect_select_all_runs` collapses them into a count-driven loop that adapts to live page count. **Works for any N: 0, 1, 2, ..., M.**

```python
# Recording-time (hardcoded for 4-GST PAN):
page.get_by_role("checkbox").first.click()
page.get_by_role("checkbox").nth(1).click()
page.get_by_role("checkbox").nth(2).click()
page.get_by_role("checkbox").nth(3).click()

# Auto-collapsed at codegen time:
_checkboxes = page.get_by_role("checkbox")
for _i in range(_checkboxes.count()):
    _checkboxes.nth(_i).click(force=True)
    page.wait_for_timeout(300)
```

**Detection rule:** any run of consecutive bare-positional `page.get_by_role("checkbox")` clicks (1+ in a row) collapses into a loop. Named checkboxes (`get_by_role("checkbox", name="Terms")`) are NOT collapsed — they target a specific named element.

**Combine with §12 for Tailwind UI:** if `[role=checkbox]` doesn't propagate state to React, swap the base locator to `.peer` overlays:
```python
_overlays = page.locator('.flex.gap-2.items-center > .peer')
for _i in range(_overlays.count()):
    _overlays.nth(_i).click(force=True)
    page.wait_for_timeout(300)
```

---

## 15. `.nth(N)` with Count Guard — Adaptive Positional Button Clicks

Recordings often capture `.nth(N)` clicks for buttons that only render in multi-row scenarios (e.g. "Make Head Branch" — one button per GST row, only shown when 2+ GSTs exist). With 0 or 1 rows in a different test scenario, `.nth(N)` doesn't exist → 10s timeout per attempt.

**Pattern: count-guard + `.last` fallback:**
```python
# Recording (brittle — assumes 4 GSTs):
page.get_by_role("button", name="Make Head Branch").nth(1).click()

# Manual edit (adapts to any non-zero count, no-op if zero):
_head_btns = page.get_by_role("button", name="Make Head Branch")
if _head_btns.count() > 0:
    _head_btns.last.click()
    page.wait_for_timeout(500)
# else: implicit no-op (e.g. 1 GST → head is auto-assigned at the row level)
```

**Important caveat (Agrim signup with 1 GST):** the "Make Head Branch" button doesn't render with 1 GST, but **the GST still needs to be marked as head** for the downstream Verify button to enable. That happens via §12 — clicking the `.peer` overlay on the GST row, not the hidden checkbox. The count-guard correctly skips the button, but you must select the row via the proper overlay or downstream Verify stays disabled.

---

## 16. Signup — OTP Auto-Advance Boxes

OTP digit inputs use long Tailwind class chains like:
```
.w-\[42px\].h-\[48px\].text-neutral-6.text-center.text-base.rounded-lg.border...placeholder\:text-neutral-3.undefined
```

The first OTP digit input has accessibility name `*`; subsequent digits are addressed via `.nth(N)` of the same role+name, or via CSS `:nth-child(N)` locators when recorded.

```python
page.get_by_role("textbox", name="*").first.fill("1")
page.get_by_role("textbox", name="*").nth(1).fill("2")
page.get_by_role("textbox", name="*").nth(2).fill("3")
# ... up to .nth(5)
```

**Don't inject heavy work between consecutive OTP `.fill()` calls.** A `page.evaluate(...)`, a `repr(Locator)`, or anything that forces a layout reflow / protocol roundtrip can break React's auto-advance — the next `.fill()` then targets a stale element and times out at 30s.

This is *the* reason the coverage analyzer's pytest plugin (`engine/dom_inspector_plugin.py`) snapshots **after** each action and gates DOM evaluation on URL change — keeping the React commit cycle untouched.

---

## 17. MUI Autocomplete — keyboard select, NOT li.click() (Admin Panel)

The Agrim Admin Panel uses MUI portal-rendered `<li>` elements inside `.MuiMenu-root` for autocomplete suggestions (vendor search, product search, etc.). Clicking the `<li>` with Playwright **sets the display text but does not fully wire the React state** — e.g. `listing_id` stays empty, the row is invalid, downstream Verify/Save buttons stay disabled.

**Use keyboard select instead:**

```python
# WRONG — display text set, internal state not wired
page.get_by_role("textbox", name="Search").fill("Testing Brand")
page.get_by_text("Testing Brand Saloniiiiii (HY").click()

# CORRECT — fills the row's listing_id and fires onChange
page.get_by_role("textbox", name="Search").fill("Testing Brand")
page.wait_for_timeout(1500)            # wait for autocomplete API
page.keyboard.press("ArrowDown")
page.wait_for_timeout(300)
page.keyboard.press("Enter")
page.wait_for_timeout(800)
```

**Auto-applied by recorder_parser** (`_is_search_autocomplete_pair`): when a recording shows a `.fill()` on a "Search"-labelled textbox followed by a `.click()` on a `get_by_text(...)`, the generator drops the click and emits the keyboard select sequence above.

---

## 18. MUI Number Inputs — press_sequentially, NOT fill() (Admin Panel)

MUI number inputs in the admin PO form (quantity, unit price, tax rate, master_packing) are React controlled inputs whose `onChange` listens to keydown/input events. `.fill()` sets the DOM value but does NOT fire onChange → form state stays stale, totals never auto-calculate, **Save Changes stays disabled forever**.

```python
# WRONG — DOM value set, React onChange never fires
page.locator('input[name="quantity"]').fill("7")

# CORRECT — real keystrokes + blur triggers React onChange + validation
qty = page.locator('input[name="quantity"]')
qty.click()
qty.press_sequentially("7", delay=80)
page.keyboard.press("Tab")               # blur fires validation API
page.wait_for_timeout(500)
```

**Selectors that trigger the auto-fix:**
- `input[name="quantity"]`, `"unit_price"`, `"tax_rate"`, `"master_packing"`, `"total"`
- `placeholder="#"` (rate inputs in MUI table cells use this)
- `get_by_placeholder("#")`, `get_by_role("spinbutton")`

**Auto-applied by recorder_parser** (`_is_mui_numeric_input_fill`): any `.fill()` whose locator matches one of these is rewritten to `.press_sequentially(VALUE, delay=80)` plus a `Tab` press. Regex handles both raw (`name="quantity"`) and backslash-escaped (`name=\"quantity\"`) forms.

---

## 19. Coverage Analyzer — Observing Tests Without Disturbing Them

The Coverage Modal (right-click a TC → Analyze Coverage) runs the generated `test_<flow>.py` via pytest with `engine/dom_inspector_plugin.py` attached. The plugin monkey-patches `Locator.click/fill/check/...` to record `{action, url, selector}` *after* each call. DOM inventory is evaluated only when the URL changes.

Output: `agrim-ats/results/_coverage/<TC-ID>_latest.json` — fixed-path file overwritten each run, containing a `navigation_map` of every unique page visited with its interactive-element inventory.

**Rules if you extend the plugin:**
- **Never call `repr(Locator)` in the wrapper.** It walks into `repr(Frame)` → `frame.url` → CDP protocol roundtrip (30–100ms each). That stall breaks timing-sensitive flows like OTP auto-advance and GST head-selection. Use `getattr(self, "_selector", "")` instead.
- **Delegate first, observe second.** `result = _orig(self, *args, **kwargs)` must run *before* any recording — observation must never delay or reflow before a click fires.
- **DOM `evaluate` only on URL change.** Forcing a synchronous layout reflow mid-React-commit can corrupt state propagation.

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
| Tailwind checkbox (visible) | `page.locator('.flex.gap-2.items-center > .peer').first.click(force=True)` |
| Dynamic-suffix field | `page.locator('[id^="poc-name"]').nth(N)` |
| Select-all positional clicks | `for _i in range(_loc.count()): _loc.nth(_i).click(force=True)` |
| Adaptive positional button | `_btns = page.get_by_role("button", name="X"); _btns.last.click() if _btns.count() > 0 else None` |
| OTP digit N (Agrim signup) | `page.get_by_role("textbox", name="*").nth(N).fill("digit")` |
| MUI autocomplete select | `fill(query); keyboard.press("ArrowDown"); keyboard.press("Enter")` |
| MUI number input (admin PO) | `loc.press_sequentially(value, delay=80); keyboard.press("Tab")` |
