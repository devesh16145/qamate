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
    fetch/XHR, script or stylesheet load is in flight and no short timer is pending,
    capped at a few seconds. A page the action didn't change gets a short look (60 ms);
    one that is reacting must stay unchanged for 120 ms after its last change. Nodes
    that never stop changing (progress bars, clocks, spinners) are recognised and ignored.
No `networkidle`, no fixed sleeps.

What real apps need is handled here once, for the live run and the test alike:
  * browser dialogs (alert / confirm / prompt): `dialog="accept"` or `"dismiss"` on the step;
  * links that open a new tab: `new_tab=True` follows it, `close_tab()` returns;
  * targets in lazily loaded lists: the list is scrolled until the target exists;
  * text checks see the whole page -- every frame and web component -- and text the
    page showed since the last action even if it has gone again (toasts);
  * slow operations: checks take `timeout=` seconds;
  * test data: `use_data()` gives each run its own `{unique}` value and reads
    `{secret:NAME}` values from the environment or an untracked secrets file.

Generated tests import this module (tests/conftest.py puts engine/ on sys.path):

    flow = Flow(page, base_url=base_url, checkpoints=checkpoints)
    tc_data = flow.use_data(tc_data, __file__)
    flow.goto("/")
    flow.fill(page.get_by_role("textbox", name="Username"), tc_data["username"], "Username")
    flow.click(page.get_by_role("button", name="Login"), "Login", expect_url="/inventory.html")
