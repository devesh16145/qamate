"""Explicit paid, synthetic protocol probe. Records metadata, never keys or arguments."""
import argparse
import datetime
import json
import os
from pathlib import Path
import time
import uuid

from openai import OpenAI
from model_profiles import resolve_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--serial", action="store_true")
    group.add_argument("--parallel", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    _, profile = resolve_profile(json.loads((root / "config.json").read_text()), args.provider)
    if profile["protocol"] != "openai":
        parser.error("This probe is for OpenAI-compatible streaming tool deltas")
    key = os.environ.get(profile.get("api_key_env", ""))
    if not key:
        parser.error("Configured environment key is missing")
    parallel = False if args.serial else True if args.parallel else None
    result = {"model": profile["model"], "parallel_tool_calls": parallel, "deltas": []}
    started = time.monotonic()
    try:
        with OpenAI(api_key=key, base_url=profile.get("base_url"), timeout=45, max_retries=0) as client:
            stream = client.chat.completions.create(model=profile["model"], stream=True,
                **{profile.get("token_parameter", "max_tokens"): 4096},
                **({"parallel_tool_calls": parallel} if parallel is not None else {}), tool_choice="required",
                messages=[{"role": "user", "content": "This is a synthetic protocol test. Call add_checkpoint four times, with name values one, two, three, four. Do not write prose."}],
                tools=[{"type": "function", "function": {"name": "add_checkpoint", "description": "Record a synthetic label; no external action.",
                       "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}}])
            for chunk in stream:
                for choice in chunk.choices:
                    if choice.finish_reason:
                        result["finish_reason"] = choice.finish_reason
                    for call in choice.delta.tool_calls or []:
                        result["deltas"].append({"index": call.index, "id": call.id,
                            "name": call.function.name if call.function else None,
                            "argument_chars": len(call.function.arguments or "") if call.function else 0})
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    result["wall_s"] = round(time.monotonic() - started, 2)
    target = root / "results" / "_protocol_probe" / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"artifact": str(target), **result}))
    return int("error_type" in result)


if __name__ == "__main__":
    raise SystemExit(main())
