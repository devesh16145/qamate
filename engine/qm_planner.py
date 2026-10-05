"""Planner for the fast explorer: task + current page -> a few plain-language steps.

The planner (any configured chat model; MiMo by default) is called rarely: once to
plan, again only when the executor reports a surprise (a step it could not do), and
it sees a compact page summary -- element lines and visible text -- instead of a large
tool list and full DOM dumps. It plans only as far as it can see; after those steps run
it is shown the new page.
"""
import json
import queue
import re
import threading
import time

from llm import make_provider, extract_json, LLMError
from qm_runtime import relative_url

SYSTEM = """You plan the steps of a browser test for QAmate, a test-automation tool.
You get the task, test data, the CURRENT page (its interactive elements and visible text) and,
when the app has been seen before, "known_pages": its other pages with their exact control labels
("label -> route" says where a control led; "[open menu: X] ..." lists controls that only appear
after clicking X -- a menu, tab or section -- so plan the click on X first).
Reply with ONLY a JSON object, keys in this order:
{"test": {"flow": "<app area this test covers, one or two words taken from the task>", "title": "<short test title>"},
 "steps": [STEP, ...], "done": false}

Each STEP is one of:
  {"do": "goto", "url": "<path or URL>"}            only the app's start URL or a link you can see
  {"do": "click", "target": "<label>", "within": "<item name, only if the label repeats>"}
  {"do": "dblclick", "target": "<label>"}           also "rightclick" (opens a context menu)
  {"do": "fill", "target": "<field label>", "value": "<text>"}
  {"do": "select", "target": "<dropdown or option-group label>", "value": "<option text>"}
  {"do": "check", "target": "<checkbox label>", "checked": true}
  {"do": "press", "key": "Enter", "target": "<field label, optional>"}
  {"do": "hover", "target": "<label>"}              only when something appears on hover
  {"do": "drag", "target": "<what to drag>", "to": "<where to drop it: a column, a list, an item>"}
  {"do": "upload", "target": "<file field, or the button that opens the file chooser>"}
  {"do": "wait", "seconds": 2}                      only when the task itself says to wait
  {"do": "close_tab"}                               return from a tab that a click opened
  {"do": "expect_text", "value": "<text>", "within": "<row, card or section it belongs to>"}
  {"do": "expect_text", "value": "<text that must be visible>"}          anywhere on the page
  {"do": "expect_text", "value": "<text>", "present": false}             must NOT be visible
  {"do": "expect_value", "target": "<field label>", "value": "<text>"}
  {"do": "expect_visible", "target": "<label>"}
  {"do": "expect_url", "url": "<path, no query string>"}                 only when certain of the path

Rules:
- Plan the WHOLE task in this reply whenever you can predict it, including pages you haven't
  seen yet -- for those, write the labels a user would see. Each extra round trip costs time.
  If a step doesn't match the real page, execution stops there and you'll be shown that page
  with the closest elements, so you can adjust the rest.
- Use labels exactly as they appear: on the current page from its elements, on other pages from
  known_pages. A target must be the control's own name, not a description of it.
- When several elements share a label (e.g. "Add to cart" on every product), say which one
  with "within": the item's name, row text or section. Only when the items themselves are
  identical (marked "[x4, identical]") add "nth": 1 for the first, 2 for the second, ...
- Set "done": true when your steps complete the whole task, including its checks. Use
  "done": false only if you genuinely need to see a page before planning further. "done" is a
  key of the reply, never a step.
- Check every outcome the task asks for, and make each check able to fail:
  * check what the action changed -- the saved record, the new row, the message -- not text
    that is on the page anyway (menus, filter names, column headings);
  * say where the text belongs with "within" whenever it is about one item (a row, a card);
  * prefer the lasting result over a message that disappears;
  * never assert times, dates or generated ids exactly.
- Values come from the task or the test data; never invent credentials. A value written
  {secret:NAME} in the test data is used exactly like that -- QAmate fills in the real one.
- When the test CREATES something the app keeps (a record's name, an email, a code), put
  {unique} in that value -- "QA Vendor {unique}" -- and use the same text wherever you refer to
  it later, so the test can run again without colliding with its own earlier data. Don't add
  it to values the task says must be exact, or to things the test only reads.
- A browser pop-up (confirm/alert) raised by a step is answered OK automatically; add
  "dialog": "dismiss" to the step to answer Cancel, or "dialog_text": "<text>" for a prompt.
- A click that saves a file (Export, Download): add "download": true to it; the test then
  also checks that the file arrives. An upload sends a small sample image unless the task
  names a file: then add "value": "<its path>".
- If you get a "problem" (a step that could not run), change your approach -- don't repeat
  the same step. The problem lists the closest matching elements.
- If the task cannot be done, reply {"steps": [], "done": true, "blocked": "<why>"}.
"""

