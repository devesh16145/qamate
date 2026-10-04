"""Fast executor: plain-language steps in, recorded test steps out.

A step ("intent") looks like
    {"do": "click", "target": "Add to cart", "within": "Sauce Labs Bike Light"}
    {"do": "fill", "target": "Username", "value": "standard_user"}
    {"do": "expect_text", "value": "Thank you for your order"}

For each intent the executor observes the page (Playwright AI snapshot), grounds the
target deterministically, and acts through qm_runtime.Flow. Only when grounding has no
clear winner does it ask the decision model (Jev) to choose among a short list. When
nothing fits, it stops that intent with a precise reason and the closest candidates,
so the planner (or a person) can adjust -- it never wanders.

Every executed action is recorded as a step whose replay is identical (qm_steps), plus
the effect seen live (a URL change) as the replay's wait condition.
"""
import re
import time

from qm_decide import choose
from qm_ground import decide, rank, words
from qm_observe import observe
from qm_runtime import Flow, relative_url
from qm_selectors import locator_for
from qm_steps import execute

# Irreversible or outward-facing actions: confirmed before they run.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|destroy|erase|purge|wipe|drop|pay|payment|purchase|send|transfer funds|"
    r"deactivate|revoke|unsubscribe|terminate|close account|cancel (?:my )?(?:account|subscription|order))\b",
    re.I)
ELEMENT_OPS = {"click", "dblclick", "hover", "fill", "select", "check", "upload",
               "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_checked"}


def data_key_for(label, taken):
    base = "_".join(words(label))[:40] or "value"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


