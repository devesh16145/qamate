"""Element choice when grounding has no clear winner.

Jev (TypeSafe's typed decision model) is used the way its documentation recommends: a
single atomic choice over a short, ranked list -- the step, plus one line per candidate
element -- with an explicit "none of these" option. No full page, no overall goal.
A generic chat model can stand in when no decision model is configured.
"""
import json
import os
import time

from llm import _http_post_json, make_provider, LLMError
from model_profiles import resolve_profile, ProfileError

DECISION_PROTOCOLS = {"typesafe", "openrouter_decisions"}
INSTRUCTIONS = ("A browser test is about to perform the step below. Choose the page element the step "
                "refers to. Each option describes one element: its role, label, state and what it is "
                "near. Choose 'none' if no option fits the step.")


def describe(step):
    op = step["op"]
    text = f'{op} "{step.get("target", "")}"'
    if step.get("within"):
        text += f' for/in "{step["within"]}"'
    if step.get("value") not in (None, "") and op in ("fill", "select"):
        text += f' with value "{step["value"]}"'
    return text


def decision_profile(config):
    name = (((config or {}).get("llm") or {}).get("roles") or {}).get("decision")
    if not name:
        return None, None
    try:
        return resolve_profile(config, name, "decision")
    except ProfileError:
        return None, None


def choose(config, step, candidates, *, page_title="", page_path="", emit=lambda e: None):
    """Pick one of `candidates` (best-first) for `step`.

    Returns {"index": int|None, "confidence": float|None, "by": "jev"|"llm"|None}.
    index None means 'none of these' or no model available.
    """
    if not candidates:
        return {"index": None, "confidence": None, "by": None}
    options = {f"c{i}": c.element.summary()[:240] for i, c in enumerate(candidates)}
    options["none"] = "None of these elements is the one the step means"
    name, cfg = decision_profile(config)
    started = time.monotonic()
    if cfg and cfg.get("protocol") in DECISION_PROTOCOLS:
        key = os.environ.get(cfg.get("api_key_env") or "", "")
        if not key:
            return {"index": None, "confidence": None, "by": None, "error": "decision key not set"}
        gateway = cfg["protocol"] == "openrouter_decisions"
        payload = {"model": cfg["model"],
                   "state": {"step": describe(step), "page": {"title": page_title, "path": page_path}},
                   "questions": {"element": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": options}}}
        base = cfg["base_url"].rstrip("/")
        try:
            result = _http_post_json(base + ("/decisions" if gateway else "/systemone"),
                                     {"Authorization": f"Bearer {key}"}, payload, timeout=cfg.get("timeout", 30))
        except LLMError as exc:
            return {"index": None, "confidence": None, "by": "jev", "error": str(exc)[:300]}
        usage = result.get("usage") or {}
        emit({"event": "model_usage", "role": "decision", "profile": name, "model": cfg["model"],
              "input": usage.get("input_tokens", usage.get("prompt_tokens")),
              "output": usage.get("output_tokens", usage.get("completion_tokens")),
              "duration_ms": round((time.monotonic() - started) * 1000)})
        answer = (result.get("answers") or {}).get("element") or {}
        choice, confidence = answer.get("choice"), answer.get("confidence")
        index = int(choice[1:]) if isinstance(choice, str) and choice.startswith("c") and choice[1:].isdigit() else None
        return {"index": index if index is not None and index < len(candidates) else None,
                "confidence": confidence, "by": "jev"}
    if not name:
        # No decision model configured: the planner gets the candidates instead. Using the
        # (slow, reasoning) planner model as a chooser costs as much as a re-plan and tells
        # the planner nothing.
        return {"index": None, "confidence": None, "by": None}
    # Generic chat model in the decision role (no confidence available).
    try:
        provider = make_provider(config, name, role="decision")
        prompt = json.dumps({"step": describe(step), "options": options,
                             "answer_format": {"choice": "one option key"}})
        raw = provider.complete(INSTRUCTIONS, prompt + "\nRespond with only JSON.", max_tokens=200, temperature=0)
        choice = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("choice")
    except Exception as exc:
        return {"index": None, "confidence": None, "by": "llm", "error": str(exc)[:300]}
    emit({"event": "model_usage", "role": "decision", "profile": name or "planner",
          "duration_ms": round((time.monotonic() - started) * 1000)})
    index = int(choice[1:]) if isinstance(choice, str) and choice.startswith("c") and choice[1:].isdigit() else None
    return {"index": index if index is not None and index < len(candidates) else None, "confidence": None, "by": "llm"}
