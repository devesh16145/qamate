"""Fast agent: one chat task -> plan, execute, verify, save a replayable test.

Loop (bounded by rounds and wall time, so it can't browse forever):
    observe -> planner proposes a few steps -> executor runs them (no model calls for
    clear steps, Jev for ambiguous ones) -> on a surprise the planner sees the new page
    and the exact problem -> ... until the planner says done.
Then the recorded steps are replayed in a FRESH browser context through the same
runtime. The test is saved only when that replay passes, so every saved test has
already been shown to run on its own.
"""
import json
import os
import re
import time
from urllib.parse import urlsplit

from llm import LLMError
from qm_explorer import Explorer
from qm_map import PageMap, describe_pages, quick_scan
from qm_observe import observe
from qm_planner import Planner, slug
from qm_runtime import relative_url
from qm_steps import render
from qm_testgen import write_test
from qm_verify import replay


_EXPLORE = re.compile(r"^\s*(?:please\s+)?(?:explore|scan|map|learn)\b", re.I)


def is_exploration(task):
    """'Explore the app and list its flows' asks for a map of the app, not a test."""
    return bool(_EXPLORE.match(task or "")) and not re.search(r"\btests?\b", task, re.I)


_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)


def task_url(task):
    """The first web address in a request ("Explore https://... and ..."), minus trailing punctuation."""
    match = _URL.search(task or "")
    return match.group(0).rstrip(".,;:!?") if match else None


def _origin(url):
    parts = urlsplit(url or "")
    return parts.scheme, parts.netloc


def _intent_text(intent):
    op = intent.get("do")
    bits = [op.replace("_", " ")]
    for key in ("target", "url", "key", "value"):
        if intent.get(key) not in (None, ""):
            bits.append(f'"{intent[key]}"' if key != "url" else intent[key])
    if intent.get("within"):
        bits.append(f'(in "{intent["within"]}")')
    if intent.get("present") is False:
        bits.append("(must not be visible)")
    return " ".join(bits)


def next_tc_id(tests_root, flow):
    """TC-<FLOW>-NNN not yet used in that flow."""
    prefix = "TC-" + re.sub(r"[^A-Z0-9]+", "-", flow.upper()).strip("-")
    taken = set()
    path = os.path.join(tests_root, "flows", flow, "test_cases.json")
    try:
        taken = {c.get("tc_id") for c in json.load(open(path, encoding="utf-8"))}
    except Exception:
        pass
    n = 1
    while f"{prefix}-{n:03d}" in taken:
        n += 1
    return f"{prefix}-{n:03d}"


