"""QAmate step runtime: ONE implementation of every browser step.

The fast explorer executes steps through `Flow` while it authors a test, and the
generated test calls the very same `Flow` methods with the very same targets. What
passed live is therefore exactly what the test does on replay -- there is no second
code path that could heal, wait or type differently.

Waiting is effect-based, never a guess:
  * Playwright auto-waits for each target to be actionable before acting;
  * after an action, the step waits for its declared effect (URL, element shown or
    hidden, value) when one is given;
  * every step ends with `settle()`: wait until the page's DOM has been quiet, no
    fetch/XHR is in flight and no short timer is pending, capped at a few seconds.
No `networkidle`, no fixed sleeps.

Generated tests import this module (tests/conftest.py puts engine/ on sys.path):

    flow = Flow(page, base_url=base_url, checkpoints=checkpoints)
    flow.goto("/")
    flow.fill(page.get_by_role("textbox", name="Username"), tc_data["username"], "Username")
    flow.click(page.get_by_role("button", name="Login"), "Login", expect_url="/inventory.html")
"""
import contextlib
import re
import time
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import expect

# Tracks DOM mutations and in-flight fetch/XHR so settle() can tell when the page has
# finished reacting. Installed as an init script (every document) and into the current one.
QUIET_PROBE = r"""
(() => {
  if (window.__qmProbe) return;
  // pending: request id -> start time. Requests open longer than 1.5 s are treated as
  // background (analytics beacons, long-polling, streams) and stop counting.
  const probe = window.__qmProbe = { pending: new Map(), seq: 0, last: performance.now() };
  probe.busy = () => { const now = performance.now(); let n = 0;
    probe.pending.forEach((t) => { if (now - t < 1500) n++; }); return n; };
  const bump = () => { probe.last = performance.now(); };
  const watch = () => new MutationObserver(bump).observe(document, {
    subtree: true, childList: true, attributes: true, characterData: true });
  try { watch(); } catch (e) { document.addEventListener('DOMContentLoaded', watch, { once: true }); }
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (...args) {
      const id = ++probe.seq; probe.pending.set(id, performance.now()); bump();
      return origFetch.apply(this, args).finally(() => { probe.pending.delete(id); bump(); });
    };
  }
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    const id = ++probe.seq; probe.pending.set(id, performance.now()); bump();
    this.addEventListener('loadend', () => { probe.pending.delete(id); bump(); }, { once: true });
    return send.apply(this, args);
  };
  // Short one-shot timers count as pending work (debounced search, simulated saves);
  // long ones (polling, carousels) are ignored so they can't stall every step.
  const timers = probe.timers = new Set();
  const setT = probe.rawSetTimeout = window.setTimeout, clearT = window.clearTimeout;
  window.setTimeout = function (fn, delay, ...rest) {
    const ms = Number(delay) || 0;
    if (ms <= 0 || ms > 1500 || typeof fn !== 'function') return setT.call(this, fn, delay, ...rest);
    const id = setT.call(this, (...a) => { timers.delete(id); bump(); return fn(...a); }, delay, ...rest);
    timers.add(id); return id;
  };
  window.clearTimeout = function (id) { timers.delete(id); return clearT.call(this, id); };
})();
"""

SETTLE_JS = r"""
([quietMs, maxMs]) => new Promise((resolve) => {
  const probe = window.__qmProbe;
  const start = performance.now();
  if (!probe) { resolve({ quiet: false, probe: false, ms: 0 }); return; }
  const tick = () => {
    const now = performance.now();
    // Quiet is measured from the start of this wait, so work an action triggers a tick
    // later (hashchange renders, microtasks) is always seen.
    if (probe.busy() === 0 && (!probe.timers || probe.timers.size === 0) && now - Math.max(probe.last, start) >= quietMs)
      return resolve({ quiet: true, ms: Math.round(now - start) });
    if (now - start >= maxMs)
      return resolve({ quiet: false, ms: Math.round(now - start), requests: probe.busy(),
                       timers: probe.timers ? probe.timers.size : 0 });
    (probe.rawSetTimeout || setTimeout)(tick, 40);   // our own poll must not count as page work
  };
  tick();
})
"""


