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
from qm_explorer import SECRET_FIELD, Explorer
from qm_map import PageMap, describe_pages, quick_scan
from qm_observe import observe
from qm_planner import Planner, slug
from qm_runtime import relative_url
from qm_steps import render
from qm_testgen import write_test
from qm_verify import replay


_EXPLORE = re.compile(r"^\s*(?:please\s+)?(?:explore|scan|map|learn)\b", re.I)
_ASKS_TO_WAIT = re.compile(r"\b(?:wait|pause|sleep)\b|\d+\s*(?:s|sec|secs|seconds?)\b", re.I)


def is_exploration(task):
    """'Explore the app and list its flows' asks for a map of the app, not a test."""
    return bool(_EXPLORE.match(task or "")) and not re.search(r"\btests?\b", task, re.I)


_LINKS_ONLY = re.compile(r"\blinks?\s+only\b|\bonly\s+(?:follow\s+)?links\b|\b(?:don'?t|do not|no)\s+click", re.I)
_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)


def task_url(task):
    """The first web address in a request ("Explore https://... and ..."), minus trailing punctuation."""
    match = _URL.search(task or "")
    return match.group(0).rstrip(".,;:!?") if match else None


def _origin(url):
    parts = urlsplit(url or "")
    return parts.scheme, parts.netloc


def public_intent(intent):
    """The intent as it may be shown and logged: a typed secret is masked."""
    if intent.get("do") == "fill" and "{secret:" not in str(intent.get("value", "")) \
            and SECRET_FIELD.search(str(intent.get("target", ""))):
        return {**intent, "value": "••••••"}
    return intent


def _intent_text(intent):
    intent = public_intent(intent)
    op = intent.get("do")
    bits = [op.replace("_", " ")]
    for key in ("target", "to", "url", "key", "value"):
        if intent.get(key) not in (None, ""):
            bits.append(intent[key] if key == "url" else f'to "{intent[key]}"' if key == "to" else f'"{intent[key]}"')
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


REPLAYS = 2      # fresh-browser runs a test must pass before it is saved


