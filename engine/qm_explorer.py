"""Fast executor: plain-language steps in, recorded test steps out.

A step ("intent") looks like
    {"do": "click", "target": "Add to cart", "within": "Sauce Labs Bike Light"}
    {"do": "fill", "target": "Vendor name", "value": "QA Vendor {unique}"}
    {"do": "expect_text", "value": "Scheduled", "within": "QA Vendor {unique}"}

For each intent the executor observes the page (Playwright AI snapshot, completed from
the page for controls it leaves nameless), grounds the target -- exact name or ask, see
qm_ground -- and acts through qm_runtime.Flow. When the target is not among the visible
elements it scrolls lazily loaded lists and opens the menu that hides it before giving
up. When nothing fits it stops that intent with a precise reason and the closest
candidates, so the planner (or a person) can adjust -- it never wanders.

Every executed action is recorded as a step whose replay is identical (qm_steps), with
what was seen live as the replay's conditions: where a click led, the dialog it raised
and how it was answered, the tab it opened, how long a slow result took. Typed values
are kept as test data and followed through later steps, so a test reads its data
instead of repeating it; `{unique}` values differ on every run and secrets never reach
the test files.

Checks are audited as they are recorded: one that was already true before anything was
done on the page cannot fail, and is reported as such.
"""
import re
import time
from urllib.parse import urljoin

from qm_decide import choose
from qm_ground import _norm, decide, rank, reading_text, shown_text, words
from qm_map import route_of
from qm_observe import observe
from qm_runtime import _SECRET, Flow, generic_url, relative_url
from qm_selectors import anchored_locator, locator_for, scoped_locator
from qm_steps import execute, parameterize, usable_literals

# Irreversible or outward-facing actions: confirmed before they run.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|destroy|erase|purge|wipe|drop|pay|payment|purchase|send|transfer funds|"
    r"deactivate|revoke|unsubscribe|terminate|close account|cancel (?:my )?(?:account|subscription|order))\b",
    re.I)
SECRET_FIELD = re.compile(r"\b(pass(?:word|code|phrase)?|secret|api[ _-]?key|access[ _-]?token|pin|otp|cvv|cvc)\b", re.I)
ELEMENT_OPS = {"click", "dblclick", "hover", "fill", "select", "check", "upload",
               "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_checked"}
DIALOG_OPS = {"click", "dblclick", "press", "select", "check"}
PART_ROLES = {"spinbutton", "textbox", "searchbox"}     # what a segmented field is made of
SCOPE_ROLES = {"row", "listitem", "article", "group", "region", "dialog", "alertdialog", "form", "link"}
MAX_STEPS = 200
MAX_CHECK_WAIT_S = 20        # how long a check may keep waiting while the page is still working
SCROLL_SEARCH_S = 8          # how long to scroll lazily loaded lists looking for a target
_VOLATILE = re.compile(r"\s\[(?:ref=[^\]]*|active|cursor=pointer)\]")

# A target that exists in the page but is not shown (a menu that opens on hover or click):
# the visible control to hover or click, or null.
HIDDEN_TARGET_JS = r"""
(target) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const want = norm(target);
  if (!want) return null;
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const nodes = document.querySelectorAll('a, button, [role=menuitem], [role=option], [role=tab], [role=link], [role=button], li, label, summary');
  for (const el of nodes) {
    if (shown(el) || norm(el.innerText || el.textContent) !== want) continue;
    let box = el.parentElement;
    while (box && !shown(box)) box = box.parentElement;
    if (!box || box === document.body || box === document.documentElement) continue;
    const controls = [...box.querySelectorAll('button, a, summary, [role=button], [aria-haspopup], [aria-expanded]')]
      .filter((c) => shown(c) && !c.contains(el));
    const trigger = controls[0] || box;
    const label = (trigger.getAttribute('aria-label') || trigger.innerText || trigger.textContent || '').replace(/\s+/g, ' ').trim();
    if (!label || label.length > 60) continue;
    return { trigger: label, expandable: trigger.tagName === 'SUMMARY' || trigger.hasAttribute('aria-haspopup') ||
                                         trigger.getAttribute('aria-expanded') === 'false' };
  }
  return null;
}
"""


def data_key_for(label, taken):
    base = "_".join(words(label))[:40] or "value"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


