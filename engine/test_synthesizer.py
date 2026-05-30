"""
Agrim ATS — Test Synthesizer (L3)
=================================

The headline: turns `requirements.json` (L2) + `app_model.json` (L1) into
runnable, **self-healing** pytest tests in the existing checkpoint schema.

Degrade-gracefully, like the PRD extractor:
  - LLM path (when a provider key is configured): the model maps each
    requirement's acceptance criteria onto concrete steps + assertions against
    REAL elements in the app model, returning a structured "plan".
  - Deterministic fallback (no key/mock): for each requirement we pick the
    best-matching page, emit a "page loads" checkpoint + "element present"
    checkpoints for app-model elements whose names match the requirement, and
    record each acceptance criterion as a SKIP TODO (we never fabricate an
    assertion we can't ground — honours the data-collector principle).

Emitted tests:
  - use the `heal(...)` fixture (self-healing) with each element's
    primary/fallbacks/fingerprint inlined straight from the app model, so they
    are self-healing *by construction*;
  - drive verdicts through the existing `CheckpointRunner` + pass-criteria;
  - reuse `recorder_parser._generate_assertion_code` for web-first assertions;
  - run through the existing runner/results pipeline (no new execution path).

Output: tests/flows/<flow_id>/{test_<flow_id>.py, test_cases.json}

CLI:
  python test_synthesizer.py <project_id> [--flow <id>] [--provider <name>]
"""

import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
import llm as llm_mod
from recorder_parser import _generate_assertion_code

SCHEMA_VERSION = 1
_STOPWORDS = {"the", "a", "an", "to", "be", "able", "with", "and", "or", "of", "for",
              "user", "should", "must", "can", "is", "in", "on", "by", "as", "their", "this"}


def _slug(s, fallback="flow"):
    out = re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")
    return out[:40] or fallback


def _keywords(text):
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOPWORDS}


def _best_page(req, pages, base_url):
    """Pick the app-model page most relevant to a requirement (keyword overlap
    of its area+statement against page title/url). Falls back to the home page."""
    want = _keywords(req.get("area", "")) | _keywords(req.get("statement", ""))
    best, best_score = None, 0
    for p in pages:
        have = _keywords(p.get("title", "")) | _keywords(p.get("url", ""))
        score = len(want & have)
        if score > best_score:
            best, best_score = p, score
    if best:
        return best
    # default: the shallowest page (usually home)
    return min(pages, key=lambda p: p.get("depth", 0)) if pages else {"url": base_url, "elements": []}


def _matching_elements(req, page, limit=3):
    """App-model elements on a page whose name overlaps the requirement."""
    want = _keywords(req.get("statement", "")) | _keywords(req.get("area", ""))
    scored = []
    for el in page.get("elements", []):
        score = len(want & _keywords(el.get("name", "")))
        if score:
            scored.append((score, el))
    scored.sort(key=lambda t: -t[0])
    return [el for _, el in scored[:limit]]


# ── Deterministic fallback plan ──────────────────────────────────────────────

def _deterministic_plan(req, pages, base_url):
    page = _best_page(req, pages, base_url)
    els = _matching_elements(req, page)
    checkpoints = [{"kind": "loads", "name": f"Open and load: {req.get('area', 'page')}", "url": page.get("url", base_url), "severity": "critical"}]
    for el in els:
        checkpoints.append({"kind": "present", "name": f"'{el.get('name', el.get('ref'))}' is present",
                            "element": el, "severity": "normal"})
    for ac in req.get("acceptance", []):
        checkpoints.append({"kind": "skip", "name": f"Acceptance: {ac}",
                            "reason": "needs an assertion — configure an LLM key or fill in during review"})
    return {
        "tc_id": req.get("id", "REQ-X").replace("REQ", "TC"),
        "description": req.get("statement", "")[:200],
        "requirement_id": req.get("id"),
        "page_url": page.get("url", base_url),
        "steps": [],
        "checkpoints": checkpoints,
        "mapped": bool(els),
    }


# ── LLM plan ─────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a senior test automation engineer. Given a requirement and a map of "
    "real pages/elements in the app, produce ONE test plan as JSON:\n"
    '{"tc_id","page_url","steps":[{"action":"fill|click|check|select","element_ref":"<ref>","value":"<if needed>"}],'
    '"checkpoints":[{"name","assert":"url_contains|page_contains_text|element_visible","value":"...","element_ref":"<for element_visible>","severity":"critical|normal|minor"}]}\n'
    "Only reference element_ref values that exist in the provided page. Prefer "
    "objective assertions. Map every acceptance criterion to a checkpoint."
)