class FastAgent:
    def __init__(self, page, browser, config, *, provider_name=None, base_url=None, tests_root=None,
                 storage_state=None, test_data=None, emit=lambda e: None, confirm=None,
                 max_rounds=8, max_seconds=300, log_dir=None, page_map=None, scan="auto", bug_store=None):
        self.page, self.browser, self.config = page, browser, config
        self.bug_store = bug_store   # the app's bug & story list (jira_items.json): a found bug is drafted there
        self.base_url, self.tests_root, self.storage_state = base_url, tests_root, storage_state
        self.emit, self.confirm = emit, confirm
        # Secrets in the test data (a project's saved password) are kept here and the planner
        # is given {secret:NAME} instead: the model never sees them, the test files never hold them.
        self.secrets = {k: str(v) for k, v in (test_data or {}).items() if SECRET_FIELD.search(k) and v}
        self.test_data = {k: ("{secret:%s}" % k if k in self.secrets else v) for k, v in (test_data or {}).items()}
        self.max_rounds, self.max_seconds, self.log_dir = max_rounds, max_seconds, log_dir
        self.planner = Planner(config, provider_name, emit=emit)
        self.history = []   # short summaries of earlier tasks in this chat
        # What the agent knows about the app's pages (qm_map); per project when given a path.
        self.page_map = page_map if page_map is not None else PageMap()
        self.scan = scan     # "auto": learn a fresh app's main pages before the first plan

    def run_task(self, task):
        started = time.monotonic()
        explorer = Explorer(self.page, base_url=self.base_url, config=self.config, emit=self.emit,
                            confirm=self.confirm, page_map=self.page_map, secrets=self.secrets)
        if self.log_dir:
            explorer.evidence_dir = os.path.join(self.log_dir, "evidence")
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
                self._learn_app(task)
                explorer.flow._hook(self.page)      # the scan drove the page with a Flow of its own: this one decides again
        elif is_exploration(task):
            return {"task": task, "steps": 0, "authoring_s": 0, "stop_reason": None, "saved": None, "replay": None,
                    "ask": "Which site should I explore? Include its address, for example: "
                           "Explore https://marmelab.com/atomic-crm-demo/ and list its pages and forms."}
        problem, meta, plan_items, stop_reason = None, {"flow": "agent", "title": ""}, [], None
        failed_checks = {}           # a check the page failed, and how often: the same one twice is the app's answer
        for round_no in range(self.max_rounds):
            if time.monotonic() - started > self.max_seconds:
                stop_reason = f"stopped after {self.max_seconds} s"
                break
            obs = observe(explorer.page)       # the tab in front (a step may have opened a new one)
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
                if intent.get("do") == "wait" and not _ASKS_TO_WAIT.search(task):
                    # A pause the task never asked for: a planner's guess at timing. Steps wait for
                    # what they need themselves; a fixed pause would only make the test slower.
                    self.trace.append({"intent": public_intent(intent), "ok": True, "ms": 0, "how": "skipped",
                                       "reason": None, "detail": "a pause the task did not ask for", "weak": None})
                    continue
                item = {"step": _intent_text(intent), "status": "active"}
                plan_items.append(item)
                self._emit_plan(plan_items, stream)
                self.emit({"event": "tool_call", "tool": "step", "args": public_intent(intent)})
                outcome = explorer.run_intent(intent)   # asks self.confirm before destructive actions
                ran += 1
                self.trace.append({"intent": public_intent(intent), "ok": outcome["ok"], "ms": outcome.get("ms"),
                                   "how": outcome.get("how"), "reason": outcome.get("reason"),
                                   "detail": outcome.get("detail"), "weak": outcome.get("weak"),
                                   "url": outcome.get("url"), "screenshot": outcome.get("screenshot"),
                                   "issues": outcome.get("issues"), "done_steps": len(explorer.steps)})
                self.emit({"event": "tool_result", "tool": "step", "outcome": "ok" if outcome["ok"] else "error",
                           "summary": json.dumps(_brief(outcome), ensure_ascii=False)[:1500]})
                item["status"] = "done" if outcome["ok"] else "failed"
                self._emit_plan(plan_items, stream)
                if not outcome["ok"]:
                    problem = {"step": public_intent(intent), **_brief(outcome)}
                    stream.cancel()   # the rest was planned for a page that turned out different
                    if outcome.get("reason") == "check_failed":
                        key = _same_check(intent)
                        failed_checks[key] = failed_checks.get(key, 0) + 1
                        if failed_checks[key] >= 2:
                            # The planner asked the same question twice and the page said no twice:
                            # that is a finding about the app, not a plan to keep correcting.
                            stop_reason = f"the page fails the same check twice: {_intent_text(intent)}"
                    break
            if stop_reason:
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

    def _learn_app(self, task, max_pages=12, max_seconds=20):
        """A fresh app: read its navigation, menus and "New ..." pages first (nothing is typed
        or submitted), so the plan can use the real labels of pages not yet on screen."""
        item = [{"step": "Learn the app's main pages", "status": "active"}]
        self.emit({"event": "plan", "steps": item})
        scanned = quick_scan(self.page, self.page_map, max_pages=max_pages, max_seconds=max_seconds, emit=self.emit,
                             focus=task, max_depth=2)
        self.scan_ms = scanned["ms"]
        item[0].update(status="done", step=f"Learned {scanned['pages']} pages of the app")
        self.emit({"event": "plan", "steps": item})

    def _explore(self, task, started):
        """'Explore the app': map its pages (following links only) instead of authoring a test."""
        page_map = self.page_map
        if page_map.path and self.base_url and _origin(self.page.url) != _origin(self.base_url):
            page_map = PageMap()   # another site: keep it out of this project's memory
        reveal = not _LINKS_ONLY.search(task)   # "follow links only" keeps it to plain links
        scanned = quick_scan(self.page, page_map, max_pages=60, max_seconds=120, emit=self.emit,
                             reveal=reveal, focus=task, max_depth=3, reveals_per_page=10)
        result = {"task": task, "steps": 0, "authoring_s": round(time.monotonic() - started, 1),
                  "stop_reason": None, "saved": None, "replay": None,
                  "explored": {**scanned, "s": round(scanned["ms"] / 1000, 1), "reveal": reveal,
                               "lines": describe_pages(page_map, limit=60)}}
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
        # The same check twice in a row (a re-plan repeating itself) says nothing more.
        steps[:] = [s for i, s in enumerate(steps)
                    if not (i and s["op"].startswith("expect") and render(s) == render(steps[i - 1]))]
        trace = getattr(self, "trace", [])
        calls = list(getattr(self.planner, "timings", []))
        checks = [s for s in steps if s["op"].startswith("expect")]
        result = {"task": task, "steps": len(steps), "authoring_s": round(time.monotonic() - started, 1),
                  "stop_reason": stop_reason or error, "saved": None, "replay": None,
                  "timing": {"planner_calls": len(calls), "planner_s": round(sum(c["ms"] for c in calls) / 1000, 1),
                             "first_step_s": (round(calls[0]["first_step_ms"] / 1000, 1)
                                              if calls and calls[0].get("first_step_ms") is not None else None),
                             "scan_s": round(self.scan_ms / 1000, 1) if getattr(self, "scan_ms", None) else None,
                             "browser_s": round(sum((t.get("ms") or 0) for t in trace) / 1000, 1),
                             "replay_s": None},
                  "replanned": [{"step": _intent_text(t["intent"]), "reason": t["reason"], "detail": t["detail"]}
                                for t in trace if not t["ok"]],
                  "checks": {"total": len(checks), "weak": [{"check": s.get("name"), "why": s["weak"]}
                                                           for s in checks if s.get("weak")]},
                  "notes": list(dict.fromkeys(n for n in explorer.notes if not n.startswith("Check "))),
                  "unique": sorted(k for k, v in data.items() if "{unique}" in str(v)),
                  "secrets": sorted(k for k, v in data.items() if "{secret:" in str(v))}
        try:
            explorer.cleanup()      # close tabs the task opened; the session's page stays
        except Exception:
            pass
        meaningful = [s for s in steps if s["op"] != "goto"]
        if error or not meaningful:
            result["summary"] = error or stop_reason or "Nothing was recorded."
            self.history.append(f"{task[:80]} -> not recorded")
            return result
        if stop_reason:
            result["finding"] = self._finding(task, steps, trace)
        # Accept the test only if it runs on its own -- twice, in fresh browsers. The second
        # run is what a real backend needs: it meets the data the first one left behind.
        runs = []
        for _ in range(REPLAYS):
            verify = replay(self.browser, steps, data, base_url=self.base_url, storage_state=self.storage_state,
                            secrets=explorer.secrets, context_options={"viewport": self.page.viewport_size}
                            if self.page.viewport_size else None)
            runs.append(verify)
            if not verify["ok"]:
                break
        ok = all(r["ok"] for r in runs) and len(runs) == REPLAYS
        result["replay"] = {"ok": ok, "ms": sum(r["ms"] for r in runs), "runs": [r["ok"] for r in runs],
                            "failed_step": runs[-1]["failed_step"], "failed_run": None if ok else len(runs)}
        result["timing"]["replay_s"] = round(sum(r["ms"] for r in runs) / 1000, 1)
        if ok and not stop_reason and self.tests_root:
            flow = slug(meta.get("flow") or "agent")
            tc_id = next_tc_id(self.tests_root, flow)
            path = write_test(self.tests_root, flow, tc_id, steps, data,
                              description=meta.get("title") or task[:120], expected="", secrets=explorer.secrets)
            result["saved"] = {"tc_id": tc_id, "flow": flow, "path": path}
        result["code"] = [render(s) for s in steps]
        self.history.append(f"{task[:80]} -> {result['saved']['tc_id'] if result['saved'] else 'not saved'}")
        self._write_log(result, calls, trace)
        return result

    def _finding(self, task, steps, trace):
        """A task that ended on a check the app failed, not on a step the plan got wrong: a
        possible bug, written up so a developer can reproduce it -- the steps that led there,
        what was expected, what the page showed, a screenshot, what the app itself reported.
        None when nothing of the kind happened. Drafted into the app's bug list when there is one."""
        failed = [t for t in trace if not t["ok"] and t["reason"] == "check_failed"]
        if not failed:
            return None
        last = failed[-1]
        intent = last["intent"]
        what = intent.get("value") or intent.get("url") or intent.get("target") or ""
        where = (f" in {intent['within']}" if intent.get("within") else "")
        expected = (f"'{what}' is not shown{where}" if intent.get("present") is False else
                    f"'{what}' is shown{where}" if intent.get("do") in ("expect_text", "expect_page_text") else
                    f"{intent.get('target') or ''} {intent.get('do', '').replace('expect_', '')} {what}".strip())
        path = [render(s) for s in steps[:last.get("done_steps") or len(steps)]]
        words = [s.get("name") or s["op"] for s in steps[:last.get("done_steps") or len(steps)] if s["op"] != "goto"]
        finding = {"title": f"{expected} -- but it is not"[:120], "task": task, "expected": expected,
                   "actual": (last.get("detail") or "")[:600], "url": last.get("url"), "steps": words,
                   "code": path, "screenshot": last.get("screenshot"),
                   "issues": [f"{i['kind']}: {i['text']}" for i in (last.get("issues") or [])]}
        if self.bug_store:
            try:
                finding["draft"] = draft_bug(self.bug_store, finding)
            except Exception:
                finding["draft"] = None
        return finding

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


