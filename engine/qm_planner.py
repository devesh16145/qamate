"""Planner for the fast explorer: task + current page -> a few plain-language steps.

The planner (any configured chat model; MiMo by default) is called rarely: once to
plan, again only when the executor reports a surprise (a step it could not do), and
it sees a compact page summary -- element lines and visible text -- instead of a large
tool list and full DOM dumps. It plans only as far as it can see; after those steps run
it is shown the new page.
"""
import json
import re
import time

from llm import make_provider, complete_json, LLMError
from qm_runtime import relative_url

SYSTEM = """You plan the steps of a browser test for QAmate, a test-automation tool.
You get the task, test data and the CURRENT page (its interactive elements and visible text).
Reply with ONLY a JSON object:
{"steps": [STEP, ...], "done": false, "test": {"flow": "orders", "title": "Short test title"}}

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
- On the current page, use labels exactly as they appear in its elements.
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
        self.timings = []                        # per call: {ms, input, output, reasoning}

    def plan(self, task, observation, *, done_steps=(), problem=None, test_data=None, history=()):
        message = {
            "task": task,
            "test_data": test_data or {},
            "page": page_summary(observation),
            "steps_done": [s.get("name") or s["op"] for s in done_steps][-30:],
            "problem": problem,
            "earlier_tasks": list(history)[-5:],
        }
        started = time.monotonic()
        try:
            raw = complete_json(self.provider, SYSTEM, json.dumps(message, ensure_ascii=False), temperature=0.1)
        finally:
            usage = self.provider.last_usage or {}
            self.timings.append({"ms": round((time.monotonic() - started) * 1000),
                               "input": usage.get("prompt_tokens", usage.get("input_tokens")),
                               "output": usage.get("completion_tokens", usage.get("output_tokens")),
                               "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")})
            self.usage["input"] += int(usage.get("prompt_tokens", usage.get("input_tokens")) or 0)
            self.usage["output"] += int(usage.get("completion_tokens", usage.get("output_tokens")) or 0)
            self.emit({"event": "model_usage", "role": "planner", "profile": self.provider_name,
                       "input": usage.get("prompt_tokens", usage.get("input_tokens")),
                       "output": usage.get("completion_tokens", usage.get("output_tokens")),
                       "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                       "duration_ms": round((time.monotonic() - started) * 1000)})
        return normalize_plan(raw)


def normalize_plan(raw):
    """Defensive parse of the planner's JSON into {steps, done, blocked, test}."""
    if not isinstance(raw, dict):
        raise LLMError("planner did not return a JSON object")
    steps = []
    for step in raw.get("steps") or []:
        if not isinstance(step, dict) or not step.get("do"):
            continue
        step = dict(step)
        if step["do"] == "expect_text" and not step.get("target"):
            step["do"] = "expect_page_text"
        steps.append(step)
    test = raw.get("test") if isinstance(raw.get("test"), dict) else {}
    return {"steps": steps, "done": bool(raw.get("done")), "blocked": raw.get("blocked") or None,
            "test": {"flow": slug(test.get("flow") or "agent"), "title": str(test.get("title") or "").strip()[:120]}}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "agent"
