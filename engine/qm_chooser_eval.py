#!/usr/bin/env python
"""Compare chooser models: which small model should answer "which of these elements does
the step mean, if any?"

Runs a fixed set of cases (engine/probes/chooser_cases.json: a step, the short list the
engine would show, the right answer or NONE) through each model the way the engine asks --
twice, with the options in opposite orders -- and reports, per model:

  correct      both runs chose the right element (or NONE when nothing fits)
  wrong pick   both runs agreed on a WRONG element: the dangerous outcome, a wrong click
  not sure     the two runs disagreed, so the engine would ask the planner instead (safe, slower)
  first-option how often a single run just took the first element listed (position bias)
  latency      per call, median and 95th percentile, from this machine

    venv/bin/python engine/qm_chooser_eval.py                      # the configured decision model + a shortlist
    venv/bin/python engine/qm_chooser_eval.py role chat:google/gemini-3.5-flash-lite
Models:  role                    the profile in config.json llm.roles.decision
         profile:<name>          any profile in config.json
         decisions:<model id>    a decision model on OpenRouter's decisions endpoint
         chat:<model id>         a chat model on OpenRouter
OpenRouter specs use OPENROUTER_API_KEY from the environment (the app passes the keys it
holds in the keychain). A model id OpenRouter does not serve is reported as unavailable.
Scorecards: results/_chooser_eval/<timestamp>/scorecard.json
"""
import copy
import datetime
import json
import os
import sys
import time

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import qm_decide
from model_profiles import resolve_profile

CASES = os.path.join(ENGINE, "probes", "chooser_cases.json")
# Candidates to measure against the configured model (ids as listed on OpenRouter in
# October 2026; none of them has been measured on this task -- that is what this is for).
SHORTLIST = ["decisions:typesafe/jev-1.13", "decisions:perplexity/pplx-decider-v1-27b", "decisions:cloudflare/clef-flash",
             "chat:google/gemini-3.5-flash-lite", "chat:qwen/qwen3.8-27b", "chat:openai/gpt-6-luna",
             "chat:mistralai/mistral-small-2603", "chat:anthropic/claude-haiku-4.5"]


def profile_for(config, spec):
    """(config to use, profile name, resolved profile) for a model spec."""
    llm = (config or {}).get("llm") or {}
    if spec == "role":
        name = (llm.get("roles") or {}).get("decision")
        if not name:
            raise ValueError("no decision model is configured (Preferences -> AI models)")
        return config, name, resolve_profile(config, name, "decision")[1]
    kind, _, model = spec.partition(":")
    if kind == "profile":
        return config, model, resolve_profile(config, model, "decision")[1]
    preset = {"decisions": "jev-openrouter", "chat": "openrouter"}.get(kind)
    if not preset or not model:
        raise ValueError(f"unknown model spec {spec!r}")
    patched = copy.deepcopy(config or {})
    patched.setdefault("llm", {}).setdefault("providers", {})["__eval__"] = {"preset": preset, "model": model}
    return patched, "__eval__", resolve_profile(patched, "__eval__", "decision")[1]


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(share * len(values)))] if values else None


