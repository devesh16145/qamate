"""
QAmate — Coverage Snapshot pytest Plugin

When loaded into a pytest run (via `-p engine.dom_inspector_plugin`), this
monkey-patches Playwright's sync `Locator` action methods so each user-visible
action records a DOM-state snapshot *before* it fires.

The captured data per action:
    { action, locator, url, title, elements: [{tag, role, text, ...}, ...] }

After the pytest session completes, all captured snapshots are written to the
file path in `ATS_COVERAGE_SNAPSHOT_OUT`. The inspector reads that file and
turns it into a navigation map + coverage suggestions.

Why a plugin (not a custom replay engine)? The existing generated test file
(test_<flow>.py) already contains every adaptation needed — force=True on
overlays, `expect_file_chooser` for uploads, dynamic-count loops for
checkboxes, `.last` fallbacks, etc. We let the real test drive the browser
and just observe.
"""
import json
import os
import threading

# Pytest fixture file — must be importable as `engine.dom_inspector_plugin`
# Re-run safety: the patches are applied only once per process.
_PATCHED = False
_LOCK = threading.Lock()

_SNAPSHOTS = []

INTERACTIVE_DOM_JS = r"""() => {
    const selectors = [
        'button',
        'a[href]',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="combobox"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[role="switch"]',
    ];
    const seen = new Set();
    const out = [];
    for (const sel of selectors) {
        let nodes;
        try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of nodes) {
            if (seen.has(el)) continue;
            seen.add(el);
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            const text = (el.innerText || el.textContent || '').trim().slice(0, 150);
            out.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                text: text,
                aria_label: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                id: el.id || '',
                value: el.value || '',
                checked: !!el.checked,
                disabled: !!el.disabled,
                required: !!el.required || el.getAttribute('aria-required') === 'true',
            });
        }
    }
    return out;
}"""


# Track the last URL we did a full DOM snapshot on. We only re-evaluate
# `INTERACTIVE_DOM_JS` when the URL changes — that's the only time a
# new "page" is worth inventorying. Same-URL actions get a cheap record
# (action + locator + url) so we don't disturb React commit cycles.
_LAST_FULL_SNAPSHOT_URL = [None]


def _record_minimal(page, action, locator_str=""):
    """Cheap record: action, locator, current URL. No JS evaluation."""
    try:
        _SNAPSHOTS.append({
            "action": action,
            "locator": (locator_str or "")[:200],
            "url": page.url,
            "title": "",      # filled later only on URL change
            "elements": [],   # filled later only on URL change
            "needs_full": True,
        })
    except Exception:
        pass


def _maybe_full_snapshot(page):
    """If the URL just changed, do a one-time full DOM inventory and stamp it
    onto every snapshot at this URL that's still missing one."""
    try:
        url = page.url
    except Exception:
        return
    if url == _LAST_FULL_SNAPSHOT_URL[0]:
        return
    _LAST_FULL_SNAPSHOT_URL[0] = url

    title = ""
    elements = []
    try:
        title = page.title()
    except Exception:
        pass
    try:
        elements = page.evaluate(INTERACTIVE_DOM_JS)
    except Exception:
        pass

    # Back-fill any snapshots at this URL that still have empty elements
    for snap in reversed(_SNAPSHOTS):
        if snap.get("url") != url:
            break
        if snap.get("needs_full"):
            snap["title"] = title
            snap["elements"] = elements
            snap["needs_full"] = False


def _apply_patches():
    """Monkey-patch Playwright action methods to record activity *after* the
    action completes (so we never delay or reflow before a click fires)."""
    global _PATCHED
    with _LOCK:
        if _PATCHED:
            return
        _PATCHED = True

    from playwright.sync_api import Locator, Page

    for cls, methods in [
        (Locator, ("click", "fill", "check", "uncheck", "dblclick",
                   "select_option", "set_input_files", "press", "type", "hover")),
        (Page, ("goto", "click", "fill", "check", "dblclick", "select_option")),
    ]:
        for name in methods:
            if not hasattr(cls, name):
                continue
            original = getattr(cls, name)

            def make_wrapper(_orig, _action, _is_locator):
                def wrapper(self, *args, **kwargs):
                    # Delegate FIRST so we never delay the actual user action
                    result = _orig(self, *args, **kwargs)
                    # Then record cheaply (no JS, no reflow on the hot path).
                    # IMPORTANT: don't call repr(self) — repr(Locator) walks
                    # into repr(Frame) which reads frame.url, a synchronous
                    # protocol roundtrip to Chrome. That's a 30–100ms stall
                    # between actions and breaks timing-sensitive React state
                    # commits (e.g. OTP auto-advance, GST head selection).
                    # Use the stored _selector instead — just an attribute read.
                    try:
                        page = self.page if _is_locator else self
                        if _is_locator:
                            # Playwright Locator stores its selector on the
                            # private _impl_obj — Locator._selector is not
                            # exposed publicly. _impl_obj._selector is a plain
                            # string attribute, no protocol call.
                            impl = getattr(self, "_impl_obj", None)
                            loc_str = (getattr(impl, "_selector", "") if impl
                                       else getattr(self, "_selector", "")) or ""
                        else:
                            loc_str = args[0] if args else ""
                        _record_minimal(page, _action, str(loc_str))
                        # Only do the heavy DOM evaluation if URL changed —
                        # that's the only time a new page inventory is meaningful.
                        _maybe_full_snapshot(page)
                    except Exception:
                        pass
                    return result
                return wrapper

            wrapped = make_wrapper(original, name, cls is Locator)
            setattr(cls, name, wrapped)


# ── Pytest hooks ──

def pytest_configure(config):
    """Apply Playwright patches as soon as pytest loads this plugin."""
    _apply_patches()


def pytest_sessionfinish(session, exitstatus):
    """At the very end, dump all collected snapshots to the configured path."""
    out_path = os.environ.get("ATS_COVERAGE_SNAPSHOT_OUT")
    if not out_path:
        return
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        # Tidy up the transient flag before serialising
        for s in _SNAPSHOTS:
            s.pop("needs_full", None)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "snapshots": _SNAPSHOTS,
                "snapshot_count": len(_SNAPSHOTS),
            }, f, default=str)
    except Exception as e:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"snapshots": [], "error": str(e)[:300]}, f)
        except Exception:
            pass