def _llm_plan(req, page, provider):
    compact_page = {
        "url": page.get("url"),
        "title": page.get("title"),
        "elements": [{"ref": e["ref"], "role": e.get("role"), "name": e.get("name"), "tag": e.get("tag")}
                     for e in page.get("elements", [])][:60],
    }
    user = ("Requirement:\n" + json.dumps(req, indent=2) +
            "\n\nPage (elements you may reference by ref):\n" + json.dumps(compact_page, indent=2))
    plan = llm_mod.complete_json(provider, _SYSTEM, user, max_tokens=2048)
    if not isinstance(plan, dict):
        raise llm_mod.LLMError("plan not an object")
    plan.setdefault("tc_id", req.get("id", "REQ-X").replace("REQ", "TC"))
    plan["requirement_id"] = req.get("id")
    plan.setdefault("page_url", page.get("url"))
    plan.setdefault("steps", [])
    plan.setdefault("checkpoints", [])
    plan["mapped"] = True
    plan["_llm"] = True
    return plan


# ── Code emission ────────────────────────────────────────────────────────────

def _el_by_ref(page, ref):
    for e in page.get("elements", []):
        if e.get("ref") == ref:
            return e
    return None


def _heal_call(el, indent="        "):
    """A heal(...).resolve() expression for an app-model element (self-healing)."""
    primary = repr(el.get("primary", {"by": "css", "value": el.get("tag", "*")}))
    fallbacks = repr(el.get("fallbacks", []))
    fingerprint = repr(el.get("fingerprint", {}))
    return f"heal(page, {primary}, fallbacks={fallbacks}, fingerprint={fingerprint}).resolve()"


def _emit_function(plan, page):
    tc_id = plan["tc_id"]
    func = f"test_{tc_id.replace('-', '_')}"
    L = [f'@pytest.mark.tc("{tc_id}")',
         f'def {func}(page: Page, base_url, heal, checkpoints):',
         f'    """{plan.get("description", "")}\n\n    Synthesized from {plan.get("requirement_id", "?")}."""',
         f'    page.goto({json.dumps(plan.get("page_url", ""))}, wait_until="domcontentloaded")',
         '    try:',
         '        page.wait_for_load_state("networkidle", timeout=5000)',
         '    except Exception:',
         '        pass']

    # Steps (LLM path) — each becomes a critical checkpoint that performs an action
    for i, step in enumerate(plan.get("steps", []), 1):
        el = _el_by_ref(page, step.get("element_ref", ""))
        if not el:
            continue
        action = step.get("action", "click")
        value = step.get("value", "")
        if action == "fill":
            act_code = f'{_heal_call(el)}.fill({json.dumps(str(value))})'
        elif action == "select":
            act_code = f'{_heal_call(el)}.select_option({json.dumps(str(value))})'
        elif action == "check":
            act_code = f'{_heal_call(el)}.check(force=True)'
        else:
            act_code = f'{_heal_call(el)}.click()'
        L += [f'    def _step{i}():',
              f'        {act_code}',
              f'        page.wait_for_timeout(400)',
              f'    checkpoints.run({json.dumps(f"{action.title()} {el.get('name', el.get('ref'))}")}, _step{i}, severity="critical")']

    # Checkpoints
    for i, cp in enumerate(plan.get("checkpoints", []), 1):
        name = cp.get("name", f"check {i}")
        sev = cp.get("severity", "normal")
        kind = cp.get("kind")
        if kind == "skip":
            L.append(f'    checkpoints.skip({json.dumps(name)}, {json.dumps(cp.get("reason", "TODO"))})')
            continue
        if kind == "loads":
            L += [f'    def _cp{i}():',
                  f'        assert page.url, "page did not load"',
                  f'        expect(page.locator("body")).to_be_visible(timeout=8000)',
                  f'    checkpoints.run({json.dumps(name)}, _cp{i}, severity="critical")']
            continue
        if kind == "present" and cp.get("element"):
            L += [f'    def _cp{i}():',
                  f'        {_heal_call(cp["element"], indent="        ")}',
                  f'    checkpoints.run({json.dumps(name)}, _cp{i}, severity={json.dumps(sev)})']
            continue
        # LLM assertion checkpoint → reuse recorder_parser._generate_assertion_code
        assertion = {"type": cp.get("assert", "page_contains_text"), "value": cp.get("value", ""), "timeout": 8000}
        if cp.get("assert") == "element_visible":
            el = _el_by_ref(page, cp.get("element_ref", ""))
            if el and el.get("primary", {}).get("by") == "css":
                assertion["selector"] = el["primary"]["value"]
            else:
                # express via heal()-resolved element instead of a raw selector
                L += [f'    def _cp{i}():',
                      f'        expect({_heal_call(el or {})}).to_be_visible(timeout=8000)',
                      f'    checkpoints.run({json.dumps(name)}, _cp{i}, severity={json.dumps(sev)})']
                continue
        code = _generate_assertion_code(assertion)
        L += [f'    def _cp{i}():']
        for line in code.split("\n"):
            L.append(f'        {line}')
        L.append(f'    checkpoints.run({json.dumps(name)}, _cp{i}, severity={json.dumps(sev)})')

    L.append('    checkpoints.finalize()')
    return "\n".join(L)


