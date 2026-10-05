"""Element choice when grounding has no clear winner.

Grounding (qm_ground) acts on its own only when a control's name IS the step's target.
What is left -- a label that only resembles the target, a guess written for a page the
planner had not seen, several controls that fit equally -- is a closed question: which
of these few elements, if any, does the step mean? That is asked of a small, fast model:

  * a typed decision model (TypeSafe Jev natively, or any decision model on OpenRouter's
    decisions endpoint -- they share one request shape), or
  * a chat model asked for a single JSON key.

Small models favour the first option they are shown and seldom answer "none" unprompted
(Jev's own documentation says so, and so does the multiple-choice literature). So the
question is asked twice, in parallel, with the options in opposite orders, meaningless
option ids and an explicit NONE option that changes place. The answer counts only when
both runs choose the same element; a split is "not sure" and goes back to the planner.
The model's own confidence is recorded but is not what the decision rests on.
"""
import concurrent.futures
import json
import os
import time

from llm import _http_post_json, make_provider, LLMError
from model_profiles import load_catalog, resolve_profile, ProfileError

DECISION_PROTOCOLS = {"typesafe", "openrouter_decisions"}
INSTRUCTIONS = ("A browser test is about to perform the step below on a web page. Choose the one page element "
                "the step means. Each option describes one element: its role, its label, its state and what is "
                "around it. Choose the NONE option when no listed element is what the step means: a label that "
                "only shares some words with the step, or the right label on the wrong kind of control, is not "
                "a match.")
OPTION_IDS = ["kav", "mip", "tor", "zel", "dun", "raf", "wix", "bog", "yem", "hul", "pim", "sot"]   # no order, no meaning
NONE_ID = "none"
NONE_TEXT = "NONE - no listed element is the one the step means"


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


def arrangements(count):
    """Two orders of the same options: as ranked with NONE last, and reversed with NONE
    first. The best-ranked element is first in one and last in the other, and NONE is never
    in the same place, so an answer that follows position cannot agree with itself."""
    ids = OPTION_IDS[:count]
    forward = [(ids[i], i) for i in range(count)] + [(NONE_ID, None)]
    backward = [(NONE_ID, None)] + [(ids[i], i) for i in reversed(range(count))]
    return [forward, backward]


def quick_settings(cfg):
    """Request extras that turn a chat model's reasoning off: a choice between a few listed
    elements needs an answer in well under a second, not a think-through. Uses the preset's
    own "thinking off" switch, or OpenRouter's reasoning parameter; nothing for other endpoints."""
    if cfg.get("decision_reasoning"):                # a profile may opt back in
        return {}
    thinking = (load_catalog()["presets"].get(cfg.get("preset") or "") or {}).get("thinking") or {}
    if thinking.get("off"):
        return dict(thinking["off"].get("extra_body") or {})
    if "openrouter.ai" in str(cfg.get("base_url") or ""):
        return {"reasoning": {"effort": "none"}}
    return {}


def _complete(provider, system, prompt, max_tokens):
    """One chat completion with reasoning off where possible. An endpoint or model that
    rejects the switch is asked again without it."""
    quick = quick_settings(provider.cfg)
    if quick:
        plain = provider.cfg
        settings = dict(plain.get("model_settings") or {})
        settings["extra_body"] = {**(settings.get("extra_body") or {}), **quick}
        provider.cfg = {**plain, "model_settings": settings}
        try:
            return provider.complete(system, prompt, max_tokens=max_tokens, temperature=0)
        except LLMError as exc:
            if getattr(exc, "status_code", None) not in (400, 422):
                raise
            provider.cfg = plain
    return provider.complete(system, prompt, max_tokens=max_tokens, temperature=0)


