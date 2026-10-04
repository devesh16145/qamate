"""Fast in-process replay: run recorded steps again in a FRESH browser context.

This is the acceptance check for an authored test: the same steps, through the same
runtime, on a clean context (no cookies, storage or state left over from exploring).
It takes seconds -- no new Python process, no video or tracing -- and reports the
first failing step with Playwright's error, so one step can be repaired instead of
re-driving the whole flow. A pytest run can follow for JUnit/report evidence.
"""
import time

from qm_runtime import Flow
from qm_steps import execute


def replay(browser, steps, data=None, *, base_url=None, storage_state=None,
           context_options=None, names=None, timeout_ms=10000):
    started = time.monotonic()
    options = dict(context_options or {})
    if storage_state:
        options["storage_state"] = storage_state
    context = browser.new_context(**options)
    results, ok = [], True
    try:
        page = context.new_page()
        flow = Flow(page, base_url=base_url, timeout_ms=timeout_ms)
        for index, step in enumerate(steps):
            t0 = time.monotonic()
            try:
                execute(flow, step, data, names)
                results.append({"index": index, "ok": True, "ms": round((time.monotonic() - t0) * 1000)})
            except Exception as exc:
                ok = False
                message = str(exc).split("\nCall log:")[0].strip()
                if step.get("target"):
                    message += f" (target: {step['target']})"
                results.append({"index": index, "ok": False, "ms": round((time.monotonic() - t0) * 1000),
                                "op": step.get("op"), "name": step.get("name"),
                                "error": message[:600], "url": page.url})
                break
    finally:
        context.close()
    return {"ok": ok, "steps": results, "ms": round((time.monotonic() - started) * 1000),
            "failed_step": next((r for r in results if not r["ok"]), None)}
