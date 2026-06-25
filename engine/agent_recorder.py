"""
QAmate — Autonomous Recorder (AI replaces the manual Record step)
====================================================================

Instead of a human clicking through the app in Playwright codegen, an LLM
(MiMo by default) drives a real browser to accomplish a goal — a scenario taken
from a PRD/Jira story — recording each action as a Playwright step (+ objective
checkpoints), then handing the result to the EXISTING recorder pipeline
(`recorder_parser.generate_from_review`). The output is an ordinary test case in
an existing flow: it runs, traces, and supports data variants exactly like a
hand-recorded one. This is NOT a new system — it's a new *driver* for the one we
already have.

Reuses: dom_inspector (DOM snapshots), app_explorer.element_to_model (refs +
self-healing strategies), smart_locator (robust execution), llm (provider),
recorder_parser.generate_from_review (codegen).

Decision protocol — at each step the model returns ONE action as JSON:
  {"action":"click|fill|select|navigate|done",
   "ref":"<element ref from the list>", "value":"<text/option/url>",
   "checkpoint":{"name":"...", "assert":"url_contains|page_contains_text", "value":"..."},
   "note":"short reason"}

CLI:
  python agent_recorder.py <project_id> --goal "..." --tc TC-X-001 --flow myflow \
      [--start /login] [--provider mimo] [--max-steps 12] [--headed]
"""

import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
import llm as llm_mod
from dom_inspector import snapshot_page, DESTRUCTIVE_KEYWORDS
from app_explorer import element_to_model
from smart_locator import smart_locator, SelfHealError
from recorder_parser import generate_from_review

_SYSTEM = (
    "You are a meticulous QA engineer driving a REAL browser to accomplish a goal "
    "and produce an automated UI test. Each step you get the current URL and the "
    "interactive elements on the page (each has a 'ref'). Choose the SINGLE next "
    "action and respond with ONLY this JSON (no prose):\n"
    '{"action":"click|fill|select|navigate|done","ref":"<ref from the list>",'
    '"value":"<text to type / option / url>",'
    '"checkpoint":{"name":"<what is verified>","assert":"url_contains|page_contains_text","value":"<substring>"},'
    '"note":"<short reason>"}\n'
    "Rules: only use a 'ref' that appears in the list. Prefer the fewest steps. "
    "Add a checkpoint when a meaningful result should be verified (e.g. after submit). "
    "Never log out or perform destructive actions unless the goal explicitly requires it. "
    "Use action=done once the goal is fully achieved."
)


def _locator_str(primary):
    """Render an element's primary strategy as a Playwright expression string for
    the recorded step (generate_from_review consumes this)."""
    by = primary.get("by")
    v = primary.get("value", "")
    if by == "role":
        name = primary.get("name")
        return f'page.get_by_role("{primary.get("role")}", name="{_q(name)}")' if name else f'page.get_by_role("{primary.get("role")}")'
    if by == "text":
        return f'page.get_by_text("{_q(v)}")'
    if by == "placeholder":
        return f'page.get_by_placeholder("{_q(v)}")'
    if by == "label":
        return f'page.get_by_label("{_q(v)}")'
    if by == "test_id":
        return f'page.get_by_test_id("{_q(v)}")'
    return f'page.locator("{_q(v)}")'


