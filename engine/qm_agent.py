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

from qm_explorer import Explorer
from qm_observe import observe
from qm_planner import Planner, slug
from qm_runtime import relative_url
from qm_steps import render
from qm_testgen import write_test
from qm_verify import replay


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
                 max_rounds=8, max_seconds=300):
        self.page, self.browser, self.config = page, browser, config
        self.base_url, self.tests_root, self.storage_state = base_url, tests_root, storage_state
        self.test_data, self.emit, self.confirm = test_data or {}, emit, confirm
        self.max_rounds, self.max_seconds = max_rounds, max_seconds
        self.planner = Planner(config, provider_name, emit=emit)
        self.history = []   # short summaries of earlier tasks in this chat

    def run_task(self, task):
        started = time.monotonic()
        explorer = Explorer(self.page, base_url=self.base_url, config=self.config,
                            emit=self.emit, confirm=self.confirm)
        # Every test starts from a known page: reopen where this task begins.
        start = explorer.run_intent({"do": "goto", "url": self.page.url if self.page.url != "about:blank"
                                     else (self.base_url or "about:blank"), "name": "Open the app"})
        if not start["ok"]:
            return self._finish(task, explorer, started, error=f"could not open the app: {start.get('detail')}")
        problem, meta, plan_items, stop_reason = None, {"flow": "agent", "title": ""}, [], None
        for round_no in range(self.max_rounds):
            if time.monotonic() - started > self.max_seconds:
                stop_reason = f"stopped after {self.max_seconds} s"
                break
            obs = observe(self.page)
            try:
                plan = self.planner.plan(task, obs, done_steps=explorer.steps, problem=problem,
                                         test_data=self.test_data, history=self.history)
            except Exception as exc:
                stop_reason = f"planner error: {str(exc)[:200]}"
                break
            if plan["test"]["flow"] != "agent" or not meta["title"]:
                meta = {**meta, **{k: v for k, v in plan["test"].items() if v}}
            if plan["blocked"]:
                stop_reason = f"blocked: {plan['blocked']}"
                break
            if not plan["steps"]:
                if plan["done"]:
                    break
                problem = {"reason": "empty_plan", "detail": "no steps were proposed; propose the next steps or set done"}
                continue
            plan_items = [p for p in plan_items if p["status"] == "done"] + \
                         [{"step": _intent_text(s), "status": "pending"} for s in plan["steps"]]
            self.emit({"event": "plan", "steps": plan_items})
            problem = None
            for intent in plan["steps"]:
                item = next(p for p in plan_items if p["status"] == "pending")
                item["status"] = "active"
                self.emit({"event": "plan", "steps": plan_items})
                self.emit({"event": "tool_call", "tool": "step", "args": intent})
                outcome = explorer.run_intent(intent)   # asks self.confirm before destructive actions
                self.emit({"event": "tool_result", "tool": "step", "outcome": "ok" if outcome["ok"] else "error",
                           "summary": json.dumps(_brief(outcome), ensure_ascii=False)[:1500]})
                item["status"] = "done" if outcome["ok"] else "failed"
                self.emit({"event": "plan", "steps": plan_items})
                if not outcome["ok"]:
                    problem = {"step": intent, **_brief(outcome)}
                    break
            if problem is None and plan["done"]:
                break
        else:
            stop_reason = f"stopped after {self.max_rounds} planning rounds"
        return self._finish(task, explorer, started, meta=meta, stop_reason=stop_reason, problem=problem)

    def _finish(self, task, explorer, started, *, meta=None, stop_reason=None, problem=None, error=None):
        meta = meta or {"flow": "agent", "title": ""}
        steps, data = explorer.steps, explorer.data
        result = {"task": task, "steps": len(steps), "authoring_s": round(time.monotonic() - started, 1),
                  "stop_reason": stop_reason or error, "saved": None, "replay": None}
        meaningful = [s for s in steps if s["op"] != "goto"]
        if error or not meaningful:
            result["summary"] = error or stop_reason or "Nothing was recorded."
            self.history.append(f"{task[:80]} -> not recorded")
            return result
        verify = replay(self.browser, steps, data, base_url=self.base_url, storage_state=self.storage_state)
        result["replay"] = {"ok": verify["ok"], "ms": verify["ms"], "failed_step": verify["failed_step"]}
        if verify["ok"] and not stop_reason and self.tests_root:
            flow = slug(meta.get("flow") or "agent")
            tc_id = next_tc_id(self.tests_root, flow)
            path = write_test(self.tests_root, flow, tc_id, steps, data,
                              description=meta.get("title") or task[:120], expected="")
            result["saved"] = {"tc_id": tc_id, "flow": flow, "path": path}
        result["code"] = [render(s) for s in steps]
        self.history.append(f"{task[:80]} -> {result['saved']['tc_id'] if result['saved'] else 'not saved'}")
        return result


def _brief(outcome):
    keep = ("ok", "reason", "detail", "how", "confidence", "element", "candidates", "url", "ms")
    out = {k: outcome[k] for k in keep if k in outcome and outcome[k] not in (None, "", [])}
    if outcome.get("step"):
        out["recorded"] = render(outcome["step"]) if outcome["ok"] else None
    return out


def summary_text(result):
    """Plain chat reply for a finished task."""
    lines = []
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
    if result.get("code"):
        lines.append("\n```python\n" + "\n".join(result["code"]) + "\n```")
    return "\n".join(lines)