class FastAgent:
    def __init__(self, page, browser, config, *, provider_name=None, base_url=None, tests_root=None,
                 storage_state=None, test_data=None, emit=lambda e: None, confirm=None,
                 max_rounds=8, max_seconds=300, log_dir=None, page_map=None, scan="auto"):
        self.page, self.browser, self.config = page, browser, config
        self.base_url, self.tests_root, self.storage_state = base_url, tests_root, storage_state
        self.test_data, self.emit, self.confirm = test_data or {}, emit, confirm
        self.max_rounds, self.max_seconds, self.log_dir = max_rounds, max_seconds, log_dir
        self.planner = Planner(config, provider_name, emit=emit)
        self.history = []   # short summaries of earlier tasks in this chat
        # What the agent knows about the app's pages (qm_map); per project when given a path.
        self.page_map = page_map if page_map is not None else PageMap()
        self.scan = scan     # "auto": learn a fresh app's main pages before the first plan

    def run_task(self, task):
        started = time.monotonic()
        explorer = Explorer(self.page, base_url=self.base_url, config=self.config,
                            emit=self.emit, confirm=self.confirm, page_map=self.page_map)
        self.planner.timings = []
        self.trace = []   # one entry per executed intent: what ran, how, how long
        self.scan_ms = None
        # Every test starts from a known page: reopen where this task begins. A blank browser
        # (no project URL) opens the site the request names; with none named, the planner's
        # first step opens the app. "Explore <url>" goes there even from another app.
        start_url = self.page.url if self.page.url not in ("", "about:blank") else self.base_url
        asked = task_url(task)
        if asked and (not start_url or (is_exploration(task) and _origin(asked) != _origin(start_url))):
            start_url = asked
        if start_url:
            start = explorer.run_intent({"do": "goto", "url": start_url, "name": "Open the app"})
            if not start["ok"]:
                return self._finish(task, explorer, started, error=f"could not open the app: {start.get('detail')}")
            if is_exploration(task):
                return self._explore(task, started)
            if self.scan == "auto" and len(self.page_map) < 3:
                self._learn_app()
        elif is_exploration(task):
            return {"task": task, "steps": 0, "authoring_s": 0, "stop_reason": None, "saved": None, "replay": None,
                    "ask": "Which site should I explore? Include its address, for example: "
                           "Explore https://marmelab.com/atomic-crm-demo/ and list its pages and forms."}
        problem, meta, plan_items, stop_reason = None, {"flow": "agent", "title": ""}, [], None
        for round_no in range(self.max_rounds):
            if time.monotonic() - started > self.max_seconds:
                stop_reason = f"stopped after {self.max_seconds} s"
                break
            obs = observe(self.page)
            self.page_map.see(obs)
            try:
                stream = self._start_plan(task, obs, explorer, problem)
            except Exception as exc:
                stop_reason = f"planner error: {str(exc)[:200]}"
                break
            # Steps run as soon as the planner has written them, while it writes the rest.
            plan_items = [p for p in plan_items if p["status"] == "done"]
            problem, ran = None, 0
            for intent in stream.steps():
                item = {"step": _intent_text(intent), "status": "active"}
                plan_items.append(item)
                self._emit_plan(plan_items, stream)
                self.emit({"event": "tool_call", "tool": "step", "args": intent})
                outcome = explorer.run_intent(intent)   # asks self.confirm before destructive actions
                ran += 1
                self.trace.append({"intent": intent, "ok": outcome["ok"], "ms": outcome.get("ms"),
                                   "how": outcome.get("how"), "reason": outcome.get("reason"),
                                   "detail": outcome.get("detail")})
                self.emit({"event": "tool_result", "tool": "step", "outcome": "ok" if outcome["ok"] else "error",
                           "summary": json.dumps(_brief(outcome), ensure_ascii=False)[:1500]})
                item["status"] = "done" if outcome["ok"] else "failed"
                self._emit_plan(plan_items, stream)
                if not outcome["ok"]:
                    problem = {"step": intent, **_brief(outcome)}
                    stream.cancel()   # the rest was planned for a page that turned out different
                    break
            try:
                plan = stream.result()
            except LLMError as exc:
                if ran and problem is None:   # the reply broke off after some steps ran: carry on
                    problem = {"reason": "planner_error", "detail": str(exc)[:200]}
                    continue
                stop_reason = f"planner error: {str(exc)[:200]}"
                break
            except Exception as exc:          # a bug on our side, not the model's: don't re-plan around it
                stop_reason = f"internal error: {type(exc).__name__}: {str(exc)[:200]}"
                break
            if plan["test"]["flow"] != "agent":
                meta["flow"] = plan["test"]["flow"]
            if plan["test"]["title"] and (not meta["title"] or plan["test"]["flow"] != "agent"):
                meta["title"] = plan["test"]["title"]
            if plan["blocked"]:
                stop_reason = f"blocked: {plan['blocked']}"
                break
            if problem is not None:
                continue
            if not ran:
                if plan["done"]:
                    break
                problem = {"reason": "empty_plan", "detail": "no steps were proposed; propose the next steps or set done"}
                continue
            if plan["done"]:
                break
        else:
            stop_reason = f"stopped after {self.max_rounds} planning rounds"
        return self._finish(task, explorer, started, meta=meta, stop_reason=stop_reason, problem=problem)

    def _learn_app(self, max_pages=12, max_seconds=20):
        """A fresh app: read its navigation and "New ..." pages first (links only, nothing is
        submitted), so the plan can use the real labels of pages not yet on screen."""
        item = [{"step": "Learn the app's main pages", "status": "active"}]
        self.emit({"event": "plan", "steps": item})
        scanned = quick_scan(self.page, self.page_map, max_pages=max_pages, max_seconds=max_seconds, emit=self.emit)
        self.scan_ms = scanned["ms"]
        item[0].update(status="done", step=f"Learned {scanned['pages']} pages of the app")
        self.emit({"event": "plan", "steps": item})

    def _explore(self, task, started):
        """'Explore the app': map its pages (following links only) instead of authoring a test."""
        page_map = self.page_map
        if page_map.path and self.base_url and _origin(self.page.url) != _origin(self.base_url):
            page_map = PageMap()   # another site: keep it out of this project's memory
        scanned = quick_scan(self.page, page_map, max_pages=25, max_seconds=45, emit=self.emit)
        result = {"task": task, "steps": 0, "authoring_s": round(time.monotonic() - started, 1),
                  "stop_reason": None, "saved": None, "replay": None,
                  "explored": {"pages": scanned["pages"], "s": round(scanned["ms"] / 1000, 1),
                               "lines": describe_pages(page_map)}}
        self.history.append(f"{task[:80]} -> mapped {scanned['pages']} pages")
        return result

    def _start_plan(self, task, observation, explorer, problem):
        kwargs = {"done_steps": explorer.steps, "problem": problem, "test_data": self.test_data,
                  "history": self.history, "known_pages": self.page_map.for_task(task, observation.url)}
        if hasattr(self.planner, "start"):
            return self.planner.start(task, observation, **kwargs)
        return _Planned(self.planner.plan(task, observation, **kwargs))

    def _emit_plan(self, items, stream):
        upcoming = [{"step": _intent_text(s), "status": "pending"} for s in stream.pending()]
        self.emit({"event": "plan", "steps": items + upcoming})

    def _finish(self, task, explorer, started, *, meta=None, stop_reason=None, problem=None, error=None):
        meta = meta or {"flow": "agent", "title": ""}
        steps, data = explorer.steps, explorer.data
        self.page_map.save()
        # A test must open a real page first, once.
        while len(steps) >= 2 and steps[0]["op"] == "goto" and steps[1]["op"] == "goto":
            steps.pop(0)
        if steps and steps[0]["op"] != "goto" and explorer.start_url:
            steps.insert(0, {"op": "goto", "value": explorer._url_value(explorer.start_url), "name": "Open the app"})
        trace = getattr(self, "trace", [])
        calls = list(getattr(self.planner, "timings", []))
        result = {"task": task, "steps": len(steps), "authoring_s": round(time.monotonic() - started, 1),
                  "stop_reason": stop_reason or error, "saved": None, "replay": None,
                  "timing": {"planner_calls": len(calls), "planner_s": round(sum(c["ms"] for c in calls) / 1000, 1),
                             "first_step_s": (round(calls[0]["first_step_ms"] / 1000, 1)
                                              if calls and calls[0].get("first_step_ms") is not None else None),
                             "scan_s": round(self.scan_ms / 1000, 1) if getattr(self, "scan_ms", None) else None,
                             "browser_s": round(sum((t.get("ms") or 0) for t in trace) / 1000, 1),
                             "replay_s": None},
                  "replanned": [{"step": _intent_text(t["intent"]), "reason": t["reason"], "detail": t["detail"]}
                                for t in trace if not t["ok"]]}
        meaningful = [s for s in steps if s["op"] != "goto"]
        if error or not meaningful:
            result["summary"] = error or stop_reason or "Nothing was recorded."
            self.history.append(f"{task[:80]} -> not recorded")
            return result
        verify = replay(self.browser, steps, data, base_url=self.base_url, storage_state=self.storage_state)
        result["replay"] = {"ok": verify["ok"], "ms": verify["ms"], "failed_step": verify["failed_step"]}
        result["timing"]["replay_s"] = round(verify["ms"] / 1000, 1)
        if verify["ok"] and not stop_reason and self.tests_root:
            flow = slug(meta.get("flow") or "agent")
            tc_id = next_tc_id(self.tests_root, flow)
            path = write_test(self.tests_root, flow, tc_id, steps, data,
                              description=meta.get("title") or task[:120], expected="")
            result["saved"] = {"tc_id": tc_id, "flow": flow, "path": path}
        result["code"] = [render(s) for s in steps]
        self.history.append(f"{task[:80]} -> {result['saved']['tc_id'] if result['saved'] else 'not saved'}")
        self._write_log(result, calls, trace)
        return result

    def _write_log(self, result, calls, trace):
        """One JSON file per task: planner calls, every executed step, replay -- for tuning."""
        if not self.log_dir:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + "-" + ((result.get("saved") or {}).get("tc_id") or "unsaved") + ".json"
            with open(os.path.join(self.log_dir, name), "w", encoding="utf-8") as f:
                json.dump({**result, "planner_calls": calls, "trace": trace}, f, indent=2, ensure_ascii=False, default=str)
            result["log"] = os.path.join(self.log_dir, name)
        except Exception:
            pass