"""
import contextlib
import datetime
import json
import os
import random
import re
import time
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import expect

# Tracks DOM mutations and in-flight work so settle() can tell when the page has finished
# reacting. Installed as an init script (every document, every frame) and into the current one.
QUIET_PROBE = r"""
(() => {
  if (window.__qmProbe) return;
  // pending: request id -> start time. Requests open longer than 1.5 s are treated as
  // background (analytics beacons, long-polling, streams) and stop counting as "busy".
  const probe = window.__qmProbe = { pending: new Map(), seq: 0, last: performance.now(), flash: [] };
  probe.busy = () => { const now = performance.now(); let n = 0;
    probe.pending.forEach((t) => { if (now - t < 1500) n++; }); return n; };
  const bump = () => { probe.last = performance.now(); };
  // Scripts and stylesheets being loaded (route chunks, preloads) are pending work too.
  const track = (node) => {
    const loads = (node.tagName === 'SCRIPT' && node.src) ||
                  (node.tagName === 'LINK' && /stylesheet|modulepreload|preload/.test(node.rel || ''));
    if (!loads) return;
    const id = ++probe.seq; probe.pending.set(id, performance.now());
    const done = () => { probe.pending.delete(id); bump(); };
    node.addEventListener('load', done, { once: true });
    node.addEventListener('error', done, { once: true });
  };
  // A node whose attributes or text keep changing (a progress bar, a clock, a spinner) is
  // decoration: after a few changes in a row it stops counting as "the page is reacting".
  const noisy = new WeakMap();
  const decoration = (record, now) => {
    if (record.type === 'childList') return false;
    const node = record.target.nodeType === 3 ? (record.target.parentNode || record.target) : record.target;
    let s = noisy.get(node);
    if (!s || now - s.since > 600) { s = { count: 0, since: now, until: s ? s.until : 0 }; noisy.set(node, s); }
    s.count++;
    if (s.count >= 4) s.until = now + 2000;
    return now < s.until;
  };
  // Text that appears is remembered until the next action, so a check can see a toast
  // that has already gone again.
  const remember = (node) => {
    try {
      const el = node.nodeType === 3 ? node.parentElement : node;
      if (!el || !el.getClientRects || /^(SCRIPT|STYLE|TEMPLATE)$/.test(el.tagName) || !el.getClientRects().length) return;
      const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
      if (text && text.length <= 300) { probe.flash.push(text); if (probe.flash.length > 80) probe.flash.shift(); }
    } catch (e) {}
  };
  const watch = () => new MutationObserver((records) => {
    const now = performance.now();
    let real = false, noted = 0;
    for (const r of records) {
      if (!decoration(r, now)) real = true;
      if (r.type === 'characterData' && noted++ < 40) remember(r.target);
      for (const n of r.addedNodes || []) {
        if (n.nodeType === 1) track(n);
        if ((n.nodeType === 1 || n.nodeType === 3) && noted++ < 40) remember(n);
      }
    }
    if (real) bump();
  }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
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
  // Short one-shot timers count as pending work (debounced search, simulated saves). Long
  // ones (up to a minute) are remembered separately: they don't hold up every step, but a
  // check that isn't true yet keeps waiting while one is running (a report being built).
  const timers = probe.timers = new Set(), slow = probe.slow = new Set();
  const setT = probe.rawSetTimeout = window.setTimeout, clearT = window.clearTimeout;
  window.setTimeout = function (fn, delay, ...rest) {
    const ms = Number(delay) || 0;
    if (ms <= 0 || ms > 60000 || typeof fn !== 'function') return setT.call(this, fn, delay, ...rest);
    const set = ms <= 1500 ? timers : slow;
    const id = setT.call(this, (...a) => { set.delete(id); bump(); return fn(...a); }, delay, ...rest);
    set.add(id); return id;
  };
  window.clearTimeout = function (id) { timers.delete(id); slow.delete(id); return clearT.call(this, id); };
  // Still working on something that takes a while? (a request in flight for up to a minute,
  // or a slow timer) -- a check that isn't true yet keeps waiting while this holds.
  probe.working = () => { const now = performance.now(); let n = slow.size + timers.size;
    probe.pending.forEach((t) => { if (now - t < 60000) n++; }); return n > 0; };
})();
"""

SETTLE_JS = r"""
([quietMs, maxMs, calmMs]) => new Promise((resolve) => {
  const probe = window.__qmProbe;
  const start = performance.now();
  if (!probe) { resolve({ quiet: false, probe: false, ms: 0 }); return; }
  const idle = () => probe.busy() === 0 && (!probe.timers || probe.timers.size === 0);
  // A page that hasn't changed lately and has nothing pending only needs a short look for
  // a delayed reaction; once it changes, it must then stay unchanged for the full period.
  const calm = idle() && start - probe.last >= calmMs;
  const tick = () => {
    const now = performance.now();
    const changed = probe.last > start;
    // Quiet is measured from the start of this wait, so work an action triggers a tick
    // later (hashchange renders, microtasks) is always seen.
    const need = calm && !changed ? calmMs : quietMs;
    if (idle() && now - Math.max(probe.last, start) >= need)
      return resolve({ quiet: true, ms: Math.round(now - start), calm: calm && !changed });
    if (now - start >= maxMs)
      return resolve({ quiet: false, ms: Math.round(now - start), requests: probe.busy(),
                       timers: probe.timers ? probe.timers.size : 0 });
    (probe.rawSetTimeout || setTimeout)(tick, 20);   // our own poll must not count as page work
  };
  tick();
})
"""

# Does this frame show `needle`? Visible text of the document, then of web components
# (shadow DOM, which innerText does not include), then text shown since the last action.
PAGE_TEXT_JS = r"""
(needle) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ');
  const want = norm(needle).trim();
  const has = (s) => norm(s).includes(want);
  let shown = !!document.body && has(document.body.innerText);
  if (!shown) {
    const visit = (root) => {
      for (const el of root.querySelectorAll('*')) {
        if (!el.shadowRoot) continue;
        for (const child of el.shadowRoot.children) if (has(child.innerText || child.textContent)) return true;
        if (visit(el.shadowRoot)) return true;
      }
      return false;
    };
    shown = visit(document);
  }
  const probe = window.__qmProbe;
  return { shown, flashed: !shown && !!probe && (probe.flash || []).some(has),
           sample: shown ? '' : norm(document.body ? document.body.innerText : '').trim().slice(0, 500) };
}
"""

# All visible text of this frame, web components included.
VISIBLE_TEXT_JS = r"""
() => {
  const parts = [document.body ? document.body.innerText : ''];
  const visit = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (!el.shadowRoot) continue;
      for (const child of el.shadowRoot.children) parts.push(child.innerText || child.textContent || '');
      visit(el.shadowRoot);
    }
  };
  visit(document);
  return parts.join('\n').replace(/\s+/g, ' ').slice(0, 200000);
}
"""

# Scroll every scrollable area (and the page) one screen further; true if anything moved.
SCROLL_MORE_JS = r"""
() => {
  const areas = [document.scrollingElement];
  for (const el of document.querySelectorAll('*')) {
    if (el.scrollHeight <= el.clientHeight + 20 || el.clientHeight < 80) continue;
    const overflow = getComputedStyle(el).overflowY;
    if (overflow === 'auto' || overflow === 'scroll') areas.push(el);
    if (areas.length > 20) break;
  }
  let moved = false;
  for (const el of areas) {
    if (!el) continue;
    const before = el.scrollTop;
    el.scrollTop = before + Math.max(200, el.clientHeight * 0.9);
    if (el.scrollTop !== before) moved = true;
  }
  return moved;
}
"""

_SECRET = re.compile(r"\{secret:([A-Za-z0-9_.-]+)\}")
SECRETS_FILE = "secrets.local.json"


def new_run_token():
    """A short value unique to one run, for data that must not collide with earlier runs."""
    n, digits = int(time.time()) % (36 ** 4), "0123456789abcdefghijklmnopqrstuvwxyz"
    stamp = ""
    for _ in range(4):
        n, r = divmod(n, 36)
        stamp = digits[r] + stamp
    return stamp + "".join(random.choice(digits) for _ in range(2))


def find_secrets_file(start):
    """secrets.local.json beside the tests: next to `start` or up to four folders above."""
    folder = os.path.dirname(os.path.abspath(start)) if start else os.getcwd()
    for _ in range(5):
        path = os.path.join(folder, SECRETS_FILE)
        if os.path.exists(path):
            return path
        folder = os.path.dirname(folder)
    return None


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


_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{20,}|[A-Za-z0-9_-]{24,})$")
_ID_TOKEN = re.compile(r"(?<=[/#]):id(?=$|[/?#])")


def generic_url(rel):
    """A URL an action led to, with record ids in its path or route written as :id --
    a test that creates a record must still match when the app assigns a new id on the
    next run (/companies/55/show -> /companies/:id/show). Query strings are kept."""
    def fold(part):
        path, mark, query = part.partition("?")
        return "/".join(":id" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/")) + mark + query
    path, hashmark, fragment = rel.partition("#")
    return fold(path) + (hashmark + fold(fragment) if hashmark else "")


def url_pattern(expected):
    """Regex matching `expected` (a relative URL or a full URL) on any origin; an `:id`
    segment matches any one segment."""
    rel = relative_url(expected) if "://" in expected else expected
    if not rel.startswith(("/", "#", "?")):
        rel = "/" + rel
    body = r"[^/?#]+".join(re.escape(piece) for piece in _ID_TOKEN.split(rel))
    return re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+" + body + r"$")


class Flow:
    """Executes test steps on a Playwright page. Each method is one step: it acts,
    waits for the declared effect, settles, and reports the step to `checkpoints`
    (the run timeline) when given."""

    def __init__(self, page, base_url=None, checkpoints=None, timeout_ms=10000,
                 settle_quiet_ms=120, settle_max_ms=3000, settle_calm_ms=60, secrets=None, unique=None):
        self.page = page
        self.base_url = base_url
        self.checkpoints = checkpoints
        self.timeout_ms = timeout_ms
        self.settle_quiet_ms = settle_quiet_ms
        self.settle_max_ms = settle_max_ms
        self.settle_calm_ms = settle_calm_ms
        self.secrets = dict(secrets or {})     # values for {secret:NAME} known to this process
        self.unique = unique or new_run_token()
        self.data = {}
        self.last = {}
        self.dialogs = []                      # every dialog seen: {type, message, action}
        self.dialog_decider = None             # authoring only: (type, message) -> "accept" | "dismiss"
        self._dialog_policy = self._dialog_text = None
        self._pages = [page]                   # tab stack; self.page is the top
        self.opened = []                       # pages opened by the app that nobody followed yet
        self._on_page = lambda new: self.opened.append(new)
        try:
            page.context.add_init_script(QUIET_PROBE)
            page.context.on("page", self._on_page)
        except Exception:
            pass
        self._hook(page)
        self._install_probe()

    # ── plumbing ────────────────────────────────────────────────────────────
    def _hook(self, page):
        """Route this page's dialogs to whichever Flow is driving it now (several Flows may
        be created for one page over a session; only the newest decides)."""
        page._qm_flow = self
        if not getattr(page, "_qm_dialog_hook", False):
            page._qm_dialog_hook = True
            page.on("dialog", lambda dialog: page._qm_flow._on_dialog(dialog))

    def _on_dialog(self, dialog):
        policy = self._dialog_policy
        if policy is None and self.dialog_decider is not None:
            try:
                policy = self.dialog_decider(dialog.type, dialog.message)
            except Exception:
                policy = None
        policy = policy if policy in ("accept", "dismiss") else "dismiss"   # Playwright's own default
        self.dialogs.append({"type": dialog.type, "message": dialog.message, "action": policy})
        try:
            if policy == "accept":
                dialog.accept(self._dialog_text) if dialog.type == "prompt" and self._dialog_text is not None \
                    else dialog.accept()
            else:
                dialog.dismiss()
        except Exception:
            pass

    def release(self):
        """Stop listening for new tabs (the page outlives this Flow in a chat session)."""
        try:
            self.page.context.remove_listener("page", self._on_page)
        except Exception:
            pass

    def _install_probe(self):
        try:
            self.page.evaluate(QUIET_PROBE)
        except Exception:
            pass   # mid-navigation: the init script covers the next document

    def _sync_page(self):
        """If the current tab closed itself, carry on in the one that opened it."""
        while len(self._pages) > 1 and self._pages[-1].is_closed():
            self._pages.pop()
        self.page = self._pages[-1]

    def follow(self, new_page):
        """Make a tab the app opened the current page."""
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=max(self.timeout_ms, 15000))
        except Exception:
            pass
        if new_page in self.opened:
            self.opened.remove(new_page)
        self._pages.append(new_page)
        self.page = new_page
        self._hook(new_page)
        self._install_probe()
        self.settle()

    def _target(self, target):
        return self.page.locator(target) if isinstance(target, str) else target

    def _find(self, target):
        """The locator for a target, after scrolling lazily loaded lists until it exists.
        Returns at once when the element is already there."""
        loc = self._target(target)
        try:
            loc.first.wait_for(state="attached", timeout=1500)
            return loc
        except Exception:
            pass
        scrolled, deadline = False, time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:      # one screen at a time, so virtualised lists render every row
            if not self.scroll_more():
                break
            scrolled = True
            if loc.count():
                return loc
        if scrolled and not loc.count():
            raise StepError("the target is not on the page, even after scrolling to the end of its lists")
        return loc    # nothing to scroll: let the action's own wait report it

    def scroll_more(self):
        """Scroll the page and its scrollable areas one screen further; False at the end."""
        try:
            moved = bool(self.page.evaluate(SCROLL_MORE_JS))
        except Exception:
            return False
        if moved:
            self.settle()
        return moved

    @contextlib.contextmanager
    def _step(self, name):
        started = time.monotonic()
        cm = self.checkpoints.step(name) if self.checkpoints is not None else contextlib.nullcontext()
        with cm:
            yield
        self.last = {"name": name, "ms": round((time.monotonic() - started) * 1000), "url": self.page.url}

    @contextlib.contextmanager
    def _action(self, name, dialog=None, dialog_text=None, new_tab=False):
        """One acting step: the dialog policy is armed before it, text shown from now on is
        remembered for later checks, and a tab it opens is followed when `new_tab`."""
        self._sync_page()
        self._dialog_policy, self._dialog_text = dialog, dialog_text
        try:
            self.page.evaluate("() => { if (window.__qmProbe) window.__qmProbe.flash = []; }")
        except Exception:
            pass
        try:
            with self._step(name):
                if new_tab:
                    with self.page.context.expect_page(timeout=self.timeout_ms) as opened:
                        yield
                    self.follow(opened.value)
                else:
                    yield
        finally:
            self._dialog_policy = self._dialog_text = None

    def settle(self):
        """Wait until the DOM is quiet and no fetch/XHR is pending (capped). Returns
        what happened; never raises -- a busy page just reaches the cap."""
        for _ in range(2):
            try:
                return self.page.evaluate(SETTLE_JS, [self.settle_quiet_ms, self.settle_max_ms, self.settle_calm_ms])
            except Exception:
                # The document navigated mid-wait: let the new one parse, then re-check.
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                except Exception:
                    pass
                self._install_probe()
        return {"quiet": False, "ms": 0}

    def working(self):
        """True while any frame still has requests in flight, timers pending or recent changes."""
        for frame in self.page.frames:
            try:
                if frame.evaluate("() => !!window.__qmProbe && window.__qmProbe.working()"):
                    return True
            except Exception:
                continue
        return False

    def _ms(self, timeout):
        return self.timeout_ms if timeout is None else int(float(timeout) * 1000)

    def _effects(self, expect_url=None, expect_visible=None, expect_hidden=None):
        if expect_url:
            expect(self.page).to_have_url(url_pattern(expect_url), timeout=self.timeout_ms)
        if expect_visible is not None:
            expect(self._target(expect_visible)).to_be_visible(timeout=self.timeout_ms)
        if expect_hidden is not None:
            expect(self._target(expect_hidden)).to_be_hidden(timeout=self.timeout_ms)

    # ── test data ───────────────────────────────────────────────────────────
    def use_data(self, data, test_file=None):
        """The test's data with run-time values filled in:
            {unique}        a short value that is new on every run ("QA Vendor {unique}")
            {today}         today's date, YYYY-MM-DD
            {secret:NAME}   from env QAMATE_SECRET_<NAME>, else secrets.local.json beside the tests
        so a test that creates records can run again and passwords stay out of test files."""
        file_secrets = None

        def secret(match):
            nonlocal file_secrets
            name = match.group(1)
            value = os.environ.get("QAMATE_SECRET_" + re.sub(r"\W", "_", name).upper())
            if value is None:
                value = self.secrets.get(name)
            if value is None:
                if file_secrets is None:
                    path = os.environ.get("QAMATE_SECRETS_FILE") or find_secrets_file(test_file)
                    try:
                        with open(path, encoding="utf-8") as f:
                            file_secrets = json.load(f)
                    except Exception:
                        file_secrets = {}
                value = file_secrets.get(name)
            if value is None:
                raise StepError(f"secret {name!r} is not set: add it to {SECRETS_FILE} beside the tests, "
                                f"or set QAMATE_SECRET_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}")
            return str(value)

        def resolve(value):
            if not isinstance(value, str) or "{" not in value:
                return value
            value = value.replace("{unique}", self.unique).replace("{today}", datetime.date.today().isoformat())
            return _SECRET.sub(secret, value)

        self.data = {key: resolve(value) for key, value in (data or {}).items()}
        return self.data

    # ── navigation ──────────────────────────────────────────────────────────
    def goto(self, url, name=None):
        full = urljoin(self.base_url, url) if self.base_url and "://" not in url else url
        with self._action(name or f"Open {url}"):
            self.page.goto(full, wait_until="domcontentloaded", timeout=max(self.timeout_ms, 20000))
            self._install_probe()
            self.settle()

    def close_tab(self, name=None):
        """Close the tab a step opened and continue in the one before it."""
        with self._step(name or "Close the tab"):
            if len(self._pages) > 1:
                closing = self._pages.pop()
                self.page = self._pages[-1]
                try:
                    closing.close()
                except Exception:
                    pass
                self.page.bring_to_front()
                self.settle()

    # ── actions ─────────────────────────────────────────────────────────────
    def click(self, target, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None,
              dialog=None, dialog_text=None, new_tab=False):
        with self._action(name or "Click", dialog, dialog_text, new_tab):
            self._find(target).click(timeout=self.timeout_ms)
            if not new_tab:
                self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()
        if new_tab:
            self._effects(expect_url, expect_visible, expect_hidden)

    def dblclick(self, target, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None,
                 dialog=None, dialog_text=None):
        with self._action(name or "Double-click", dialog, dialog_text):
            self._find(target).dblclick(timeout=self.timeout_ms)
            self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()

    def hover(self, target, name=None, *, expect_visible=None):
        with self._action(name or "Hover"):
            self._find(target).hover(timeout=self.timeout_ms)
            self._effects(expect_visible=expect_visible)
            self.settle()

    def fill(self, target, value, name=None):
        """Replace the field's value. If the app doesn't accept a programmatic fill
        (some masked or controlled inputs), type it for real instead. Either way the
        field must end up showing exactly `value`, live and on replay."""
        value = "" if value is None else str(value)
        with self._action(name or "Fill"):
            loc = self._find(target)
            loc.fill(value, timeout=self.timeout_ms)
            if not self._shows(loc, value):
                loc.click(timeout=self.timeout_ms)
                loc.press("ControlOrMeta+a")
                loc.press_sequentially(value, delay=20)
                if not self._shows(loc, value):
                    raise StepError(f"the field shows {self._editable_value(loc)!r} after entering the value")
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

    def select(self, target, option, name=None, *, dialog=None):
        """Choose an option in a native <select> by its value or visible label."""
        with self._action(name or f"Select {option}", dialog):
            loc = self._find(target)
            try:
                loc.select_option(value=str(option), timeout=self.timeout_ms)
            except Exception:
                loc.select_option(label=str(option), timeout=self.timeout_ms)
            self.settle()

    def check(self, target, checked=True, name=None, *, dialog=None):
        with self._action(name or ("Check" if checked else "Uncheck"), dialog):
            self._find(target).set_checked(bool(checked), timeout=self.timeout_ms)
            self.settle()

    def press(self, key, target=None, name=None, *, expect_url=None, expect_visible=None, expect_hidden=None,
              dialog=None, dialog_text=None):
        with self._action(name or f"Press {key}", dialog, dialog_text):
            if target is None:
                self.page.keyboard.press(key)
            else:
                self._find(target).press(key, timeout=self.timeout_ms)
            self._effects(expect_url, expect_visible, expect_hidden)
            self.settle()

    def upload(self, target, files, name=None):
        with self._action(name or "Upload file"):
            self._find(target).set_input_files(files, timeout=self.timeout_ms)
            self.settle()

    # ── checks (web-first: they retry until true or timeout) ─────────────────
    def expect_url(self, url, name=None, *, timeout=None):
        with self._step(name or f"URL is {url}"):
            self._sync_page()
            expect(self.page).to_have_url(url_pattern(url), timeout=self._ms(timeout))

    def expect_visible(self, target, name=None, *, timeout=None):
        with self._step(name or "Is visible"):
            expect(self._find(target)).to_be_visible(timeout=self._ms(timeout))

    def expect_hidden(self, target, name=None, *, timeout=None):
        with self._step(name or "Is hidden"):
            expect(self._target(target)).to_be_hidden(timeout=self._ms(timeout))

    def expect_text(self, target, text, name=None, exact=False, *, present=True, timeout=None):
        """The element shows `text` -- or, with present=False, does not (it must still exist)."""
        with self._step(name or (f"Shows '{text}'" if present else f"Does not show '{text}'")):
            check = expect(self._find(target))
            if not present:
                check.not_to_contain_text(text, timeout=self._ms(timeout))
            elif exact:
                check.to_have_text(text, timeout=self._ms(timeout))
            else:
                check.to_contain_text(text, timeout=self._ms(timeout))

    def expect_value(self, target, value, name=None, *, timeout=None):
        with self._step(name or f"Value is '{value}'"):
            expect(self._find(target)).to_have_value(str(value), timeout=self._ms(timeout))

    def expect_checked(self, target, checked=True, name=None, *, timeout=None):
        with self._step(name or ("Is checked" if checked else "Is not checked")):
            expect(self._find(target)).to_be_checked(checked=bool(checked), timeout=self._ms(timeout))

    def visible_text(self):
        """Everything the page shows right now (all frames and web components), whitespace collapsed."""
        parts = []
        for frame in self.page.frames:
            try:
                parts.append(frame.evaluate(VISIBLE_TEXT_JS))
            except Exception:
                continue
        return " ".join(parts)

    def page_text(self, text):
        """(shown now, shown since the last action, sample of the page's text) across all
        frames and web components."""
        shown = flashed = False
        sample = ""
        for frame in self.page.frames:
            try:
                state = frame.evaluate(PAGE_TEXT_JS, str(text))
            except Exception:
                continue
            shown, flashed = shown or state["shown"], flashed or state["flashed"]
            sample = sample or state.get("sample") or ""
        return shown, flashed, sample

    def expect_page_text(self, text, present=True, name=None, *, timeout=None):
        """The page shows `text` (anywhere: any frame or web component, or shown since the
        last action and gone again, like a toast) -- or, with present=False, does not."""
        with self._step(name or (f"Page shows '{text}'" if present else f"Page does not show '{text}'")):
            self._sync_page()
            deadline = time.monotonic() + self._ms(timeout) / 1000
            while True:
                shown, flashed, sample = self.page_text(text)
                if (shown or flashed) if present else not shown:
                    return
                if time.monotonic() >= deadline:
                    if present:
                        raise StepError(f"the page does not show {text!r}. It shows: {sample[:400]}")
                    raise StepError(f"the page still shows {text!r}")
                self.page.wait_for_timeout(100)

    def expect_count(self, target, count, name=None, *, timeout=None):
        with self._step(name or f"Count is {count}"):
            expect(self._target(target)).to_have_count(int(count), timeout=self._ms(timeout))