def ask(config, name, cfg, step_text, options, page):
    """One call: {option id: text} in presentation order -> {"choice", "confidence", "usage", "ms"}.
    `choice` is an option id from `options`, or None when the model answered something else."""
    started = time.monotonic()
    if cfg.get("protocol") in DECISION_PROTOCOLS:
        key = os.environ.get(cfg.get("api_key_env") or "", "")
        if not key:
            raise LLMError("decision key not set")
        payload = {"model": cfg["model"], "state": {"step": step_text, "page": page},
                   "questions": {"element": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": options}}}
        path = "/decisions" if cfg["protocol"] == "openrouter_decisions" else "/systemone"
        result = _http_post_json(cfg["base_url"].rstrip("/") + path, {"Authorization": f"Bearer {key}"}, payload,
                                 timeout=cfg.get("timeout", 30))
        answer = (result.get("answers") or {}).get("element") or {}
        choice, confidence, usage = answer.get("choice"), answer.get("confidence"), result.get("usage") or {}
    else:
        provider = make_provider(config, name, role="decision")
        prompt = json.dumps({"step": step_text, "page": page, "options": options,
                             "answer_format": {"choice": "<one option id>"}}, ensure_ascii=False)
        raw = _complete(provider, INSTRUCTIONS, prompt + "\nRespond with only JSON.",
                        cfg.get("decision_max_tokens", 600)) or ""
        try:
            choice = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("choice")
        except (ValueError, AttributeError):
            choice = None
        confidence, usage = None, provider.last_usage or {}
    return {"choice": choice if choice in options else None, "answered": choice, "confidence": confidence,
            "usage": usage, "ms": round((time.monotonic() - started) * 1000)}


def family(cfg):
    if "jev" in str(cfg.get("model", "")).lower():
        return "jev"
    return "decision" if cfg.get("protocol") in DECISION_PROTOCOLS else "llm"


def vote(config, name, cfg, step_text, texts, page, rounds=2, emit=lambda e: None):
    """Ask in `rounds` arrangements at once. Returns {"index", "agreed", "votes", "confidence", "ms", "error"}:
    index is the element all runs chose (None for NONE, a split or an error)."""
    plans = arrangements(len(texts))[:max(1, rounds)]

    def run(plan):
        options = {key: (NONE_TEXT if index is None else texts[index]) for key, index in plan}
        last = None
        for _ in range(2):                       # one retry: a transient error must not cost a re-plan
            try:
                reply = ask(config, name, cfg, step_text, options, page)
                break
            except Exception as exc:
                last = exc
        else:
            return {"error": str(last)[:300]}
        usage = reply["usage"]
        emit({"event": "model_usage", "role": "decision", "profile": name, "model": cfg.get("model"),
              "input": usage.get("input_tokens", usage.get("prompt_tokens")),
              "output": usage.get("output_tokens", usage.get("completion_tokens")), "duration_ms": reply["ms"]})
        if reply["choice"] is None:
            return {"error": f"answered {reply['answered']!r}, which is not an option", "ms": reply["ms"]}
        return {"index": dict(plan)[reply["choice"]], "none": reply["choice"] == NONE_ID,
                "confidence": reply["confidence"], "ms": reply["ms"]}

    started = time.monotonic()
    if len(plans) == 1:
        votes = [run(plans[0])]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(plans)) as pool:
            votes = list(pool.map(run, plans))
    out = {"index": None, "agreed": False, "votes": votes, "confidence": None,
           "ms": round((time.monotonic() - started) * 1000)}
    errors = [v["error"] for v in votes if "error" in v]
    if errors:
        out["error"] = errors[0]
        return out
    picks = {v["index"] for v in votes}
    if len(picks) == 1:
        out["agreed"], out["index"] = True, votes[0]["index"]
        confidences = [v["confidence"] for v in votes if v.get("confidence") is not None]
        out["confidence"] = min(confidences) if confidences else None
    return out


def choose(config, step, candidates, *, page_title="", page_path="", emit=lambda e: None):
    """Pick one of `candidates` (best-first) for `step`.

    Returns {"index": int|None, "confidence": float|None, "by": "jev"|"decision"|"llm"|None, ...}.
    index None means 'none of these', the two runs disagreed, an error, or no model."""
    if not candidates:
        return {"index": None, "confidence": None, "by": None}
    name, cfg = decision_profile(config)
    if not name or not cfg:
        # No decision model configured: the planner gets the candidates instead. Using the
        # (slow, reasoning) planner model as a chooser costs as much as a re-plan and tells
        # the planner nothing.
        return {"index": None, "confidence": None, "by": None}
    texts = [c.element.summary()[:240] for c in candidates[:len(OPTION_IDS)]]
    result = vote(config, name, cfg, describe(step), texts, {"title": page_title, "path": page_path}, emit=emit)
    out = {"index": result["index"], "confidence": result["confidence"], "by": family(cfg),
           "agreed": result["agreed"], "ms": result["ms"]}
    if result.get("error"):
        out["error"] = result["error"]
    elif not result["agreed"]:
        out["split"] = True
    return out