def evaluate(config, spec, cases, log=print):
    """Run every case through one model exactly as the engine would (qm_decide.vote: two
    orders at once, one retry, agreement required) and score it."""
    try:
        run_config, name, cfg = profile_for(config, spec)
    except Exception as exc:
        return {"model": spec, "unavailable": str(exc)[:200]}
    log(f"- {spec} ({cfg.get('model')})")
    rows, latencies = [], []
    first_taken = first_possible = unreachable = 0
    for number, case in enumerate(cases):
        texts = case["options"]
        result = qm_decide.vote(run_config, name, cfg, qm_decide.describe(case["step"]), texts, {"title": "", "path": ""})
        votes = result["votes"]
        latencies += [v["ms"] for v in votes if "ms" in v]
        picks = ["error" if "error" in v else v["index"] for v in votes]
        # Order one lists element 0 first, order two lists the last element first.
        for pick, first in zip(picks, (0, len(texts) - 1)):
            if pick != "error":
                first_possible += 1
                first_taken += pick == first
        unreachable += all("error" in v and "ms" not in v for v in votes)
        if number == 2 and unreachable == 3:      # no call got through: wrong model id, or no key for it
            return {"model": spec, "resolved_model": cfg.get("model"), "unavailable": votes[0]["error"]}
        if "error" in picks:
            outcome = "error"
        elif picks[0] != picks[1]:
            outcome = "not_sure"
        elif picks[0] == case["gold"]:
            outcome = "correct"
        elif picks[0] is None:
            outcome = "missed"        # said NONE although the element was listed: safe, costs a re-plan
        else:
            outcome = "wrong_pick"
        rows.append({"id": case["id"], "kind": case["kind"], "gold": case["gold"], "picks": picks, "outcome": outcome})
    count = len(rows) or 1
    tally = {k: sum(r["outcome"] == k for r in rows) for k in ("correct", "wrong_pick", "not_sure", "missed", "error")}
    single = [p == r["gold"] for r in rows for p in r["picks"] if p != "error"]
    log(f"  {tally['correct']}/{len(rows)} correct, {tally['wrong_pick']} wrong pick(s), "
        f"{tally['not_sure'] + tally['missed']} not sure, {tally['error']} failed")
    return {"model": spec, "resolved_model": cfg.get("model"), "cases": len(rows), **tally,
            "correct_pct": round(100 * tally["correct"] / count, 1),
            "wrong_pick_pct": round(100 * tally["wrong_pick"] / count, 1),
            "single_call_correct_pct": round(100 * sum(single) / len(single), 1) if single else None,
            "first_option_pct": round(100 * first_taken / first_possible, 1) if first_possible else None,
            "p50_ms": percentile(latencies, 0.5), "p95_ms": percentile(latencies, 0.95),
            "by_kind": {kind: f"{sum(r['outcome'] == 'correct' for r in rows if r['kind'] == kind)}/"
                              f"{sum(r['kind'] == kind for r in rows)}" for kind in dict.fromkeys(r["kind"] for r in rows)},
            "wrong": [r for r in rows if r["outcome"] == "wrong_pick"], "rows": rows}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.environ.get("ATS_ROOT") or os.path.dirname(ENGINE)
    if "--ats-root" in argv:
        at = argv.index("--ats-root")
        root = argv[at + 1]
        del argv[at:at + 2]
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    specs = [a for a in argv if not a.startswith("-")]
    if not specs:
        specs = ["role"] if ((config.get("llm") or {}).get("roles") or {}).get("decision") else []
        if os.environ.get("OPENROUTER_API_KEY"):
            specs += SHORTLIST
    if not specs:
        print("No decision model is configured and no OpenRouter key is available: nothing to compare.")
        return 1
    print(f"Chooser comparison: {len(cases)} cases, each asked twice in opposite orders, {len(specs)} model(s)", flush=True)
    started = time.monotonic()
    emit = lambda line: print(line, flush=True)
    results = [evaluate(config, spec, cases, emit) for spec in specs]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(root, "results", "_chooser_eval", stamp)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "scorecard.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"), "cases": len(cases),
                   "models": results}, f, indent=2, ensure_ascii=False)
    usable = sorted((r for r in results if "unavailable" not in r),
                    key=lambda r: (r["wrong_pick"], -r["correct"], r["p50_ms"] or 0))
    print(f"\n{'model':44s} correct  wrong pick  not sure  missed  first-option  p50 / p95")
    for r in usable:
        print(f"{r['model'][:44]:44s} {r['correct_pct']:6.1f}%  {r['wrong_pick_pct']:9.1f}%  {r['not_sure']:8d}  {r['missed']:6d}  "
              f"{(str(r['first_option_pct']) + '%') if r['first_option_pct'] is not None else '-':>12s}  "
              f"{r['p50_ms']} / {r['p95_ms']} ms")
    for r in results:
        if "unavailable" in r:
            print(f"{r['model'][:44]:44s} unavailable: {r['unavailable'][:110]}")
    if usable:
        print(f"\nFewest wrong picks, then most correct: {usable[0]['model']}")
    print(f"Scorecard: {os.path.join(out_dir, 'scorecard.json')}  ({time.monotonic() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