def draft_bug(store, finding):
    """Add the finding to the app's bug & story list (DATA_ROOT/jira_items.json, the Bug
    manager window's store) as a draft, with the screenshot attached. Returns its id."""
    import random
    items = []
    try:
        with open(store, encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else list(data.get("items") or [])
    except Exception:
        pass
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    repro = "\n".join(f"{n}. {s}" for n, s in enumerate(finding["steps"], 1)) or "(open the app)"
    description = (f"Found by QAmate while working on: {finding['task']}\n\n"
                   f"Steps to reproduce:\n{repro}\n\nExpected: {finding['expected']}\n"
                   f"Actual: {finding['actual']}\n\nPage: {finding.get('url') or ''}")
    if finding.get("issues"):
        description += "\n\nWhat the app reported meanwhile:\n" + "\n".join("- " + i for i in finding["issues"][:8])
    description += "\n\nReproduction as a test (QAmate step runtime):\n" + "\n".join(finding["code"])
    item = {"id": "JI-" + format(int(time.time() * 1000), "x") + format(random.randrange(16 ** 4), "04x"),
            "type": "bug", "title": finding["title"], "description": description, "labels": ["qamate", "found-by-agent"],
            "status": "draft", "attachments": [p for p in [finding.get("screenshot")] if p],
            "createdAt": now, "updatedAt": now}
    items.append(item)
    os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    with open(store, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    return item["id"]


def _same_check(intent):
    """What makes two checks the same question: the kind, what, and where."""
    return (intent.get("do"), str(intent.get("target") or ""), str(intent.get("value") or intent.get("url") or ""),
            str(intent.get("within") or ""), bool(intent.get("present", True)))


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
        found = f"**{e['pages']} pages**" + (f" ({e['new']} new)" if e.get("new") is not None and e["new"] != e["pages"] else "")
        if e.get("views"):
            found += f" and **{e['views']} views** behind menus, tabs and sections"
        how = ("opened menus, tabs and collapsible sections, followed links; nothing was typed or submitted"
               if e.get("reveal", False) else "followed links only; nothing was clicked or submitted")
        lines.append(f"Mapped {found} in {e['s']} s ({how}).\n")
        lines += e["lines"]
        if e.get("queued"):
            lines.append(f"\n{e['queued']} more links are queued — send **Explore more** to continue from here.")
        lines.append("\nI'll plan with these exact labels from now on. Ask me to write a test for any of these flows.")
        return "\n".join(lines)
    replay_info = result.get("replay") or {}
    if result.get("saved"):
        s = result["saved"]
        lines.append(f"Saved **{s['tc_id']}** in flow `{s['flow']}` — {result['steps']} steps, "
                     f"authored in {result['authoring_s']} s.")
        lines.append(f"Ran it {len(replay_info.get('runs') or [1])} times in fresh browsers: **passed** "
                     f"({replay_info['ms'] / 1000:.1f} s).")
    elif replay_info and not replay_info["ok"]:
        f = replay_info["failed_step"] or {}
        again = replay_info.get("failed_run", 1) and replay_info.get("failed_run", 1) > 1
        lines.append(("The test passed once in a fresh browser but **failed when run again**" if again
                      else "The flow ran, but replaying it in a fresh browser **failed**") +
                     f" at step {f.get('index', 0) + 1} ({f.get('name') or f.get('op')}): {f.get('error', '')}")
        if not result.get("unique"):
            lines.append("If the app keeps its data between runs, the earlier run may have used these values up "
                         "(a record with the same name already exists). Ask me to use a unique value for what "
                         "the test creates.")
        lines.append("Not saved. Tell me what to change, or ask me to try again.")
    else:
        lines.append(f"Not saved: {result.get('summary') or result.get('stop_reason') or 'the task did not finish'}.")
    if result.get("stop_reason") and result.get("saved") is None and result.get("replay"):
        lines.append(f"Stopped early: {result['stop_reason']}.")
    finding = result.get("finding")
    if finding:
        lines.append(f"\n**Possible bug:** {finding['expected']}, but the page did not show that.")
        lines.append(f"What it showed: {finding['actual'][:300]}")
        if finding["steps"]:
            lines.append("To reproduce: " + " → ".join(finding["steps"][:12]))
        if finding.get("issues"):
            lines.append("The app reported meanwhile: " + "; ".join(finding["issues"][:3]))
        if finding.get("screenshot"):
            lines.append(f"Screenshot: {finding['screenshot']}")
        lines.append("Saved as a draft in Bugs & stories (with the steps as a test)." if finding.get("draft")
                     else "Not drafted as a bug: no project bug list here.")
    checks = result.get("checks") or {}
    if checks.get("total"):
        weak = checks.get("weak") or []
        lines.append(f"Checks: {checks['total'] - len(weak)} of {checks['total']} verify something the steps changed"
                     + ("; the rest only confirm what was already there:" if weak else "."))
        for w in weak[:5]:
            lines.append(f"- {w['check']} — {w['why']}")
    elif result.get("saved"):
        lines.append("Checks: none. This test only proves the steps can be clicked through.")
    if result.get("unique"):
        lines.append(f"New value on every run for: {', '.join(result['unique'])} (so the test can be re-run).")
    if result.get("secrets"):
        lines.append(f"Kept out of the test files: {', '.join(result['secrets'])} "
                     f"(stored in `secrets.local.json` beside the tests, not in git).")
    for note in (result.get("notes") or [])[:4]:
        lines.append(f"Note: {note}")
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