def _q(s):
    # Escape for a Python double-quoted string literal — including newlines/tabs,
    # so a multi-line element name can never break the generated code.
    return ((s or "").replace('\\', '\\\\').replace('"', '\\"')
            .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'))


def _compact_elements(models, limit=45):
    """Compact element list for the prompt (skip destructive/logout controls)."""
    out = []
    for m in models:
        label = (m.get("name") or "").lower()
        if any(kw in label for kw in DESTRUCTIVE_KEYWORDS) or "logout" in label or "sign out" in label:
            continue
        out.append({"ref": m["ref"], "role": m.get("role") or m.get("tag"), "name": m.get("name", "")[:60],
                    "type": m.get("input_type") or ""})
        if len(out) >= limit:
            break
    return out


def record_scenario(ats_root, project_id, goal, tc_id, flow_id, start_path="",
                    max_steps=12, provider_name=None, config=None, headless=True, on_log=None):
    """Drive the browser with the LLM to accomplish `goal`, recording a test case
    into flow `flow_id` via the existing recorder pipeline."""
    from playwright.sync_api import sync_playwright

    def log(m):
        (on_log or (lambda s: print(s, flush=True)))(str(m))

    project = project_store.get_project(ats_root, project_id)
    if not project:
        return {"status": "error", "message": f"project '{project_id}' not found"}
    base_url = project_store.resolve_base_url(project)
    start_url = base_url.rstrip("/") + "/" + start_path.lstrip("/") if start_path else base_url

    try:
        provider = llm_mod.make_provider(config or {}, provider_name)
    except Exception as e:
        return {"status": "error", "message": f"LLM provider error: {e}"}

    ss = project_store.storage_state_path(ats_root, project_id)
    storage_state = ss if os.path.exists(ss) else None

    steps, assertions, history = [], [], []
    log(f"[agent] goal: {goal}")
    log(f"[agent] start: {start_url} | provider: {provider.name} | auth: {'yes' if storage_state else 'none'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, channel="chrome", args=["--disable-gpu", "--no-sandbox"])
        ctx_args = {"no_viewport": True} if not headless else {"viewport": {"width": 1280, "height": 720}}
        if storage_state:
            ctx_args["storage_state"] = storage_state
        context = browser.new_context(**ctx_args)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(20000)
        page.goto(start_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

        # First recorded step: navigate.
        steps.append({"id": 1, "rawLine": f'page.goto("{_q(start_url)}")', "type": "navigate",
                      "target": start_url, "targetDescription": f"Navigate to {start_url}", "value": "", "varName": ""})

        input_counter = 1
        for n in range(max_steps):
            models = [element_to_model(el) for el in snapshot_page(page) if isinstance(el, dict) and "_error" not in el and (el.get("aria_label") or el.get("text") or el.get("placeholder") or el.get("name"))]
            by_ref = {m["ref"]: m for m in models}
            compact = _compact_elements(models)
            user = (f"Goal: {goal}\nCurrent URL: {page.url}\n"
                    f"Interactive elements:\n{json.dumps(compact, indent=1)}\n"
                    f"Steps so far:\n{json.dumps(history[-8:], indent=1)}\n"
                    "Next action?")
            try:
                # Generous budget: MiMo is a reasoning model and spends tokens
                # "thinking" before the final JSON action.
                d = llm_mod.complete_json(provider, _SYSTEM, user, max_tokens=3072)
            except Exception as e:
                log(f"[agent] LLM error: {e}; stopping")
                break
            action = (d.get("action") or "").lower()
            log(f"[agent] step {n + 1}: {action} ref={d.get('ref')} value={str(d.get('value'))[:40]!r} — {d.get('note', '')[:80]}")
            if action == "done" or not action:
                break

            sid = len(steps) + 1
            if action == "navigate" and d.get("value"):
                url = d["value"]
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    log(f"[agent]   navigate failed: {e}")
                steps.append({"id": sid, "rawLine": f'page.goto("{_q(url)}")', "type": "navigate",
                              "target": url, "targetDescription": f"Navigate to {url}", "value": "", "varName": ""})
                history.append(f"navigate {url}")
            else:
                el = by_ref.get(d.get("ref"))
                if not el:
                    log(f"[agent]   ref '{d.get('ref')}' not on page; skipping")
                    history.append(f"(skipped invalid ref {d.get('ref')})")
                    continue
                loc_str = _locator_str(el["primary"])
                try:
                    live = smart_locator(page, el["primary"], fallbacks=el.get("fallbacks"), fingerprint=el.get("fingerprint")).resolve()
                except SelfHealError as e:
                    log(f"[agent]   could not resolve element: {e}")
                    history.append(f"(could not act on {el.get('name')})")
                    continue
                if action == "fill":
                    val = str(d.get("value", ""))
                    var = f"input_{input_counter}"; input_counter += 1
                    try: live.fill(val)
                    except Exception as e: log(f"[agent]   fill failed: {e}")
                    steps.append({"id": sid, "rawLine": f'{loc_str}.fill("{_q(val)}")', "type": "fill",
                                  "target": loc_str, "targetDescription": f'Enter "{val}" in {el.get("name")}', "value": val, "varName": var})
                    history.append(f"fill '{el.get('name')}' = {val}")
                elif action == "select":
                    val = str(d.get("value", ""))
                    try: live.select_option(val)
                    except Exception as e: log(f"[agent]   select failed: {e}")
                    steps.append({"id": sid, "rawLine": f'{loc_str}.select_option("{_q(val)}")', "type": "select",
                                  "target": loc_str, "targetDescription": f'Select "{val}" in {el.get("name")}', "value": val, "varName": ""})
                    history.append(f"select '{el.get('name')}' = {val}")
                else:  # click
                    try: live.click()
                    except Exception as e: log(f"[agent]   click failed: {e}")
                    steps.append({"id": sid, "rawLine": f'{loc_str}.click()', "type": "click",
                                  "target": loc_str, "targetDescription": f'Click {el.get("name")}', "value": "", "varName": ""})
                    history.append(f"click '{el.get('name')}'")
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass

            # Checkpoint requested by the model → recorded as an assertion after this step
            cp = d.get("checkpoint")
            if isinstance(cp, dict) and cp.get("assert") in ("url_contains", "page_contains_text") and cp.get("value"):
                assertions.append({"type": cp["assert"], "value": cp["value"], "afterStep": steps[-1]["id"],
                                   "description": cp.get("name", cp["assert"])})
                log(f"[agent]   + checkpoint: {cp.get('name')} ({cp['assert']}: {cp['value']!r})")

        context.close()
        browser.close()

    if len(steps) <= 1:
        return {"status": "error", "message": "agent took no actions (check goal / LLM / auth)"}

    payload = {
        "tc_id": tc_id, "description": goal[:200], "flowId": flow_id,
        "preconditions": f"Project '{project.get('name', project_id)}' reachable; logged in if required",
        "expectedResult": "", "steps": steps, "assertions": assertions, "criteria": [],
    }
    res = generate_from_review(payload, ats_root)
    res.update({"recorded_steps": len(steps), "checkpoints": len(assertions), "goal": goal})
    log(f"[agent] recorded {len(steps)} steps, {len(assertions)} checkpoint(s) -> {res.get('flow')}/{tc_id}")
    return res


def autonomous(ats_root, project_id, flow_id=None, max_scenarios=10, max_steps=12,
               provider_name=None, config=None, on_log=None):
    """Record one test case per requirement in the project's requirements.json
    (from the PRD extractor / Jira stories)."""
    reqs_doc = project_store._read_json(project_store.requirements_path(ats_root, project_id)) \
        if hasattr(project_store, "_read_json") else None
    if reqs_doc is None:
        p = project_store.requirements_path(ats_root, project_id)
        reqs_doc = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    requirements = (reqs_doc or {}).get("requirements", []) if isinstance(reqs_doc, dict) else (reqs_doc or [])
    if not requirements:
        return {"status": "error", "message": "no requirements.json — run the PRD/Jira extractor first"}

    project = project_store.get_project(ats_root, project_id) or {}
    flow_id = flow_id or re.sub(r"[^a-z0-9]+", "_", (project.get("name") or project_id).lower()).strip("_") or "auto"
    results = []
    for req in requirements[:max_scenarios]:
        goal = req.get("statement", "")
        if req.get("acceptance"):
            goal += " Verify: " + "; ".join(req["acceptance"][:3])
        tc_id = req.get("id", "REQ-X").replace("REQ", "TC")
        results.append(record_scenario(ats_root, project_id, goal, tc_id, flow_id,
                                        max_steps=max_steps, provider_name=provider_name, config=config, on_log=on_log))
    ok = sum(1 for r in results if r.get("status") == "success")
    return {"status": "success", "flow_id": flow_id, "recorded": ok, "total": len(results), "results": results}


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"status": "error", "message": "usage: agent_recorder.py <project_id> (--goal '...' --tc ID --flow F | --auto) [--start P] [--provider P] [--max-steps N] [--headed]"}))
        return 1
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pid = argv[1]
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    config = json.load(open(os.path.join(ats_root, "config.json"), encoding="utf-8")) if os.path.exists(os.path.join(ats_root, "config.json")) else {}
    provider = opt("--provider")
    max_steps = int(opt("--max-steps", "12"))
    headless = "--headed" not in argv
    log = lambda m: print(json.dumps({"event": "log", "message": m}), flush=True)

    if "--auto" in argv:
        res = autonomous(ats_root, pid, flow_id=opt("--flow"), max_steps=max_steps, provider_name=provider, config=config, on_log=log)
    else:
        res = record_scenario(ats_root, pid, opt("--goal", "Explore the app"), opt("--tc", "TC-AUTO-001"),
                              opt("--flow", "auto"), start_path=opt("--start", ""), max_steps=max_steps,
                              provider_name=provider, config=config, headless=headless, on_log=log)
    print(json.dumps({"event": "result", **{k: v for k, v in res.items() if k != "results"}}))
    return 0 if res.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