MAX_ELEMENT_LINES = 120
MAX_TEXT_LINES = 60
_CHROME = ("navigation", "banner", "contentinfo", "menubar")
_FIELDS = ("textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio", "switch", "listbox")


def page_summary(observation):
    """What the planner is shown of a page: its controls and headings, then its text.
    When a page has more than fits, what a task works with is kept first -- fields, buttons,
    dialogs, messages, then links in the content -- and site navigation and footer links are
    cut back to a sample."""
    entries, seen = [], {}
    for order, el in enumerate(observation.elements):
        if not (el.interactive or el.role in ("heading", "alert", "status", "dialog")):
            continue
        if not el.label and el.role not in _FIELDS and el.role != "dialog":
            continue                                   # nothing to call it by
        line = el.summary()[:140]
        if line in seen:
            seen[line] += 1                            # identical items: listed once, with how many there are
            continue
        seen[line] = 1
        chrome = el.region(*_CHROME) is not None
        # what cannot be used until something else is opened (a closed drawer, a later slide) comes last
        priority = 3 if getattr(el, "offscreen", False) else 2 if (el.role == "link" and chrome) else 1 if el.role == "link" else 0
        entries.append((priority, order, line))
    kept = sorted(entries)[:MAX_ELEMENT_LINES] if len(entries) > MAX_ELEMENT_LINES else entries
    if len(entries) > MAX_ELEMENT_LINES:
        nav = [e for e in kept if e[0] == 2]
        if len(nav) > 15:                               # navigation never crowds out content
            kept = [e for e in kept if e[0] != 2] + nav[:15]
        away = [e for e in kept if e[0] == 3]
        if len(away) > 25:                              # nor does what is off-screen
            kept = [e for e in kept if e[0] != 3] + away[:25]
    lines = [line + (f"  [x{seen[line]}, identical]" if seen[line] > 1 else "")
             for _, _, line in sorted(kept, key=lambda e: e[1])]
    if len(lines) < len(entries):
        lines.append(f"... ({len(entries) - len(lines)} more links not shown)")
    text = [t[:200] for t in dict.fromkeys(t.strip() for t in observation.page_text) if t][:MAX_TEXT_LINES]
    return {"url": relative_url(observation.url), "title": observation.title,
            "elements": lines, "text": text}