class StepError(AssertionError):
    """A step could not do what it was asked; the message says which step and why."""


def relative_url(url):
    """Path + query + fragment of a URL: what an expected-URL check compares, so a
    test stays portable across hosts (dev/staging) and SPA hash routes are kept."""
    parts = urlsplit(url)
    rel = parts.path or "/"
    if parts.query:
        rel += "?" + parts.query
    if parts.fragment:
        rel += "#" + parts.fragment
    return rel


def url_pattern(expected):
    """Regex matching `expected` (a relative URL or a full URL) on any origin."""
    rel = relative_url(expected) if "://" in expected else expected
    if not rel.startswith(("/", "#", "?")):
        rel = "/" + rel
    return re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+" + re.escape(rel) + r"$")


class Flow:
    """Executes test steps on a Playwright page. Each method is one step: it acts,
    waits for the declared effect, settles, and reports the step to `checkpoints`
    (the run timeline) when given."""

    def __init__(self, page, base_url=None, checkpoints=None, timeout_ms=10000,
                 settle_quiet_ms=250, settle_max_ms=3000):
        self.page = page
        self.base_url = base_url
        self.checkpoints = checkpoints
        self.timeout_ms = timeout_ms
        self.settle_quiet_ms = settle_quiet_ms
        self.settle_max_ms = settle_max_ms
        self.last = {}
        try:
            page.context.add_init_script(QUIET_PROBE)
        except Exception:
            pass
        self._install_probe()

    # ── plumbing ────────────────────────────────────────────────────────────
    def _install_probe(self):
        try:
            self.page.evaluate(QUIET_PROBE)
        except Exception:
            pass   # mid-navigation: the init script covers the next document

    def _target(self, target):
        return self.page.locator(target) if isinstance(target, str) else target

    @contextlib.contextmanager
    def _step(self, name):
        started = time.monotonic()
        cm = self.checkpoints.step(name) if self.checkpoints is not None else contextlib.nullcontext()
        with cm:
            yield
        self.last = {"name": name, "ms": round((time.monotonic() - started) * 1000), "url": self.page.url}

    def settle(self):
        """Wait until the DOM is quiet and no fetch/XHR is pending (capped). Returns
        what happened; never raises -- a busy page just reaches the cap."""
        for _ in range(2):
            try:
                return self.page.evaluate(SETTLE_JS, [self.settle_quiet_ms, self.settle_max_ms])
            except Exception:
                # The document navigated mid-wait: let the new one parse, then re-check.
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                except Exception:
                    pass
                self._install_probe()
        return {"quiet": False, "ms": 0}

    def _effects(self, expect_url=None, expect_visible=None, expect_hidden=None):
        if expect_url:
            expect(self.page).to_have_url(url_pattern(expect_url), timeout=self.timeout_ms)
        if expect_visible is not None:
            expect(self._target(expect_visible)).to_be_visible(timeout=self.timeout_ms)
        if expect_hidden is not None:
            expect(self._target(expect_hidden)).to_be_hidden(timeout=self.timeout_ms)

    # ── navigation ──────────────────────────────────────────────────────────
    def goto(self, url, name=None):
        full = urljoin(self.base_url, url) if self.base_url and "://" not in url else url
        with self._step(name or f"Open {url}"):
            self.page.goto(full, wait_until="domcontentloaded", timeout=max(self.timeout_ms, 20000))
            self._install_probe()
            self.settle()

    # ── actions ─────────────────────────────────────────────────────────────
    def click(self, target, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None):
        with self._step(name or "Click"):
            self._target(target).click(timeout=self.timeout_ms)
            self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()

    def dblclick(self, target, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None):
        with self._step(name or "Double-click"):
            self._target(target).dblclick(timeout=self.timeout_ms)
            self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()

    def hover(self, target, name=None, *, expect_visible=None):
        with self._step(name or "Hover"):
            self._target(target).hover(timeout=self.timeout_ms)
            self._effects(expect_visible=expect_visible)
            self.settle()

    def fill(self, target, value, name=None):
        """Replace the field's value. If the app doesn't accept a programmatic fill
        (some masked or controlled inputs), type it for real instead. Either way the
        field must end up showing exactly `value`, live and on replay."""
        value = "" if value is None else str(value)
        with self._step(name or "Fill"):
            loc = self._target(target)
            loc.fill(value, timeout=self.timeout_ms)
            if not self._shows(loc, value):
                loc.click(timeout=self.timeout_ms)
                loc.press("ControlOrMeta+a")
                loc.press_sequentially(value, delay=20)
                if not self._shows(loc, value):
                    raise StepError(f"field shows {self._editable_value(loc)!r} after entering {value!r}")
            self.settle()

    @classmethod
    def _shows(cls, loc, value):
        """True when the field displays `value` -- allowing input masks that only add
        separators (e.g. '5551234567' shown as '(555) 123-4567')."""
        actual = cls._editable_value(loc)
        if actual is None or actual == value:
            return True
        squash = lambda s: re.sub(r"[\W_]+", "", s).lower()
        return bool(value) and squash(actual) == squash(value)

    @staticmethod
    def _editable_value(loc):
        try:
            return loc.input_value(timeout=1000)
        except Exception:
            return None   # contenteditable / non-input: no value to read back

    def select(self, target, option, name=None):
        """Choose an option in a native <select> by its value or visible label."""
        with self._step(name or f"Select {option}"):
            loc = self._target(target)
            try:
                loc.select_option(value=str(option), timeout=self.timeout_ms)
            except Exception:
                loc.select_option(label=str(option), timeout=self.timeout_ms)
            self.settle()

    def check(self, target, checked=True, name=None):
        with self._step(name or ("Check" if checked else "Uncheck")):
            self._target(target).set_checked(bool(checked), timeout=self.timeout_ms)
            self.settle()

    def press(self, key, target=None, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None):
        with self._step(name or f"Press {key}"):
            if target is None:
                self.page.keyboard.press(key)
            else:
                self._target(target).press(key, timeout=self.timeout_ms)
            self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()

    def upload(self, target, files, name=None):
        with self._step(name or "Upload file"):
            self._target(target).set_input_files(files, timeout=self.timeout_ms)
            self.settle()

    # ── checks (web-first: they retry until true or timeout) ─────────────────
    def expect_url(self, url, name=None):
        with self._step(name or f"URL is {url}"):
            expect(self.page).to_have_url(url_pattern(url), timeout=self.timeout_ms)

    def expect_visible(self, target, name=None):
        with self._step(name or "Is visible"):
            expect(self._target(target)).to_be_visible(timeout=self.timeout_ms)

    def expect_hidden(self, target, name=None):
        with self._step(name or "Is hidden"):
            expect(self._target(target)).to_be_hidden(timeout=self.timeout_ms)

    def expect_text(self, target, text, name=None, exact=False):
        with self._step(name or f"Shows '{text}'"):
            if exact:
                expect(self._target(target)).to_have_text(text, timeout=self.timeout_ms)
            else:
                expect(self._target(target)).to_contain_text(text, timeout=self.timeout_ms)

    def expect_value(self, target, value, name=None):
        with self._step(name or f"Value is '{value}'"):
            expect(self._target(target)).to_have_value(str(value), timeout=self.timeout_ms)

    def expect_checked(self, target, checked=True, name=None):
        with self._step(name or ("Is checked" if checked else "Is not checked")):
            expect(self._target(target)).to_be_checked(checked=bool(checked), timeout=self.timeout_ms)

    def expect_page_text(self, text, present=True, name=None):
        """Visible page text contains (or doesn't contain) `text`."""
        with self._step(name or (f"Page shows '{text}'" if present else f"Page does not show '{text}'")):
            body = expect(self.page.locator("body"))
            if present:
                body.to_contain_text(text, use_inner_text=True, timeout=self.timeout_ms)
            else:
                body.not_to_contain_text(text, use_inner_text=True, timeout=self.timeout_ms)

    def expect_count(self, target, count, name=None):
        with self._step(name or f"Count is {count}"):
            expect(self._target(target)).to_have_count(int(count), timeout=self.timeout_ms)