class _Planned:
    """A finished plan behind the PlanStream interface, for planners that don't stream."""

    def __init__(self, plan):
        self.plan, self.taken = plan, 0

    def steps(self):
        for step in self.plan["steps"]:
            self.taken += 1
            yield step

    def pending(self):
        return self.plan["steps"][self.taken:]

    def cancel(self):
        pass

    def result(self):
        return self.plan


def _brief(outcome):
    keep = ("ok", "reason", "detail", "how", "confidence", "element", "candidates", "url", "ms")
    out = {k: outcome[k] for k in keep if k in outcome and outcome[k] not in (None, "", [])}
    if outcome.get("step"):
        out["recorded"] = render(outcome["step"]) if outcome["ok"] else None
    return out


def summary_text(result):
    """Plain chat reply for a finished task."""
    lines = []
    if result.get("ask"):
        return result["ask"]
    if result.get("explored"):
        e = result["explored"]
        lines.append(f"Mapped **{e['pages']} pages** in {e['s']} s, following links only (nothing was submitted).\n")
        lines += e["lines"]
        lines.append("\nI'll plan with these exact labels from now on. Ask me to write a test for any of these flows.")
        return "\n".join(lines)
    if result.get("saved"):
        s = result["saved"]
        lines.append(f"Saved **{s['tc_id']}** in flow `{s['flow']}` — {result['steps']} steps, "
                     f"authored in {result['authoring_s']} s.")
        lines.append(f"Replay in a fresh browser: **passed** ({result['replay']['ms'] / 1000:.1f} s).")
    elif result.get("replay") and not result["replay"]["ok"]:
        f = result["replay"]["failed_step"] or {}
        lines.append(f"The flow ran, but replaying it in a fresh browser **failed** at step "
                     f"{f.get('index', 0) + 1} ({f.get('name') or f.get('op')}): {f.get('error', '')}")
        lines.append("Not saved. Tell me what to change, or ask me to try again.")
    else:
        lines.append(f"Not saved: {result.get('summary') or result.get('stop_reason') or 'the task did not finish'}.")
    if result.get("stop_reason") and result.get("saved") is None and result.get("replay"):
        lines.append(f"Stopped early: {result['stop_reason']}.")
    t = result.get("timing") or {}
    if t.get("planner_calls") is not None:
        calls = f"{t['planner_calls']} call{'s' if t['planner_calls'] != 1 else ''}"
        if t.get("first_step_s") is not None:
            calls += f", first step running at {t['first_step_s']} s"
        parts = [f"planning {t['planner_s']} s ({calls})", f"browser {t['browser_s']} s"]
        if t.get("replay_s") is not None:
            parts.append(f"replay {t['replay_s']} s")
        if t.get("scan_s") is not None:
            parts.insert(0, f"learning the app {t['scan_s']} s")
        lines.append("Time: " + " · ".join(parts) + ".")
    for r in (result.get("replanned") or [])[:3]:
        lines.append(f"Re-planned after: {r['step']} — {r['reason']}" + (f" ({r['detail'][:120]})" if r.get("detail") else ""))
    if result.get("code"):
        lines.append("\n```python\n" + "\n".join(result["code"]) + "\n```")
    return "\n".join(lines)