class Planner:
    def __init__(self, config, provider_name=None, emit=lambda e: None):
        self.config, self.provider_name, self.emit = config, provider_name, emit
        self.provider = make_provider(config, provider_name, role="planner")
        self.usage = {"input": 0, "output": 0}   # running totals, drained by the caller
        self.timings = []                        # per call: {ms, first_step_ms, input, output, reasoning}

    def _message(self, task, observation, done_steps, problem, test_data, history, known_pages):
        message = {
            "task": task,
            "test_data": test_data or {},
            "page": page_summary(observation),
            "steps_done": [s.get("name") or s["op"] for s in done_steps][-30:],
            "problem": problem,
            "earlier_tasks": list(history)[-5:],
        }
        if known_pages:
            message["known_pages"] = list(known_pages)
        return json.dumps(message, ensure_ascii=False)

    def plan(self, task, observation, *, done_steps=(), problem=None, test_data=None, history=(), known_pages=None):
        """The whole plan in one go (blocks until the model has finished writing it)."""
        stream = self.start(task, observation, done_steps=done_steps, problem=problem,
                            test_data=test_data, history=history, known_pages=known_pages)
        for _ in stream.steps():
            pass
        return stream.result()

    def start(self, task, observation, *, done_steps=(), problem=None, test_data=None, history=(), known_pages=None):
        """Start planning and return a PlanStream: its steps can be executed while the model
        is still writing the rest of the plan."""
        return PlanStream(self, self._message(task, observation, done_steps, problem, test_data, history,
                                              known_pages))

    def _account(self, started, first_step_ms, cancelled):
        usage = self.provider.last_usage or {}
        tokens_in = usage.get("prompt_tokens", usage.get("input_tokens"))
        tokens_out = usage.get("completion_tokens", usage.get("output_tokens"))
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        ms = round((time.monotonic() - started) * 1000)
        self.timings.append({"ms": ms, "first_step_ms": first_step_ms, "input": tokens_in, "output": tokens_out,
                             "reasoning": reasoning, "cancelled": cancelled})
        # The chat runtime drains these totals after every turn (it pops the keys).
        self.usage["input"] = self.usage.get("input", 0) + int(tokens_in or 0)
        self.usage["output"] = self.usage.get("output", 0) + int(tokens_out or 0)
        self.emit({"event": "model_usage", "role": "planner", "profile": self.provider_name,
                   "input": tokens_in, "output": tokens_out, "reasoning_tokens": reasoning,
                   "duration_ms": ms, "first_step_ms": first_step_ms})


class StepScanner:
    """Pulls each complete step object out of a reply that is still being written:
    {"steps": [{...}, {...}, ...  -> yields every {...} as soon as its closing brace arrives."""

    _STEPS = re.compile(r'"steps"\s*:\s*\[')

    def __init__(self):
        self.text, self.pos, self.depth, self.start = "", None, 0, None
        self.in_string = self.escaped = self.closed = False

    def feed(self, piece):
        self.text += piece
        found = []
        if self.pos is None:
            match = self._STEPS.search(self.text)
            if not match:
                return found
            self.pos = match.end()
        i, text = self.pos, self.text
        while i < len(text) and not self.closed:
            ch = text[i]
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif ch == "\\":
                    self.escaped = True
                elif ch == '"':
                    self.in_string = False
            elif ch == '"':
                self.in_string = True
            elif ch == "{":
                if self.depth == 0:
                    self.start = i
                self.depth += 1
            elif ch == "}" and self.depth > 0:
                self.depth -= 1
                if self.depth == 0 and self.start is not None:
                    try:
                        found.append(json.loads(text[self.start:i + 1]))
                    except ValueError:
                        pass
                    self.start = None
            elif ch == "]" and self.depth == 0:
                self.closed = True
            i += 1
        self.pos = i
        return [s for s in (normalize_step(f) for f in found) if s]