_FILE_HEADER = (
    '"""{flow} — auto-synthesized self-healing tests (Agrim ATS L3).\n\n'
    'Generated from the project PRD + app model. Review before relying on them:\n'
    'steps/assertions come from an LLM (or a deterministic scaffold without a key).\n'
    '"""\n\n'
    "import re\n"
    "import pytest\n"
    "from playwright.sync_api import expect, Page\n\n\n"
)


def synthesize(ats_root, project_id, flow_id=None, config=None, provider_name=None, on_log=None):
    def log(m):
        (on_log or (lambda s: print(s, flush=True)))(str(m))

    project = project_store.get_project(ats_root, project_id)
    if not project:
        return {"status": "error", "message": f"project '{project_id}' not found"}
    base_url = project_store.resolve_base_url(project)

    model = _load(project_store.app_model_path(ats_root, project_id))
    if not model:
        return {"status": "error", "message": "no app_model.json — run the App Explorer first"}
    reqs_doc = _load(project_store.requirements_path(ats_root, project_id))
    requirements = (reqs_doc or {}).get("requirements", []) if isinstance(reqs_doc, dict) else (reqs_doc or [])
    if not requirements:
        return {"status": "error", "message": "no requirements.json — run the PRD Extractor first"}

    pages = model.get("pages", [])
    flow_id = _slug(flow_id or project.get("name") or project_id)

    provider = None
    try:
        provider = llm_mod.make_provider(config or {}, provider_name)
        use_llm = provider.name != "mock" or os.environ.get("ATS_LLM_MOCK_RESPONSE")
    except Exception:
        use_llm = False

    plans, gaps = [], []
    for req in requirements:
        page = _best_page(req, pages, base_url)
        plan = None
        if use_llm and provider is not None:
            try:
                plan = _llm_plan(req, page, provider)
            except (llm_mod.LLMNotConfigured, llm_mod.LLMError) as e:
                log(f"[synth] LLM plan failed for {req.get('id')} ({e}); using deterministic scaffold")
        if plan is None:
            plan = _deterministic_plan(req, pages, base_url)
        if not plan.get("mapped"):
            gaps.append({"requirement_id": req.get("id"), "reason": "no matching elements found on best-match page"})
        plans.append((req, plan, page))

    # Emit the test file + test_cases.json
    flow_dir = os.path.join(ats_root, "tests", "flows", flow_id)
    os.makedirs(flow_dir, exist_ok=True)
    open(os.path.join(flow_dir, "__init__.py"), "a").close()

    body = _FILE_HEADER.format(flow=flow_id)
    tc_entries = []
    for req, plan, page in plans:
        body += _emit_function(plan, page) + "\n\n\n"
        tc_entries.append({
            "tc_id": plan["tc_id"],
            "module": flow_id,
            "description": plan.get("description", ""),
            "preconditions": f"Project '{project.get('name', project_id)}' reachable; logged in if required",
            "checkpoints": [c.get("name", "") for c in plan.get("checkpoints", [])],
            "requirement_id": plan.get("requirement_id"),
            "synthesized": True,
        })

    test_py = os.path.join(flow_dir, f"test_{flow_id}.py")
    with open(test_py, "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.path.join(flow_dir, "test_cases.json"), "w", encoding="utf-8") as f:
        json.dump(tc_entries, f, indent=2)

    log(f"[synth] wrote {len(tc_entries)} test(s) -> {test_py}")
    return {
        "status": "success",
        "flow_id": flow_id,
        "test_file": test_py,
        "count": len(tc_entries),
        "tc_ids": [e["tc_id"] for e in tc_entries],
        "llm": bool(use_llm),
        "coverage_gaps": gaps,
    }


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"status": "error", "message": "usage: test_synthesizer.py <project_id> [--flow <id>] [--provider <name>]"}))
        return 1
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_id = argv[1]
    flow_id = argv[argv.index("--flow") + 1] if "--flow" in argv else None
    provider = argv[argv.index("--provider") + 1] if "--provider" in argv else None
    config = _load(os.path.join(ats_root, "config.json")) or {}
    res = synthesize(ats_root, project_id, flow_id=flow_id, config=config, provider_name=provider,
                     on_log=lambda m: print(json.dumps({"event": "log", "message": m}), flush=True))
    print(json.dumps({"event": "result", **res}))
    return 0 if res.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
