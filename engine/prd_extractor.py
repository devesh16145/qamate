"""
Agrim ATS — PRD Extractor (L2)
==============================

Turns a PRD (markdown) into structured, testable **requirements** that the Test
Synthesizer (L3) maps onto the App Model.

Two-stage, degrade-gracefully design:
  1. Deterministic pre-pass — headings → areas, modal sentences ("must/should")
     and bullets → requirement statements, "Acceptance" lists → criteria. Works
     with NO LLM and produces usable output on its own.
  2. LLM refinement (optional) — when a provider key is configured, the LLM
     rewrites the candidates into atomic, deduped, verifiable requirements and
     fills missing acceptance criteria. If the LLM is unconfigured/mock/errors,
     we keep the pre-pass output (the feature still works without a key).

Output: projects/<id>/requirements.json (or a path), schema:
  [{ "id","area","role","statement","acceptance":[...],"priority","source" }]

CLI:
  python prd_extractor.py <prd_path> [--project <id>] [--provider <name>]
"""

import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
import llm as llm_mod

SCHEMA_VERSION = 1

_MODAL_RE = re.compile(r'\b(must|shall|should|is able to|can|will be able to|needs to|required to|allow(?:s|ed)? to)\b', re.I)


def _slug(s, fallback="gen"):
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return out[:24] or fallback


def _priority(text):
    if re.search(r'\b(must|shall|required)\b', text, re.I):
        return "P1"
    if re.search(r'\b(may|optional|nice to have|could)\b', text, re.I):
        return "P3"
    return "P2"


def _guess_role(text):
    m = re.search(r'\bas an?\s+([a-z ]{3,20}?)[,;]', text, re.I)
    if m:
        return m.group(1).strip().title()
    for role in ("admin", "seller", "customer", "buyer", "manager", "user"):
        if re.search(rf'\b{role}\b', text, re.I):
            return role.title()
    return "User"


def prepass(md):
    """Deterministic requirement extraction from PRD markdown."""
    area = "General"
    reqs, cur = [], None
    in_acceptance = False
    counters = {}

    def new_id(a):
        slug = re.sub(r"[^A-Z0-9]+", "-", a.upper()).strip("-")[:16] or "GEN"
        counters[slug] = counters.get(slug, 0) + 1
        return f"REQ-{slug}-{counters[slug]:03d}"

    for raw in md.splitlines():
        s = raw.strip()
        if not s:
            in_acceptance = False
            continue
        h = re.match(r'^(#{1,6})\s+(.*)', s)
        if h:
            area = h.group(2).strip().rstrip("#").strip()
            cur = None
            in_acceptance = bool(re.search(r'acceptance', area, re.I))
            continue
        if re.match(r'^acceptance(\s+criteria)?\s*:?\s*$', s, re.I):
            in_acceptance = True
            continue
        bullet = re.match(r'^[-*+]\s+(.*)', s) or re.match(r'^\d+[.)]\s+(.*)', s)
        text = (bullet.group(1) if bullet else s).strip()
        text = re.sub(r'\*\*|__|`', '', text)  # strip md emphasis

        if in_acceptance and bullet and cur is not None:
            cur["acceptance"].append(text[:200])
            continue

        if _MODAL_RE.search(text) or (bullet and not in_acceptance):
            cur = {
                "id": new_id(area),
                "area": area,
                "role": _guess_role(text),
                "statement": text[:300],
                "acceptance": [],
                "priority": _priority(text),
                "source": f"PRD#{_slug(area)}",
            }
            reqs.append(cur)
            in_acceptance = False

    return [r for r in reqs if len(r["statement"]) >= 8]


_SYSTEM = (
    "You are a senior QA analyst. Convert a Product Requirements Document into a "
    "flat JSON array of ATOMIC, TESTABLE requirements. Each item: "
    '{"id","area","role","statement","acceptance":[strings],"priority":"P1|P2|P3","source"}. '
    "Split compound requirements; every acceptance criterion must be objectively "
    "verifiable in a UI test. Keep ids stable and kebab-ish (REQ-AREA-001). "
    "Do not invent features not implied by the PRD."
)


def _looks_like_requirements(data):
    return isinstance(data, list) and all(
        isinstance(x, dict) and x.get("statement") for x in data
    )


def extract(prd_path, project_id=None, config=None, provider_name=None, ats_root=None, on_log=None):
    """Extract requirements from a PRD. Writes requirements.json when project_id
    is given. Returns {status, requirements, refined_by}."""
    def log(m):
        (on_log or (lambda s: print(s, flush=True)))(str(m))

    ats_root = ats_root or os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not prd_path or not os.path.exists(prd_path):
        return {"status": "error", "message": f"PRD not found: {prd_path}"}

    with open(prd_path, "r", encoding="utf-8") as f:
        md = f.read()

    candidates = prepass(md)
    log(f"[prd] pre-pass extracted {len(candidates)} candidate requirement(s)")

    requirements, refined_by = candidates, "prepass"
    try:
        provider = llm_mod.make_provider(config or {}, provider_name)
        if provider.name != "mock":
            log(f"[prd] refining with {provider.name}…")
        user = (
            "PRD:\n\n" + md[:20000] +
            "\n\n---\nCandidate requirements (from a deterministic pass; refine, "
            "split, dedupe, and fill acceptance criteria):\n" +
            json.dumps(candidates, indent=2)[:8000]
        )
        refined = llm_mod.complete_json(provider, _SYSTEM, user, max_tokens=4096)
        if _looks_like_requirements(refined):
            requirements = refined
            refined_by = provider.name
            log(f"[prd] LLM refined to {len(refined)} requirement(s)")
        else:
            log("[prd] LLM output not in expected shape — keeping pre-pass output")
    except llm_mod.LLMNotConfigured as e:
        log(f"[prd] no LLM key ({e}) — using deterministic pre-pass output")
    except llm_mod.LLMError as e:
        log(f"[prd] LLM error ({e}) — using deterministic pre-pass output")

    result = {
        "status": "success",
        "schema_version": SCHEMA_VERSION,
        "prd_path": prd_path,
        "refined_by": refined_by,
        "count": len(requirements),
        "requirements": requirements,
    }

    if project_id:
        out = project_store.requirements_path(ats_root, project_id)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        result["requirements_path"] = out
        log(f"[prd] wrote {len(requirements)} requirement(s) -> {out}")

    return result


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"status": "error", "message": "usage: prd_extractor.py <prd_path> [--project <id>] [--provider <name>]"}))
        return 1
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prd_path = argv[1]
    project_id = argv[argv.index("--project") + 1] if "--project" in argv else None
    provider = argv[argv.index("--provider") + 1] if "--provider" in argv else None
    config = {}
    try:
        with open(os.path.join(ats_root, "config.json")) as f:
            config = json.load(f)
    except Exception:
        pass
    res = extract(prd_path, project_id=project_id, config=config, provider_name=provider,
                  ats_root=ats_root, on_log=lambda m: print(json.dumps({"event": "log", "message": m}), flush=True))
    print(json.dumps({"event": "result", **{k: v for k, v in res.items() if k != "requirements"}}))
    return 0 if res.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
