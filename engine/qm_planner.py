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
("label -> route" says where a control led).
Reply with ONLY a JSON object, keys in this order:
{"test": {"flow": "orders", "title": "Short test title"}, "steps": [STEP, ...], "done": false}

Each STEP is one of:
  {"do": "goto", "url": "<path or URL>"}            only the app's start URL or a link you can see
  {"do": "click", "target": "<label>", "within": "<item name, only if the label repeats>"}
  {"do": "fill", "target": "<field label>", "value": "<text>"}
  {"do": "select", "target": "<dropdown label>", "value": "<option text>"}
  {"do": "check", "target": "<checkbox label>", "checked": true}
  {"do": "press", "key": "Enter", "target": "<field label, optional>"}
  {"do": "expect_text", "value": "<text that must be visible>"}
  {"do": "expect_text", "value": "<text>", "present": false}     must NOT be visible
  {"do": "expect_value", "target": "<field label>", "value": "<text>"}
  {"do": "expect_visible", "target": "<label>"}
  {"do": "expect_url", "url": "<path>"}             only when certain of the exact path

Rules:
- Plan the WHOLE task in this reply whenever you can predict it, including pages you haven't
  seen yet -- for those, write the labels a user would see. Each extra round trip costs time.
  If a step doesn't match the real page, execution stops there and you'll be shown that page
  with the closest elements, so you can adjust the rest.
- On the current page, use labels exactly as they appear in its elements. For pages listed in
  known_pages, use their labels exactly too -- plan through them as confidently as the current page.
- When several elements share a label (e.g. "Add to cart" on every product), say which one
  with "within": the item's name, row text or section.
- Set "done": true when your steps complete the whole task, including its checks. Use
  "done": false only if you genuinely need to see a page before planning further.
- Check every outcome the task asks for with expect_* steps. Prefer stable visible text;
  never assert times, dates or generated ids exactly.
- Use values from the task or the test data. Never invent credentials.
- If you get a "problem" (a step that could not run), change your approach -- don't repeat
  the same step. The problem lists the closest matching elements.
- If the task cannot be done, reply {"steps": [], "done": true, "blocked": "<why>"}.
"""

MAX_ELEMENT_LINES = 120
MAX_TEXT_LINES = 40


def page_summary(observation):
    lines, seen = [], set()
    for el in observation.elements:
        if not (el.interactive or el.role in ("heading", "alert", "status", "dialog")):
            continue
        line = el.summary()[:140]
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= MAX_ELEMENT_LINES:
            lines.append("... (more elements not shown)")
            break
    text = [t for t in dict.fromkeys(t.strip() for t in observation.page_text) if t][:MAX_TEXT_LINES]
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
        self.usage["input"] += int(tokens_in or 0)
        self.usage["output"] += int(tokens_out or 0)
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


def normalize_step(step):
    if not isinstance(step, dict) or not step.get("do"):
        return None
    step = dict(step)
    if step["do"] == "expect_text" and not step.get("target"):
        step["do"] = "expect_page_text"
    return step


def _test_meta(test):
    test = test if isinstance(test, dict) else {}
    return {"flow": slug(test.get("flow") or "agent"), "title": str(test.get("title") or "").strip()[:120]}


def normalize_plan(raw):
    """Defensive parse of the planner's JSON into {steps, done, blocked, test}."""
    if not isinstance(raw, dict):
        raise LLMError("planner did not return a JSON object")
    steps = [s for s in (normalize_step(step) for step in raw.get("steps") or []) if s]
    return {"steps": steps, "done": bool(raw.get("done")), "blocked": raw.get("blocked") or None,
            "test": _test_meta(raw.get("test"))}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "agent"