class Explorer:
    def __init__(self, page, *, base_url=None, config=None, emit=lambda e: None, confirm=None,
                 timeout_ms=8000, check_timeout_ms=5000, page_map=None, secrets=None):
        self.base_url, self.config = base_url, config or {}
        self.emit, self.confirm = emit, confirm
        self.page_map = page_map   # qm_map.PageMap: learns pages and where clicks lead
        self.secrets = dict(secrets or {})     # NAME -> value for {secret:NAME}; never written to test files
        self.flow = Flow(page, base_url=base_url, timeout_ms=timeout_ms, secrets=self.secrets)
        self.flow.dialog_decider = self._decide_dialog
        self.check_timeout_ms = check_timeout_ms
        self.steps = []
        self.data = {}             # what the test's data file stores: typed values, {unique} and {secret:..} kept as written
        self.start_url = None      # page the first recorded action happened on
        self.notes = []            # what a reviewer should know: weak checks, position-based locators, dialogs
        self._history = {}
        self._first_page = page
        self._route, self._states = None, []   # what the page showed before each action since arriving on it
        self._dialog_choice = None
        self._destructive_ok = False
        self._blocked_dialog = None

    @property
    def page(self):
        """The tab being driven (it changes when a step opens a new tab)."""
        return self.flow.page

    def cleanup(self):
        """Close tabs the task opened and stop listening; the session's page stays."""
        while len(self.flow._pages) > 1:
            try:
                self.flow.close_tab()
            except Exception:
                break
        self.flow.release()

    # ── public ──────────────────────────────────────────────────────────────
    def run(self, intents):
        """Execute intents in order; stop at the first that can't be done.
        Returns {"ok", "done": n, "stopped": outcome | None, "ms"}."""
        started = time.monotonic()
        for index, intent in enumerate(intents):
            outcome = self.run_intent(intent)
            self.emit({"event": "qm_step", "index": index, "intent": intent, **outcome})
            if not outcome["ok"]:
                return {"ok": False, "done": index, "stopped": {"index": index, "intent": intent, **outcome},
                        "ms": round((time.monotonic() - started) * 1000)}
        return {"ok": True, "done": len(intents), "stopped": None, "ms": round((time.monotonic() - started) * 1000)}

    def run_intent(self, intent):
        t0 = time.monotonic()
        op = intent.get("do")
        live = {k: self._live(v) for k, v in intent.items()}    # run-time tokens filled in
        try:
            if op == "goto":
                url = live["url"]
                if "://" not in url and not self.base_url and self.page.url.startswith("http"):
                    url = urljoin(self.page.url, url)   # no project URL: the test must still open it
                outcome = self._record({"op": "goto", "value": self._url_value(url),
                                        "name": intent.get("name") or f"Open {intent['url']}"})
            elif op == "close_tab":
                outcome = self._record({"op": "close_tab", "name": intent.get("name") or "Close the tab"})
            elif op == "press" and not intent.get("target"):
                outcome = self._record({"op": "press", "value": live["key"],
                                        "name": intent.get("name") or f"Press {intent['key']}"})
            elif op in ("expect_url", "expect_page_text"):
                outcome = self._check_page(intent, live)
            elif op in ELEMENT_OPS or op == "press":
                outcome = self._element_intent(intent, live)
            else:
                outcome = {"ok": False, "reason": "unknown_step", "detail": f"unknown step type {op!r}"}
        except Exception as exc:
            outcome = {"ok": False, "reason": "action_failed", "detail": _short(exc)}
        outcome["ms"] = round((time.monotonic() - t0) * 1000)
        return outcome

    # ── test data ───────────────────────────────────────────────────────────
    def _live(self, text):
        """Planner text with run-time tokens filled in for this authoring run."""
        if not isinstance(text, str) or "{" not in text:
            return text
        text = text.replace("{unique}", self.flow.unique)
        return _SECRET.sub(lambda m: str(self.secrets.get(m.group(1), m.group(0))), text)

    def _refresh_data(self):
        self.flow.secrets = self.secrets
        self.flow.use_data(self.data)

    def _literals(self):
        """Typed values to follow through later steps (never secrets)."""
        public = {k: v for k, v in self.flow.data.items() if "{secret:" not in str(self.data.get(k, ""))}
        return usable_literals(public, always={k for k, v in self.data.items() if "{unique}" in str(v)})

    # ── page-level checks ───────────────────────────────────────────────────
    def _check_page(self, intent, live):
        op = intent["do"]
        if op == "expect_url":
            step = {"op": "expect_url", "value": self._url_value(live["url"]),
                    "name": intent.get("name") or f"URL is {intent['url']}"}
            return self._record(step, check=True)
        if intent.get("within"):
            return self._scoped_text_check(intent, live)
        present = intent.get("present", True)
        step = {"op": "expect_page_text", "value": live["value"], "present": present,
                "name": intent.get("name") or (f"Page shows '{intent['value']}'" if present
                                               else f"Page does not show '{intent['value']}'")}
        return self._record(step, check=True)

    def _scoped_text_check(self, intent, live):
        """'<value> is shown for <within>': the text must be in the row, card or section
        that names the item -- not merely somewhere on the page."""
        within, value, present = live["within"], live["value"], intent.get("present", True)
        want = set(words(within))
        obs = observe(self.page)
        best = None
        for el in obs.elements:
            if el.role in SCOPE_ROLES and not el.attrs.get("aria-hidden") and want <= set(words(shown_text(el, limit=80))):
                if best is None or el.depth >= best.depth:     # the smallest thing that names the item
                    best = el
        if best is None:
            return self._adjacent_text_check(intent, within, value, present, obs)
        loc = anchored_locator(self.page, best, within) or locator_for(self.page, best)
        if not loc["unique"]:
            return {"ok": False, "reason": "no_stable_locator", "detail": f"could not pin down {best.summary()}"}
        step = {"op": "expect_text", "target": loc["python"], "value": value, "present": present,
                "name": intent.get("name") or (f"{intent['within']} shows '{intent['value']}'" if present
                                               else f"{intent['within']} does not show '{intent['value']}'")}
        outcome = self._record(step, check=True, element=best)
        outcome["element"] = best.summary()
        return outcome

    def _adjacent_text_check(self, intent, within, value, present, obs):
        """No row, card or section is named `within` -- it is a plain label beside the value
        ("SUBTOTAL  $ 999.00"). Check the two together, as one phrase in reading order: that
        ties the value to its label without needing a locator for an unnamed box."""
        phrase = None
        if present:
            low_within, low_value = " ".join(within.split()).casefold(), " ".join(value.split()).casefold()
            for el in sorted(obs.elements, key=lambda e: -e.depth):          # smallest boxes first
                text = reading_text(el)
                low = text.casefold()
                i, j = low.find(low_within), low.find(low_value)
                if i < 0 or j < 0:
                    continue
                start, end = min(i, j), max(i + len(low_within), j + len(low_value))
                if end - start <= len(low_within) + len(low_value) + 40:     # side by side, not merely on the same page
                    phrase = text[start:end]
                    break
        if phrase is None:
            return {"ok": False, "reason": "not_found",
                    "detail": (f"no row, card or section on the page is named {within!r}"
                               + ("" if not present else f", and {value!r} is not shown beside it"))}
        step = {"op": "expect_page_text", "value": phrase, "present": True,
                "name": intent.get("name") or f"{intent['within']} shows '{intent['value']}'"}
        return self._record(step, check=True)

    # ── element steps ───────────────────────────────────────────────────────
    def _element_intent(self, intent, live, reveal=True):
        op = intent["do"]
        target = live.get("target") or ""
        ground = {"op": op, "target": target, "within": live.get("within"),
                  "role": intent.get("role"), "name_hint": live.get("name"), "value": live.get("value")}
        obs, candidates = self._look(ground)
        pick, confidence = decide(candidates, ground), None
        composite = False
        if pick is None and op == "fill":
            # No field is named this, but a group of fields may be: a date entered as month,
            # day and year, a code with a box per digit. It is typed into as one field.
            groups = [c for c in candidates if c.match != "partial" and c.tier is None
                      and 0 < sum(d.role in PART_ROLES for d in _descendants(c.element)) <= 12]
            # the group itself, not a section that happens to contain it: the deepest, a real group first
            pick = max(groups, key=lambda c: (c.element.role in ("group", "radiogroup"), c.element.depth), default=None)
            composite = pick is not None
        if pick is None and reveal and not any(c.exact for c in candidates) and self._reveal(ground):
            obs, candidates = self._look(ground)
            pick = decide(candidates, ground)
        how = "match" if pick is None or pick.match == "exact" else pick.match   # decorated | synonym
        if pick is None and candidates:
            choice = choose(self.config, ground, candidates[:8], page_title=obs.title,
                            page_path=relative_url(obs.url), emit=self.emit)
            if choice.get("index") is not None and (choice.get("confidence") is None or choice["confidence"] >= 0.6):
                pick, how, confidence = candidates[choice["index"]], choice["by"], choice.get("confidence")
            elif choice.get("error"):
                self.emit({"event": "qm_decision_error", "error": choice["error"]})
        if pick is None:
            real = [c for c in candidates if c.exact]
            return {"ok": False, "reason": "ambiguous" if real else "not_found",
                    "detail": (f"{len(real)} elements are named {target!r}; say which one with \"within\""
                               if real else f"nothing on the page is named {target!r}"),
                    "candidates": [c.element.summary() for c in candidates[:5]]}
        element = pick.element
        within = intent.get("within")
        raw_target = intent.get("target") or ""
        label = intent.get("label") or (f"{raw_target} ({within})" if raw_target and within else raw_target) or element.label
        self._dialog_choice = intent.get("dialog") if intent.get("dialog") in ("accept", "dismiss") else None
        self._destructive_ok = intent.get("safe") is True
        if op in ("click", "dblclick", "check", "press") and self._destructive(element, intent):
            if not (self.confirm and self.confirm(intent, element.summary())):
                return {"ok": False, "reason": "needs_confirmation",
                        "detail": f"'{element.label}' looks irreversible; confirm before running it",
                        "element": element.summary()}
            self._destructive_ok = True
        loc = locator_for(self.page, element)
        if not loc["unique"] and pick.group and pick.group != element.ref:
            # Text inside a clickable card/button that can't be addressed on its own: use the
            # card/button itself (it is what receives the click anyway).
            container = obs.by_ref(pick.group)
            if container is not None:
                loc = locator_for(self.page, container)
        if loc["positional"] or not loc["unique"]:
            # A repeated control: find its item by what the item says, not by its position.
            anchors = [live.get("within")] + [part for part in (element.context or "").split(" · ")]
            loc = scoped_locator(self.page, element, anchors) or loc
        if not loc["unique"]:
            return {"ok": False, "reason": "no_stable_locator", "detail": f"could not pin down {element.summary()}"}
        step = {"op": "type" if composite else op, "target": loc["python"], "name": label}
        data = None
        if op in ("fill", "select", "expect_value", "expect_text"):
            step["value"] = live.get("value", "")
            if op == "fill":
                key = intent.get("data_key") or data_key_for(raw_target or label, self.data)
                stored = str(intent.get("value", ""))        # as written: "QA Vendor {unique}", "{secret:password}"
                if "{secret:" not in stored and self._secret_field(loc, element):
                    self.secrets[key] = step["value"]
                    stored = "{secret:%s}" % key
                step["data_key"] = key
                if "{secret:" in stored:
                    step["secret"] = True
                data = (key, stored)
        if op == "expect_text" and intent.get("present") is False:
            step["present"] = False
        if op in ("check", "expect_checked"):
            step["checked"] = intent.get("checked", True)
        if op == "press":
            step["value"] = live["key"]
        if intent.get("dialog_text") is not None and op in ("click", "dblclick", "press"):
            step["dialog"], step["dialog_text"] = "accept", str(live["dialog_text"])
        if op == "select" and not self._is_native_select(loc["selector"]):
            return self._custom_select(intent, live, step, element, how, confidence, loc)
        before = self.page.url
        self._blocked_dialog = None
        outcome = self._record(step, check=op.startswith("expect"), state=self._state(obs), data=data,
                               obs=obs, element=element)
        if outcome["ok"] and self._blocked_dialog:
            # The app asked something irreversible-sounding and was answered "no": undo the
            # recording and ask the user; with a yes, do it again and answer "yes".
            message, self._blocked_dialog = self._blocked_dialog, None
            self.steps.pop()
            if self.confirm and self.confirm(intent, f"{element.summary()} -- the app asks: {message!r}"):
                return self._element_intent({**intent, "dialog": "accept", "safe": True}, live, reveal=False)
            return {"ok": False, "reason": "needs_confirmation",
                    "detail": f"the app asks {message!r}; confirm before answering yes", "element": element.summary()}
        if outcome["ok"] and self.page_map is not None and self.page.url != before:
            self.page_map.went(before, element.label, self.page.url)   # the real label, not the planner's wording
        if outcome["ok"] and loc["positional"]:
            self.notes.append(f"'{label}' is found by its position on the page; it will break if the order changes")
        outcome.update({"how": how, "confidence": confidence, "element": element.summary(),
                        "positional": loc["positional"]})
        return outcome

    def _look(self, ground, patience_ms=1500):
        """Observe and rank. While nothing fits well AND the page is still changing (a route
        rendering, a list loading), look again briefly -- that is far cheaper than asking the
        planner. A page that has gone quiet without the target fails at once."""
        deadline = time.monotonic() + patience_ms / 1000
        while True:
            obs = observe(self.page)
            if self.page_map is not None:
                self.page_map.see(obs)
            candidates = rank(ground, obs)
            if (candidates and candidates[0].exact) or time.monotonic() >= deadline or not self._settling():
                return obs, candidates
            self.page.wait_for_timeout(200)

    def _settling(self):
        """True while the page has pending requests/timers or changed in the last second."""
        try:
            return bool(self.page.evaluate("""() => { const p = window.__qmProbe; if (!p) return false;
                return p.busy() > 0 || (p.timers && p.timers.size > 0) || performance.now() - p.last < 1000; }"""))
        except Exception:
            return True   # mid-navigation

    def _reveal(self, ground):
        """The target is not among the visible elements. Before giving up: scroll lazily
        loaded lists until it appears, then look for it hidden in the page -- a menu that
        opens on hover or click -- and open that (recorded as its own step)."""
        if ground["op"] == "expect_hidden":
            return False
        deadline = time.monotonic() + SCROLL_SEARCH_S
        while time.monotonic() < deadline:      # one screen at a time, so virtualised lists render every row
            if not self.flow.scroll_more():
                break
            candidates = rank(ground, observe(self.page))
            if candidates and candidates[0].exact:
                return True
        try:
            hidden = self.page.evaluate(HIDDEN_TARGET_JS, ground["target"])
        except Exception:
            hidden = None
        if not hidden or DESTRUCTIVE.search(hidden["trigger"]):
            return False
        for op in (["click"] if hidden["expandable"] else ["hover", "click"]):
            opened = self._element_intent({"do": op, "target": hidden["trigger"], "label": f"Open {hidden['trigger']}"},
                                          {"do": op, "target": hidden["trigger"]}, reveal=False)
            if not opened["ok"]:
                return False
            candidates = rank(ground, observe(self.page))
            if candidates and candidates[0].exact:
                return True
            self.steps.pop()        # it didn't show the target: not part of the test
        return False

    def _custom_select(self, intent, live, step, element, how, confidence, loc):
        """A non-native dropdown: open it and click the option. Searchable ones (comboboxes
        that list only some suggestions) get the value typed into their search box first.
        Only an option labelled exactly as asked is taken -- never a look-alike such as
        'Create "<value>"', which would add a record instead of choosing one."""
        value = str(live.get("value", ""))
        opened = self._record({"op": "click", "target": loc["python"], "name": f"Open {step['name']}"})
        if not opened["ok"]:
            return opened
        option, candidates = self._exact_option(value)
        if option is None:
            box = self._focused_search_box(exclude=element)
            if box is not None:
                typed = self._record({"op": "fill", "target": box["python"], "value": value,
                                      "name": f"Search {step['name']} for {intent.get('value')}"})
                if not typed["ok"]:
                    return typed
                option, candidates = self._exact_option(value)
        if option is None:
            return {"ok": False, "reason": "not_found", "detail": f"{step['name']} has no option {value!r}",
                    "candidates": [c.element.summary() for c in candidates[:5]]}
        option_loc = locator_for(self.page, option.element)
        if not option_loc["unique"]:
            return {"ok": False, "reason": "no_stable_locator", "detail": f"could not pin down {option.element.summary()}"}
        outcome = self._record({"op": "click", "target": option_loc["python"], "name": f"Choose {intent.get('value')}"})
        outcome.update({"how": how, "confidence": confidence, "element": option.element.summary()})
        return outcome

    def _exact_option(self, value):
        candidates = rank({"op": "click", "target": value}, observe(self.page))
        # A value is chosen literally: its exact label (counts aside), never a synonym.
        exact = [c for c in candidates if c.match in ("exact", "decorated") and c.tier is not None]
        best = max(exact, key=lambda c: (c.exact, c.element.role == "option", c.score), default=None)
        if best is not None and sum(1 for c in exact if c.exact == best.exact and c.element.role == best.element.role) > 1:
            best = None   # two options with the same label: let the planner disambiguate
        return best, candidates

    def _focused_search_box(self, exclude=None):
        """The text box a just-opened dropdown put the cursor in (its filter/search field)."""
        obs = observe(self.page)
        for el in obs.elements:
            if el.attrs.get("active") and el.role in ("combobox", "searchbox", "textbox"):
                if exclude is not None and el.ref == exclude.ref and el.role != "combobox":
                    return None
                loc = locator_for(self.page, el)
                return loc if loc["unique"] else None
        return None

    # ── dialogs ─────────────────────────────────────────────────────────────
    def _decide_dialog(self, kind, message):
        """How to answer a browser dialog the step raised. alert() has only OK. A question is
        answered yes -- going ahead is what the step asked for -- unless it sounds
        irreversible and the user hasn't agreed to that: then no, and they are asked."""
        if self._dialog_choice:
            return self._dialog_choice
        if kind in ("alert", "beforeunload"):
            return "accept"
        if DESTRUCTIVE.search(message or "") and not self._destructive_ok:
            self._blocked_dialog = message or "(no message)"
            return "dismiss"
        return "accept"

    # ── execution + recording ───────────────────────────────────────────────
    def _state(self, obs=None):
        """A fingerprint of what the page shows now (URL + accessibility tree, typed values
        included; element refs and focus markers excluded)."""
        try:
            text = obs.text if obs is not None else self.page.aria_snapshot(mode="ai", timeout=3000)
        except Exception:
            return None
        return hash((relative_url(self.page.url), _VOLATILE.sub("", text)))

    def _stuck(self, step, state):
        """True when this exact action has already been tried twice on a page that looked
        exactly like this -- i.e. it changes nothing. Repeating an action that does change
        the page (Next page, Add row, a second Save after fixing a field) is fine."""
        signature = (step["op"], step.get("target"), str(step.get("value")))
        last = self._history.get(signature)
        if state is None or last is None or last["state"] != state:
            self._history[signature] = {"state": state, "stale": 0}
            return False
        last["stale"] += 1
        return last["stale"] >= 2

    def _remember(self, obs=None):
        """Keep what the page shows before an action, per page visited, for auditing checks."""
        route = route_of(self.page.url)
        if route != self._route:
            self._route, self._states = route, []
        try:
            text = self.flow.visible_text()
        except Exception:
            text = None
        self._states.append({"text": text, "obs": obs, "url": relative_url(self.page.url)})

    def _weak(self, step, element=None):
        """Why this (passing) check verifies nothing the test did, or None: it was just as true
        before every action taken on this page, so no step here is what made it true. Such a
        check only confirms what was already there (often page furniture: a menu, a filter
        name); it would not notice if the steps before it stopped working."""
        if route_of(self.page.url) != self._route or not self._states:
            return None            # nothing has been done on this page yet: the check verifies arriving here
        op, value = step["op"], " ".join(str(step.get("value") or "").split())
        if op == "expect_page_text":
            if any(s["text"] is None for s in self._states):
                return None
            present = step.get("present", True)
            if all((value in s["text"]) == present for s in self._states):
                return ("the text was already on the page before the steps on this page" if present
                        else "the text was never on the page")
            return None
        if op == "expect_url":
            if all(s["url"] == relative_url(self.page.url) for s in self._states):
                return "the address did not change"
            return None
        if element is None or any(s["obs"] is None for s in self._states):
            return None
        twins = [next((e for e in s["obs"].elements if e.role == element.role and _norm(e.label) == _norm(element.label)), None)
                 for s in self._states]
        if any(t is None for t in twins):
            return None
        if op == "expect_visible":
            return "it was already visible before the steps on this page"
        if op == "expect_value" and all(" ".join((t.inline or "").split()) == value for t in twins):
            return "the field already held this value before the steps on this page"
        if op == "expect_text":
            present = step.get("present", True)
            if all((_norm(value) in _norm(shown_text(t, limit=80))) == present for t in twins):
                return "it already showed this before the steps on this page" if present else "it never showed this"
        return None

    def _record(self, step, check=False, state=None, data=None, obs=None, element=None):
        if len(self.steps) >= MAX_STEPS:
            return {"ok": False, "reason": "too_long", "detail": f"the test already has {MAX_STEPS} steps"}
        if not check and self._stuck(step, state if state is not None else self._state(obs)):
            return {"ok": False, "reason": "repeating",
                    "detail": f"'{step.get('name')}' was already tried twice here and the page did not change"}
        fresh_key = data is not None and data[0] not in self.data
        if data is not None:
            self.data[data[0]] = data[1]
            self._refresh_data()
        before = self.page.url
        if not self.steps and step["op"] != "goto":
            self.start_url = before
        if not check:
            self._remember(obs)
        seen_dialogs = len(self.flow.dialogs)
        self.flow.opened.clear()
        saved = self.flow.timeout_ms
        if check:
            self.flow.timeout_ms = self.check_timeout_ms
        started = time.monotonic()
        try:
            while True:
                try:
                    execute(self.flow, step, self.flow.data)
                    break
                except Exception:
                    # A check that isn't true yet keeps waiting while the page is still working
                    # on something (a request in flight, a slow timer): reports, imports, uploads.
                    if not check or time.monotonic() - started >= MAX_CHECK_WAIT_S or not self.flow.working():
                        raise
        except Exception as exc:
            if fresh_key:
                self.data.pop(data[0], None)
                self._refresh_data()
            return {"ok": False, "reason": "check_failed" if check else "action_failed",
                    "detail": _short(exc), "step": step}
        finally:
            self.flow.timeout_ms = saved
        waited = time.monotonic() - started
        if check and waited > 6:          # slower than a replay would wait by default: give it room
            step["timeout"] = int(waited * 1.5) + 5
        dialogs = self.flow.dialogs[seen_dialogs:]
        if dialogs and step["op"] in DIALOG_OPS:
            step.setdefault("dialog", dialogs[-1]["action"])
            self.notes.append(f"'{step.get('name')}' raised a browser dialog ({dialogs[-1]['message']!r}); "
                              f"answered {'OK' if dialogs[-1]['action'] == 'accept' else 'Cancel'}")
        if self.flow.opened and step["op"] == "click":     # the click opened a new tab: carry on there
            self.flow.follow(self.flow.opened[-1])
            step["new_tab"] = True
        if step["op"] in ("click", "dblclick", "press") and self.page.url != before:
            step["expect_url"] = generic_url(relative_url(self.page.url))   # /companies/:id/show
        outcome = {"ok": True, "step": step, "url": self.page.url}
        if check:
            reason = self._weak(step, element)
            if reason:
                step["weak"] = reason
                outcome["weak"] = reason
                self.notes.append(f"Check '{step.get('name')}' verifies no change: {reason}")
        parameterize(step, self._literals())
        self.steps.append(step)
        return outcome

    # ── helpers ─────────────────────────────────────────────────────────────
    def _url_value(self, url):
        if self.base_url and url.startswith(self.base_url.rstrip("/")):
            return relative_url(url)
        return url

    def _is_native_select(self, selector):
        try:
            return self.page.locator(selector).evaluate("el => el.tagName === 'SELECT'")
        except Exception:
            return True

    def _secret_field(self, loc, element):
        """A password box, or a field whose label says it holds a secret."""
        if SECRET_FIELD.search(element.label or ""):
            return True
        try:
            return self.page.locator(loc["selector"]).evaluate("el => el.type === 'password'")
        except Exception:
            return False

    @staticmethod
    def _destructive(element, intent):
        if intent.get("safe") is True:
            return False
        if intent.get("destructive") is True:
            return True
        return bool(DESTRUCTIVE.search(f"{element.label} {intent.get('target', '')}"))


def _descendants(element, limit=40):
    out, stack = [], list(element.children)
    while stack and len(out) < limit:
        node = stack.pop()
        out.append(node)
        stack.extend(node.children)
    return out


_BLOCKERS = (("intercepts pointer events", "something else is on top of it: "),
             ("element is not visible", "it is on the page but not visible"),
             ("element is not enabled", "it is disabled"),
             ("element is outside of the viewport", "it cannot be scrolled into view"),
             ("element is not stable", "it keeps moving"))


def _short(exc):
    """Playwright's error, plus -- when the action timed out waiting on the element -- what
    it was waiting for, from the call log. "Timeout exceeded" alone tells a planner nothing;
    "a panel is on top of it" tells it to close the panel."""
    text = str(exc)
    head, _, log = text.partition("\nCall log:")
    head = head.strip()[:400]
    for marker, meaning in _BLOCKERS:
        line = next((l.strip(" -") for l in log.splitlines() if marker in l), None)
        if line:
            covering = line.split(marker)[0].strip() if marker == "intercepts pointer events" else ""
            return (head + " -- " + meaning + covering)[:500]
    return head