class Explorer:
    def __init__(self, page, *, base_url=None, config=None, emit=lambda e: None, confirm=None,
                 timeout_ms=8000, check_timeout_ms=5000):
        self.page, self.base_url, self.config = page, base_url, config or {}
        self.emit, self.confirm = emit, confirm
        self.flow = Flow(page, base_url=base_url, timeout_ms=timeout_ms)
        self.check_timeout_ms = check_timeout_ms
        self.steps, self.data = [], {}
        self.start_url = None      # page the first recorded action happened on
        self._history = {}

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
        try:
            if op == "goto":
                outcome = self._record({"op": "goto", "value": self._url_value(intent["url"]),
                                        "name": intent.get("name") or f"Open {intent['url']}"})
            elif op == "press" and not intent.get("target"):
                outcome = self._record({"op": "press", "value": intent["key"],
                                        "name": intent.get("name") or f"Press {intent['key']}"})
            elif op in ("expect_url", "expect_page_text"):
                outcome = self._check_page(intent)
            elif op in ELEMENT_OPS or op == "press":
                outcome = self._element_intent(intent)
            else:
                outcome = {"ok": False, "reason": "unknown_step", "detail": f"unknown step type {op!r}"}
        except Exception as exc:
            outcome = {"ok": False, "reason": "action_failed", "detail": _short(exc)}
        outcome["ms"] = round((time.monotonic() - t0) * 1000)
        return outcome

    # ── page-level checks ───────────────────────────────────────────────────
    def _check_page(self, intent):
        op = intent["do"]
        if op == "expect_url":
            step = {"op": "expect_url", "value": self._url_value(intent["url"]),
                    "name": intent.get("name") or f"URL is {intent['url']}"}
        else:
            present = intent.get("present", True)
            step = {"op": "expect_page_text", "value": intent["value"], "present": present,
                    "name": intent.get("name") or (f"Page shows '{intent['value']}'" if present
                                                   else f"Page does not show '{intent['value']}'")}
        return self._record(step, check=True)

    # ── element steps ───────────────────────────────────────────────────────
    def _element_intent(self, intent):
        op = intent["do"]
        target = intent.get("target") or ""
        obs = observe(self.page)
        ground = {"op": op, "target": target, "within": intent.get("within"),
                  "role": intent.get("role"), "name_hint": intent.get("name"), "value": intent.get("value")}
        candidates = rank(ground, obs)
        pick, how, confidence = decide(candidates), "match", None
        if pick is None and candidates:
            choice = choose(self.config, ground, candidates[:8], page_title=obs.title,
                            page_path=relative_url(obs.url), emit=self.emit)
            if choice.get("index") is not None and (choice.get("confidence") is None or choice["confidence"] >= 0.6):
                pick, how, confidence = candidates[choice["index"]], choice["by"], choice.get("confidence")
            elif choice.get("error"):
                self.emit({"event": "qm_decision_error", "error": choice["error"]})
        if pick is None:
            return {"ok": False, "reason": "ambiguous" if candidates else "not_found",
                    "detail": f"no element clearly matches {target!r}",
                    "candidates": [c.element.summary() for c in candidates[:5]]}
        element = pick.element
        within = intent.get("within")
        label = intent.get("label") or (f"{target} ({within})" if target and within else target) or element.label
        if op in ("click", "dblclick", "check", "press") and self._destructive(element, intent):
            if not (self.confirm and self.confirm(intent, element.summary())):
                return {"ok": False, "reason": "needs_confirmation",
                        "detail": f"'{element.label}' looks irreversible; confirm before running it",
                        "element": element.summary()}
        loc = locator_for(self.page, element)
        if not loc["unique"]:
            return {"ok": False, "reason": "no_stable_locator", "detail": f"could not pin down {element.summary()}"}
        step = {"op": op, "target": loc["python"], "name": label}
        if op in ("fill", "select", "expect_value", "expect_text"):
            value = intent.get("value", "")
            step["value"] = value
            if op == "fill":
                step["data_key"] = intent.get("data_key") or data_key_for(label, self.data)
        if op in ("check", "expect_checked"):
            step["checked"] = intent.get("checked", True)
        if op == "press":
            step["value"] = intent["key"]
        if op == "select" and not self._is_native_select(loc["selector"]):
            return self._custom_select(intent, step, element, how, confidence, loc)
        outcome = self._record(step, check=op.startswith("expect"))
        outcome.update({"how": how, "confidence": confidence, "element": element.summary(),
                        "positional": loc["positional"]})
        return outcome

    def _custom_select(self, intent, step, element, how, confidence, loc):
        """A non-native dropdown: open it and click the option. Searchable ones (comboboxes
        that list only some suggestions) get the value typed into their search box first.
        Only an option labelled exactly as asked is taken -- never a look-alike such as
        'Create "<value>"', which would add a record instead of choosing one."""
        value = str(intent.get("value", ""))
        opened = self._record({"op": "click", "target": loc["python"], "name": f"Open {step['name']}"})
        if not opened["ok"]:
            return opened
        option, candidates = self._exact_option(value)
        if option is None:
            box = self._focused_search_box(exclude=element)
            if box is not None:
                typed = self._record({"op": "fill", "target": box["python"], "value": value,
                                      "name": f"Search {step['name']} for {value}"})
                if not typed["ok"]:
                    return typed
                option, candidates = self._exact_option(value)
        if option is None:
            return {"ok": False, "reason": "not_found", "detail": f"{step['name']} has no option {value!r}",
                    "candidates": [c.element.summary() for c in candidates[:5]]}
        option_loc = locator_for(self.page, option.element)
        if not option_loc["unique"]:
            return {"ok": False, "reason": "no_stable_locator", "detail": f"could not pin down {option.element.summary()}"}
        outcome = self._record({"op": "click", "target": option_loc["python"], "name": f"Choose {value}"})
        outcome.update({"how": how, "confidence": confidence, "element": option.element.summary()})
        return outcome

    def _exact_option(self, value):
        candidates = rank({"op": "click", "target": value}, observe(self.page))
        exact = [c for c in candidates if c.exact >= 0.95]
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

    # ── execution + recording ───────────────────────────────────────────────
    def _record(self, step, check=False):
        signature = (relative_url(self.page.url), step["op"], step.get("target"), str(step.get("value")))
        self._history[signature] = self._history.get(signature, 0) + 1
        if not check and self._history[signature] > 2:
            return {"ok": False, "reason": "repeating",
                    "detail": f"'{step.get('name')}' already ran twice on this page without moving on"}
        if step.get("data_key"):
            self.data[step["data_key"]] = step["value"]
        before = self.page.url
        if not self.steps and step["op"] != "goto":
            self.start_url = before
        if check:
            saved = self.flow.timeout_ms
            self.flow.timeout_ms = self.check_timeout_ms
        try:
            execute(self.flow, step, self.data)
        except Exception as exc:
            if step.get("data_key") and not any(s.get("data_key") == step["data_key"] for s in self.steps):
                self.data.pop(step["data_key"], None)
            return {"ok": False, "reason": "check_failed" if check else "action_failed",
                    "detail": _short(exc), "step": step}
        finally:
            if check:
                self.flow.timeout_ms = saved
        if step["op"] in ("click", "dblclick", "press") and self.page.url != before:
            step["expect_url"] = relative_url(self.page.url)
        self.steps.append(step)
        return {"ok": True, "step": step, "url": self.page.url}

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

    @staticmethod
    def _destructive(element, intent):
        if intent.get("safe") is True:
            return False
        if intent.get("destructive") is True:
            return True
        return bool(DESTRUCTIVE.search(f"{element.label} {intent.get('target', '')}"))


def _short(exc):
    return str(exc).split("\nCall log:")[0].strip()[:400]