class PlanStream:
    """One planner call, streamed on a worker thread. `steps()` yields each step as soon as
    the model has written it; `cancel()` abandons the rest (the plan stopped matching the
    page, so the model would only be writing steps that won't be used); `result()` gives
    the final {steps, done, blocked, test}."""

    def __init__(self, planner, message):
        self.planner, self.message = planner, message
        self.queue, self.scanner = queue.Queue(), StepScanner()
        self.started = time.monotonic()
        self.first_step_ms = None
        self.cancelled = False
        self.text, self.error = "", None
        self.thread = threading.Thread(target=self._run, name="qm-planner", daemon=True)
        self.thread.start()

    def _run(self):
        prompt = self.message + "\n\nRespond with ONLY valid JSON. No prose, no code fences."
        provider = self.planner.provider
        try:
            if hasattr(provider, "stream"):
                self.text = provider.stream(SYSTEM, prompt, self._on_text, temperature=0.1)
            else:   # a provider that can only answer in one piece
                self.text = provider.complete(SYSTEM, prompt, temperature=0.1)
                self._on_text(self.text)
        except Exception as exc:   # surfaced by result(); the caller decides what it means
            self.error = exc
        finally:
            self.queue.put(None)

    def _on_text(self, piece):
        if self.cancelled:
            return False
        for step in self.scanner.feed(piece):
            if self.first_step_ms is None:
                self.first_step_ms = round((time.monotonic() - self.started) * 1000)
            self.queue.put(step)
        return True

    def steps(self):
        while True:
            step = self.queue.get()
            if step is None:
                return
            yield step

    def pending(self):
        """Steps already written but not yet taken by steps() -- shown as upcoming."""
        return [s for s in list(self.queue.queue) if s is not None]

    def cancel(self):
        self.cancelled = True

    def result(self, timeout=None):
        self.thread.join(timeout)
        self.planner._account(self.started, self.first_step_ms, self.cancelled)
        if self.error is not None and not self.cancelled:
            raise self.error if isinstance(self.error, LLMError) else LLMError(str(self.error))
        try:
            if not self.cancelled:
                return normalize_plan(extract_json(self.text))
        except LLMError:
            if self.first_step_ms is None:
                raise
        # Abandoned (or cut off) after some steps: keep what the reply said about the test.
        match = re.search(r'"test"\s*:\s*(\{[^{}]*\})', self.scanner.text)
        try:
            test = json.loads(match.group(1)) if match else {}
        except ValueError:
            test = {}
        return {"steps": [], "done": False, "blocked": None, "test": _test_meta(test), "partial": True}


# Models sometimes end the list with a pseudo-step; it means "the plan is complete".
END_STEPS = {"done", "finish", "finished", "end", "stop", "complete", "completed"}


def is_end_step(step):
    return isinstance(step, dict) and str(step.get("do", "")).strip().lower() in END_STEPS


def normalize_step(step):
    if not isinstance(step, dict) or not step.get("do") or is_end_step(step):
        return None
    step = dict(step)
    if step["do"] == "expect_text" and not step.get("target"):
        step["do"] = "expect_page_text"
    if step["do"] == "press" and not step.get("key"):
        step["key"] = step.pop("value", None) or "Enter"       # models also write the key as "value"
    if step["do"] in ("right_click", "context_click", "contextmenu"):
        step["do"] = "rightclick"
    if step["do"] in ("double_click", "doubleclick"):
        step["do"] = "dblclick"
    if step["do"] in ("sleep", "pause", "wait_for"):
        step["do"] = "wait"
    if step["do"] == "wait" and step.get("seconds") is None:
        step["seconds"] = step.pop("value", None) or step.pop("duration", None) or 1
    if step["do"] in ("drag_and_drop", "drag_to", "dragdrop") or (step["do"] == "drag" and not step.get("to")):
        step["do"] = "drag"
        step.setdefault("to", step.pop("destination", None) or step.pop("value", None) or "")
    return step


def _test_meta(test):
    test = test if isinstance(test, dict) else {}
    flow, title = str(test.get("flow") or ""), str(test.get("title") or "").strip()
    if "<" in flow:      # the prompt's placeholder copied verbatim
        flow = ""
    return {"flow": slug(flow or "agent"), "title": "" if "<" in title else title[:120]}


def normalize_plan(raw):
    """Defensive parse of the planner's JSON into {steps, done, blocked, test}."""
    if not isinstance(raw, dict):
        raise LLMError("planner did not return a JSON object")
    raw_steps = raw.get("steps") or []
    steps = [s for s in (normalize_step(step) for step in raw_steps) if s]
    done = bool(raw.get("done")) or any(is_end_step(step) for step in raw_steps)
    return {"steps": steps, "done": done, "blocked": raw.get("blocked") or None,
            "test": _test_meta(raw.get("test"))}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "agent"
