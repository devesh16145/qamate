"""
Agrim ATS — DOM Inspector

Replays a saved test case (or the live temp_recording.py) in a headless
Playwright browser and snapshots interactive elements at each step.

After replay, it compares "what was visible" vs. "what the user actually
touched" and emits coverage-gap suggestions:
    - tabs the recording never visited
    - cancel/discard/back buttons that were never clicked
    - destructive actions (delete/remove) without coverage
    - required inputs that were never filled
    - dropdown options that were available but not selected
    - checkboxes left untoggled

Suggestions are returned as structured JSON so the Electron Review modal
can render them and the user can accept/reject each one.

Usage (CLI):
    python dom_inspector.py analyze <flow_id> <tc_id> [--headed]
    python dom_inspector.py analyze-temp [--headed]
"""

import os
import sys
import json
import re

# Import the same step-hardening helpers used at code-gen time so the replay
# behaves identically to the generated test (loops, positional rewrites, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from recorder_parser import _detect_select_all_runs, _rewrite_dynamic_ids
except Exception:
    def _detect_select_all_runs(steps):
        return {}
    def _rewrite_dynamic_ids(steps):
        return steps


# ── DOM snapshot script ──

INTERACTIVE_DOM_JS = r"""() => {
    const selectors = [
        'button',
        'a[href]',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="combobox"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[role="switch"]',
    ];
    const seen = new Set();
    const out = [];
    for (const sel of selectors) {
        let nodes;
        try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of nodes) {
            if (seen.has(el)) continue;
            seen.add(el);

            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;

            const text = (el.innerText || el.textContent || '').trim().slice(0, 150);
            out.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                text: text,
                aria_label: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                id: el.id || '',
                test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
                value: el.value || '',
                checked: !!el.checked,
                disabled: !!el.disabled,
                required: !!el.required || el.getAttribute('aria-required') === 'true',
            });
        }
    }
    return out;
}"""


def snapshot_page(page):
    """Capture all interactive elements visible on the current page."""
    try:
        return page.evaluate(INTERACTIVE_DOM_JS)
    except Exception as e:
        return [{"_error": str(e)[:200]}]


# ── Replay-time step hardening ──

# ── Test fixture path for replays of file-upload steps ──
# Matches the constant the generator bakes into generated test files.
_ATS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_UPLOAD_IMAGE_PATH = os.path.join(_ATS_ROOT, "tests", "fixtures", "test_upload.png")


def _harden_step_for_replay(step):
    r"""Apply replay-time mitigations.

    All clicks/checks/dblclicks get force=True. Inspector mode prioritises
    making forward progress through the recording over strict actionability
    semantics — a button that is technically "not actionable" (covered by a
    transparent overlay, marked disabled briefly, etc.) should still be
    clicked so subsequent steps have a chance to fire.

    Note: we do NOT convert `locator("...")` to `locator(r"...")` here. The
    stored rawLine is verbatim codegen output (non-raw: `"\\["` = 2 backslash
    chars in the value); Python's non-raw parser decodes it to `\[` which is
    valid CSS. Adding `r` would yield `\\[` at runtime — invalid CSS.
    """
    raw = step.get("rawLine", "")
    if not raw:
        return raw

    if step.get("type") in ("click", "check", "dblclick"):
        if ".click(force=True)" not in raw:
            raw = raw.replace(".click()", ".click(force=True)")
        if ".check(force=True)" not in raw:
            raw = raw.replace(".check()", ".check(force=True)")
        if ".dblclick(force=True)" not in raw:
            raw = raw.replace(".dblclick()", ".dblclick(force=True)")

    # Rewrite hardcoded local file paths in set_input_files to the test fixture
    # (recording captured an absolute path from the user's machine that won't
    # exist at replay time on any other host).
    if "set_input_files(" in raw:
        raw = re.sub(
            r'\.set_input_files\((["\']).*?\1\)',
            f'.set_input_files({json.dumps(TEST_UPLOAD_IMAGE_PATH)})',
            raw,
        )

    return raw


def _build_replay_ops(steps):
    """Turn parsed steps into a sequence of replay ops.

    Op shapes:
      {"kind": "exec",         "step": <step>, "raw": <python line>}
      {"kind": "loop",         "step": <first step>, "base_locator": <expr>, "count": N}
      {"kind": "file_chooser", "step": <click step>, "click_raw": <line>}

    - Consecutive bare-positional checkbox clicks → 'loop' (adapts to live count)
    - Click immediately followed by set_input_files → 'file_chooser'
      (the click triggers a native file dialog; we intercept via Playwright's
      `expect_file_chooser()` and set our test fixture instead)
    """
    runs = _detect_select_all_runs(steps)
    ops = []
    i = 0
    while i < len(steps):
        step = steps[i]
        sid = step.get("id")
        run_info = runs.get(sid)
        if run_info and run_info["action"] == "loop_skip":
            i += 1
            continue
        if run_info and run_info["action"] == "loop_start":
            ops.append({
                "kind": "loop",
                "step": step,
                "base_locator": run_info["base_locator"],
                "count": run_info["count"],
            })
            i += 1
            continue

        # Pair: click/dblclick followed by set_input_files → file-chooser flow
        if (i + 1 < len(steps)
                and step.get("type") in ("click", "dblclick")
                and "set_input_files(" in (steps[i + 1].get("rawLine") or "")):
            ops.append({
                "kind": "file_chooser",
                "step": step,
                "click_raw": _harden_step_for_replay(step),
                "next_step": steps[i + 1],
            })
            i += 2  # consume both steps
            continue

        ops.append({"kind": "exec", "step": step, "raw": _harden_step_for_replay(step)})
        i += 1
    return ops


# ── Replay engine ──

def replay_and_snapshot(steps, headless=True, stop_before_terminal=False, on_log=None):
    """Replay a sequence of recorded steps, capturing a DOM snapshot before each one.

    Args:
        steps: list of parsed step dicts (must have "rawLine" and "id")
        headless: run Playwright headless
        stop_before_terminal: skip the final action (avoids re-submitting destructive forms)
        on_log: callable(str) for progress streaming

    Returns:
        dict {steps, snapshots, errors}
    """
    from playwright.sync_api import sync_playwright
    import re as _re

    def log(msg):
        if on_log:
            on_log(str(msg))
        else:
            print(str(msg), flush=True)

    snapshots = []
    errors = []
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 6

    ops = _build_replay_ops(steps)
    terminal_idx = len(ops) - 1 if stop_before_terminal else -1

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            channel="chrome",
            args=["--disable-gpu", "--no-sandbox"],
        )
        ctx_args = {"no_viewport": True} if not headless else {"viewport": {"width": 1280, "height": 720}}
        context = browser.new_context(**ctx_args)
        page = context.new_page()
        # 10s gives slow SPA elements (Tailwind class chains, async-rendered
        # OTP boxes) a fair chance to mount, while the consecutive-failure
        # bail-out caps worst-case waste if the replay is doomed.
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(15000)

        for idx, op in enumerate(ops):
            step = op["step"]
            sid = step.get("id", idx + 1)

            # Snapshot BEFORE the action — capture what was available to the user
            snapshots.append({
                "step_id": sid,
                "when": "before",
                "url": page.url,
                "action_type": step.get("type", "other"),
                "action_target": step.get("targetDescription", ""),
                "action_value": step.get("value", ""),
                "elements": snapshot_page(page),
            })

            if stop_before_terminal and idx == terminal_idx:
                log(f"[inspector] stopping before terminal op (step {sid})")
                break

            step_succeeded = False
            try:
                if op["kind"] == "file_chooser":
                    # Click that opens a native file dialog → intercept via
                    # expect_file_chooser, set our test fixture image.
                    click_raw = op["click_raw"]
                    log(f"[inspector] step {sid}: file-chooser upload — {TEST_UPLOAD_IMAGE_PATH}")
                    try:
                        with page.expect_file_chooser(timeout=5000) as fc_info:
                            exec(click_raw, {"page": page, "re": _re})
                        fc_info.value.set_files(TEST_UPLOAD_IMAGE_PATH)
                        step_succeeded = True
                    except Exception as fe:
                        # If chooser didn't open (some uploads are direct input
                        # writes), fall back: just exec the click + set_files on
                        # any visible file input nearby.
                        log(f"[inspector]   chooser intercept failed ({str(fe)[:80]}) — trying direct set_input_files")
                        try:
                            file_inputs = page.locator('input[type="file"]')
                            if file_inputs.count() > 0:
                                file_inputs.first.set_input_files(TEST_UPLOAD_IMAGE_PATH)
                                step_succeeded = True
                        except Exception:
                            pass

                elif op["kind"] == "loop":
                    # Dynamic select-all checkbox loop — adapts to live page count
                    base_expr = op["base_locator"]
                    log(f"[inspector] step {sid}: select-all loop on {base_expr}")
                    eval_globals = {"page": page, "re": _re}
                    locator = eval(base_expr, eval_globals)
                    n = locator.count()
                    log(f"[inspector]   found {n} checkbox(es) on page (recording had {op['count']})")
                    for _i in range(n):
                        try:
                            locator.nth(_i).click(force=True, timeout=5000)
                            page.wait_for_timeout(250)
                        except Exception as ce:
                            log(f"[inspector]   checkbox #{_i} click failed: {str(ce)[:120]}")
                    step_succeeded = True
                else:
                    raw = op["raw"]
                    log(f"[inspector] step {sid}: {raw[:100]}")

                    # Mirror generator behaviour: scroll the target into view
                    # before locator()-based click/fill/check (handles sticky
                    # footers, off-screen OTP boxes, etc.)
                    if "locator(" in raw and step.get("type") in ("click", "fill", "check", "dblclick"):
                        try:
                            locator_expr = (
                                raw.strip()
                                .split(".click(")[0]
                                .split(".fill(")[0]
                                .split(".check(")[0]
                                .split(".dblclick(")[0]
                            )
                            exec(f"{locator_expr}.scroll_into_view_if_needed(timeout=3000)",
                                 {"page": page, "re": _re})
                        except Exception:
                            pass

                    # Pre-flight count guard: if the locator uses `.nth(N)` for
                    # N>=1 and the live page has fewer matching elements than the
                    # recording assumed, we'd otherwise burn a 10s timeout per
                    # step. Check count cheaply and re-target.
                    pre_skipped = False
                    if step.get("type") in ("click", "check", "dblclick"):
                        nth_m = re.search(r'\.nth\((\d+)\)', raw)
                        if nth_m and int(nth_m.group(1)) >= 1:
                            try:
                                base_expr = raw[:nth_m.start()]
                                live_count = eval(f"({base_expr}).count()",
                                                  {"page": page, "re": _re})
                                if live_count == 0:
                                    log(f"[inspector]   no matching elements (count=0) — skipping step")
                                    step_succeeded = True
                                    pre_skipped = True
                                elif live_count <= int(nth_m.group(1)):
                                    raw = re.sub(r'\.nth\(\d+\)', '.last', raw, count=1)
                                    log(f"[inspector]   count={live_count} < .nth({nth_m.group(1)}) — using .last")
                            except Exception:
                                pass  # fall through to normal exec

                    if not pre_skipped:
                        try:
                            exec(raw, {"page": page, "re": _re})
                            step_succeeded = True
                        except Exception as primary_err:
                            # Last-resort fallback for unexpected actionability
                            # failures: retry with .last if .nth(N>=1) and we
                            # didn't already swap to .last.
                            fallback_used = False
                            if step.get("type") in ("click", "check", "dblclick"):
                                nth_m = re.search(r'\.nth\((\d+)\)', raw)
                                if nth_m and int(nth_m.group(1)) >= 1:
                                    fallback = re.sub(r'\.nth\(\d+\)', '.last', raw, count=1)
                                    log(f"[inspector]   retry with .last")
                                    try:
                                        exec(fallback, {"page": page, "re": _re})
                                        step_succeeded = True
                                        fallback_used = True
                                    except Exception:
                                        pass
                            if not fallback_used:
                                raise primary_err

                # Post-action: wait for API calls (Verify buttons trigger PAN/GST lookups)
                target_desc = (step.get("targetDescription") or "").lower()
                if "verify" in target_desc or "send otp" in target_desc:
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                else:
                    # Best-effort short networkidle for SPA settling
                    try:
                        page.wait_for_load_state("networkidle", timeout=2000)
                    except Exception:
                        pass

                page.wait_for_timeout(300)
            except Exception as e:
                msg = f"[inspector] step {sid} failed: {str(e)[:160]}"
                log(msg)
                errors.append({"step_id": sid, "error": str(e)[:300]})
                # Continue — partial coverage is still useful

            # Track consecutive failures — bail out if we're clearly off the rails
            # (e.g. recording references dynamic IDs that don't exist on this run)
            if step_succeeded:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(f"[inspector] {consecutive_failures} consecutive failures — "
                        f"bailing out and reporting snapshots from {len(snapshots)} steps reached")
                    break

        # Final snapshot (post all actions)
        snapshots.append({
            "step_id": (steps[-1].get("id", len(steps)) if steps else 0) + 1,
            "when": "final",
            "url": page.url,
            "action_type": "final_state",
            "action_target": "",
            "elements": snapshot_page(page),
        })

        context.close()
        browser.close()

    return {"steps": steps, "snapshots": snapshots, "errors": errors}


# ── Coverage gap analysis ──

CANCEL_KEYWORDS = ("cancel", "discard", "back", "close", "skip", "reset")
DESTRUCTIVE_KEYWORDS = ("delete", "remove", "archive", "deactivate", "block")
SUBMIT_KEYWORDS = ("save", "submit", "create", "send", "confirm", "continue", "next", "verify", "complete")


def _element_label(el):
    """Best-effort human-readable label for an element."""
    return (
        el.get("aria_label")
        or el.get("text")
        or el.get("placeholder")
        or el.get("name")
        or el.get("id")
        or ""
    ).strip()


def _was_interacted_with(label, used_descriptors):
    """Check if the user touched an element with this label during recording."""
    if not label:
        return False
    l = label.lower()
    for d in used_descriptors:
        if not d:
            continue
        if l in d or d in l:
            return True
    return False


def analyze_gaps(replay_result):
    """Analyze snapshots vs recorded actions to find coverage gaps."""
    steps = replay_result["steps"]
    snapshots = replay_result["snapshots"]

    # Build set of descriptors the user interacted with (lowercased substrings)
    used = set()
    for s in steps:
        for key in ("targetDescription", "target", "value"):
            v = (s.get(key) or "").lower().strip()
            if v:
                used.add(v)
                # Extract quoted name from selectors like  get_by_role("button", name="Save")
                import re as _re
                for m in _re.finditer(r'["\']([^"\']{2,60})["\']', v):
                    used.add(m.group(1).lower())

    gaps = {
        "unused_tabs": [],
        "negative_action_buttons": [],
        "destructive_buttons": [],
        "alternate_actions": [],
        "unfilled_required_inputs": [],
        "unfilled_inputs": [],
        "unused_dropdown_options": [],
        "checkboxes_skipped": [],
    }

    for snap in snapshots:
        sid = snap["step_id"]
        url = snap.get("url", "")
        options_in_snap = []

        for el in snap.get("elements", []):
            if "_error" in el:
                continue
            label = _element_label(el)
            if not label:
                continue
            label_l = label.lower()
            role = el.get("role", "")
            tag = el.get("tag", "")

            # Skip elements that the user already touched
            if _was_interacted_with(label, used):
                continue

            # — Tabs not clicked —
            if role == "tab":
                gaps["unused_tabs"].append({
                    "step_id": sid, "label": label, "url": url,
                })
                continue

            # — Buttons (categorized) —
            if tag == "button" or role == "button":
                if any(kw in label_l for kw in CANCEL_KEYWORDS):
                    gaps["negative_action_buttons"].append({"step_id": sid, "label": label, "url": url})
                elif any(kw in label_l for kw in DESTRUCTIVE_KEYWORDS):
                    gaps["destructive_buttons"].append({"step_id": sid, "label": label, "url": url})
                elif any(kw in label_l for kw in SUBMIT_KEYWORDS):
                    gaps["alternate_actions"].append({"step_id": sid, "label": label, "url": url})
                continue

            el_type = (el.get("type") or "").lower()

            # — Checkboxes (skipped) — checked BEFORE generic input branch
            if role == "checkbox" or el_type == "checkbox":
                if not el.get("checked"):
                    gaps["checkboxes_skipped"].append({"step_id": sid, "label": label, "url": url})
                continue

            # — Inputs/textareas not filled —
            if tag in ("input", "textarea"):
                if el.get("disabled"):
                    continue
                if el_type in ("hidden", "submit", "button", "radio"):
                    continue
                if not el.get("value"):
                    bucket = gaps["unfilled_required_inputs"] if el.get("required") else gaps["unfilled_inputs"]
                    bucket.append({
                        "step_id": sid, "label": label, "type": el_type or "text", "url": url,
                    })
                continue

            # — Dropdown options (collected for batch suggestion) —
            if role == "option":
                options_in_snap.append({"step_id": sid, "label": label, "url": url})

        if len(options_in_snap) > 1:
            gaps["unused_dropdown_options"].append({
                "step_id": sid,
                "url": url,
                "count": len(options_in_snap),
                "options": options_in_snap[:20],
            })

    # — Dedupe across snapshots (by label + url) —
    for key, items in gaps.items():
        if key == "unused_dropdown_options":
            continue
        seen = set()
        deduped = []
        for item in items:
            k = (item.get("label", "").lower(), item.get("url", ""))
            if k not in seen:
                seen.add(k)
                deduped.append(item)
        gaps[key] = deduped

    return gaps


# ── Suggestion generation ──

def build_suggestions(gaps, base_tc_id):
    """Convert raw coverage gaps into proposed test case suggestions."""
    suggestions = []

    for item in gaps.get("unused_tabs", []):
        suggestions.append({
            "type": "tab_coverage",
            "priority": "medium",
            "title": f"Cover tab '{item['label']}'",
            "description": f"Tab '{item['label']}' was visible but never clicked. Add a TC that opens it and verifies content.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("negative_action_buttons", []):
        suggestions.append({
            "type": "negative_flow",
            "priority": "high",
            "title": f"Negative flow: '{item['label']}'",
            "description": f"Reach the same screen, click '{item['label']}' instead of submitting, verify state is discarded.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("destructive_buttons", []):
        suggestions.append({
            "type": "destructive_flow",
            "priority": "high",
            "title": f"Destructive flow: '{item['label']}'",
            "description": f"Cover '{item['label']}' action — likely needs a confirm-modal check and post-delete verification.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("alternate_actions", []):
        suggestions.append({
            "type": "alternate_action",
            "priority": "low",
            "title": f"Alternate action: '{item['label']}'",
            "description": f"Action button '{item['label']}' was visible but never clicked.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("unfilled_required_inputs", []):
        suggestions.append({
            "type": "validation",
            "priority": "high",
            "title": f"Validation: empty '{item['label']}'",
            "description": f"Required field '{item['label']}' was never tested empty. Add a TC that submits with this field blank.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("unfilled_inputs", []):
        suggestions.append({
            "type": "input_coverage",
            "priority": "low",
            "title": f"Coverage: optional field '{item['label']}'",
            "description": f"Optional field '{item['label']}' was visible but not filled.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("checkboxes_skipped", []):
        suggestions.append({
            "type": "checkbox_coverage",
            "priority": "low",
            "title": f"Checkbox '{item['label']}' untoggled",
            "description": f"Checkbox '{item['label']}' was visible but never toggled. Add a variant where it is checked.",
            "based_on": base_tc_id, "label": item["label"], "step_id": item["step_id"],
        })

    for item in gaps.get("unused_dropdown_options", []):
        opts = ", ".join([o["label"] for o in item["options"][:5]])
        more = "" if item["count"] <= 5 else f" (+{item['count'] - 5} more)"
        suggestions.append({
            "type": "dropdown_variants",
            "priority": "medium",
            "title": f"Dropdown variants on step {item['step_id']} ({item['count']} options)",
            "description": f"Dropdown showed {item['count']} options ({opts}{more}). Generate variants for each.",
            "based_on": base_tc_id, "step_id": item["step_id"], "options": item["options"],
        })

    # Sort: high → medium → low
    rank = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: rank.get(s["priority"], 9))
    return suggestions


# ── Variant value substitution ──

def _load_variant_data(ats_root, flow_id, tc_id, variant):
    """Load the chosen variant's input map for a TC, or return None."""
    data_file = os.path.join(ats_root, "tests", "flows", flow_id, "test_data.json")
    if not os.path.exists(data_file):
        return None
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    tc_raw = data.get(tc_id, {})
    if not isinstance(tc_raw, dict) or not tc_raw:
        return None

    is_variants = all(isinstance(v, dict) for v in tc_raw.values())
    if is_variants:
        if variant and variant in tc_raw:
            return tc_raw[variant]
        # No variant chosen — fall through to first variant (matches conftest behaviour)
        return next(iter(tc_raw.values()), None)
    # Flat (non-variant) data — return as-is
    return tc_raw


def _apply_values_to_steps(steps, value_map):
    """Replace recorded fill/type/select values with values from a variant map.

    Match each step by its `varName` (set at recording time, e.g. "input_1").
    The recorded `value` is replaced inside the step's `rawLine` and `value` field.
    """
    import re as _re
    if not value_map:
        return steps

    rewritten = []
    for s in steps:
        var_name = s.get("varName")
        if not var_name or var_name not in value_map:
            rewritten.append(s)
            continue

        new_val = value_map[var_name]
        new_val_str = "" if new_val is None else str(new_val)
        old_val = s.get("value", "")
        raw = s.get("rawLine", "")

        # Python-quoted literal for the new value (handles escapes safely)
        new_lit = json.dumps(new_val_str)

        if old_val and raw:
            pattern = _re.compile(
                r'\.(fill|type|select_option|press)\((["\'])'
                + _re.escape(old_val)
                + r'\2'
            )
            raw = pattern.sub(lambda m: f'.{m.group(1)}({new_lit}', raw)

        new_step = dict(s)
        new_step["rawLine"] = raw
        new_step["value"] = new_val_str
        rewritten.append(new_step)
    return rewritten


# ── Navigation map (snapshots → site structure) ──

def _normalize_url(url):
    """Strip query string + fragment so /catalog?page=1 == /catalog?page=2."""
    if not url:
        return ""
    base = url.split("#", 1)[0].split("?", 1)[0]
    return base.rstrip("/")


def build_navigation_map(snapshots):
    """Group per-action snapshots into a per-URL navigation map.

    Returns:
        {
          "pages": [
            {
              "url": "...",
              "title": "...",
              "first_seen_at_action": 5,
              "actions_on_page": [{"action": "click", "locator": "...", "step_index": N}],
              "elements": [union of all interactive elements seen on this URL],
              "transitions_out": [{"to_url": "...", "via_action": "..."}],
            }, ...
          ],
          "transitions": [{"from_url": "...", "to_url": "...", "via": "..."}],
          "ordered_urls": ["url1", "url2", ...]
        }
    """
    pages = {}        # normalized url -> page dict
    ordered = []      # urls in first-seen order
    transitions = []
    prev_url = None
    prev_action = None

    for idx, snap in enumerate(snapshots):
        url = _normalize_url(snap.get("url", ""))
        if not url:
            continue

        if url not in pages:
            pages[url] = {
                "url": url,
                "title": snap.get("title", ""),
                "first_seen_at_action": idx,
                "actions_on_page": [],
                "elements_by_key": {},
                "transitions_out": [],
            }
            ordered.append(url)

        page = pages[url]
        # Better title (some snapshots have empty title due to navigation timing)
        if not page["title"] and snap.get("title"):
            page["title"] = snap.get("title", "")

        # Record this action on the page
        page["actions_on_page"].append({
            "step_index": idx,
            "action": snap.get("action", ""),
            "locator": snap.get("locator", "")[:200],
        })

        # Merge interactive elements (dedupe by stable key)
        for el in snap.get("elements", []) or []:
            if not isinstance(el, dict):
                continue
            key = _element_key(el)
            if key and key not in page["elements_by_key"]:
                page["elements_by_key"][key] = el

        # Record URL transitions
        if prev_url and prev_url != url:
            transitions.append({
                "from_url": prev_url,
                "to_url": url,
                "via_action": prev_action or "",
                "at_index": idx,
            })
            if prev_url in pages:
                pages[prev_url]["transitions_out"].append({
                    "to_url": url,
                    "via_action": prev_action or "",
                })

        prev_url = url
        prev_action = f"{snap.get('action', '')}: {snap.get('locator', '')[:80]}"

    # Flatten elements_by_key to a list
    for p in pages.values():
        p["elements"] = list(p["elements_by_key"].values())
        del p["elements_by_key"]

    return {
        "pages": [pages[u] for u in ordered],
        "ordered_urls": ordered,
        "transitions": transitions,
    }


def _element_key(el):
    """A stable identifier for an element (for dedupe across snapshots on same page)."""
    parts = [
        el.get("tag", ""),
        el.get("role", ""),
        el.get("id", ""),
        el.get("aria_label", "") or el.get("name", "") or el.get("placeholder", "") or "",
        (el.get("text") or "")[:60],
    ]
    return "|".join(parts)


def _label(el):
    return (
        el.get("aria_label")
        or el.get("text")
        or el.get("placeholder")
        or el.get("name")
        or el.get("id")
        or ""
    ).strip()


def analyze_gaps_by_page(nav_map):
    """For each page in the navigation map, identify interactive elements
    the test never used. Returns {url: {category: [items]}, ...}."""
    gaps_by_page = {}

    for page in nav_map["pages"]:
        url = page["url"]
        # Set of locators the user actually touched on this page
        touched = set()
        for a in page["actions_on_page"]:
            loc_l = a.get("locator", "").lower()
            if loc_l:
                touched.add(loc_l)
                # also extract quoted names from locator
                import re as _re
                for m in _re.finditer(r'["\']([^"\']{2,60})["\']', loc_l):
                    touched.add(m.group(1).lower())

        cats = {
            "unused_tabs": [],
            "unused_buttons_cancel_back": [],
            "unused_buttons_destructive": [],
            "unused_buttons_action": [],
            "unfilled_required_inputs": [],
            "unfilled_optional_inputs": [],
            "unused_links": [],
        }

        for el in page.get("elements", []):
            label = _label(el)
            if not label:
                continue
            label_l = label.lower()

            # Already touched?
            if any(label_l in t or t in label_l for t in touched if t):
                continue

            role = el.get("role", "")
            tag = el.get("tag", "")
            el_type = (el.get("type") or "").lower()

            if role == "tab":
                cats["unused_tabs"].append({"label": label})
            elif tag == "a":
                cats["unused_links"].append({"label": label})
            elif tag == "button" or role == "button":
                if any(kw in label_l for kw in CANCEL_KEYWORDS):
                    cats["unused_buttons_cancel_back"].append({"label": label})
                elif any(kw in label_l for kw in DESTRUCTIVE_KEYWORDS):
                    cats["unused_buttons_destructive"].append({"label": label})
                elif any(kw in label_l for kw in SUBMIT_KEYWORDS):
                    cats["unused_buttons_action"].append({"label": label})
                else:
                    cats["unused_buttons_action"].append({"label": label})
            elif tag in ("input", "textarea") and el_type not in ("hidden", "submit", "button", "checkbox", "radio"):
                if not el.get("value") and not el.get("disabled"):
                    bucket = "unfilled_required_inputs" if el.get("required") else "unfilled_optional_inputs"
                    cats[bucket].append({"label": label, "type": el_type or "text"})

        # Dedup
        for k, items in cats.items():
            seen = set()
            unique = []
            for it in items:
                key = it.get("label", "").lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(it)
            cats[k] = unique

        # Only record pages that have at least one gap
        if any(cats.values()):
            gaps_by_page[url] = cats

    return gaps_by_page


def build_suggestions_from_navmap(nav_map, gaps_by_page, base_tc_id):
    """Turn per-page gaps into ranked suggestions, tagged with the page URL."""
    suggestions = []
    rank = {"high": 0, "medium": 1, "low": 2}

    for page in nav_map["pages"]:
        url = page["url"]
        title = page.get("title") or url
        gaps = gaps_by_page.get(url)
        if not gaps:
            continue

        for item in gaps.get("unused_tabs", []):
            suggestions.append({
                "type": "tab_coverage", "priority": "medium",
                "title": f"Cover tab '{item['label']}'",
                "description": f"On {title} — tab '{item['label']}' was visible but never clicked.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unused_buttons_cancel_back", []):
            suggestions.append({
                "type": "negative_flow", "priority": "high",
                "title": f"Negative flow: '{item['label']}'",
                "description": f"On {title} — '{item['label']}' button never tested. Verify it discards state correctly.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unused_buttons_destructive", []):
            suggestions.append({
                "type": "destructive_flow", "priority": "high",
                "title": f"Destructive: '{item['label']}'",
                "description": f"On {title} — '{item['label']}' needs a TC, likely with confirm-modal check.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unused_buttons_action", []):
            suggestions.append({
                "type": "alternate_action", "priority": "low",
                "title": f"Alternate: '{item['label']}'",
                "description": f"On {title} — '{item['label']}' button was visible but not clicked.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unfilled_required_inputs", []):
            suggestions.append({
                "type": "validation", "priority": "high",
                "title": f"Empty required '{item['label']}'",
                "description": f"On {title} — required field '{item['label']}' never tested empty.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unfilled_optional_inputs", []):
            suggestions.append({
                "type": "input_coverage", "priority": "low",
                "title": f"Optional field '{item['label']}'",
                "description": f"On {title} — '{item['label']}' was visible but not filled.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })
        for item in gaps.get("unused_links", []):
            suggestions.append({
                "type": "link_coverage", "priority": "low",
                "title": f"Cover link '{item['label']}'",
                "description": f"On {title} — link '{item['label']}' never clicked.",
                "page_url": url, "label": item["label"], "based_on": base_tc_id,
            })

    suggestions.sort(key=lambda s: rank.get(s["priority"], 9))
    return suggestions


# ── Public entry points ──

def inspect_tc(ats_root, flow_id, tc_id, headless=True, stop_before_terminal=False,
               variant=None, on_log=None):
    """Inspect a saved TC. Drives the actual generated test via pytest when
    available; falls back to the custom replay engine otherwise.

    The pytest path is preferred because it inherits every adaptation the
    user (and the generator) baked into the .py file — force=True, file
    chooser interception, dynamic loops, manual `.last` fallbacks, etc."""
    test_py = os.path.join(ats_root, "tests", "flows", flow_id, f"test_{flow_id}.py")
    if os.path.exists(test_py):
        if on_log:
            on_log(f"[inspector] driving via pytest: {test_py}")
        return _inspect_via_pytest(ats_root, flow_id, tc_id, headless=headless,
                                   variant=variant, on_log=on_log)
    if on_log:
        on_log(f"[inspector] no generated test for flow '{flow_id}' — using replay engine")
    return _inspect_via_replay(ats_root, flow_id, tc_id, headless=headless,
                               stop_before_terminal=stop_before_terminal,
                               variant=variant, on_log=on_log)


def _inspect_via_pytest(ats_root, flow_id, tc_id, headless=True, variant=None, on_log=None):
    """Run the generated test_<flow>.py with our snapshot plugin attached.

    The plugin (engine/dom_inspector_plugin.py) monkey-patches Playwright's
    Locator/Page action methods to record DOM state before every user-facing
    action, and writes them to ATS_COVERAGE_SNAPSHOT_OUT when pytest finishes.
    """
    import subprocess
    import sys as _sys
    import tempfile
    import datetime

    def log(msg):
        if on_log:
            on_log(str(msg))

    # Where the plugin will dump the snapshots.
    # We write to two places:
    #   1. A timestamped file for historical record
    #   2. A fixed `<tc_id>_latest.json` so Claude / scripts always know where
    #      to find the most recent capture without guessing timestamps.
    out_dir = os.path.join(ats_root, "results", "_coverage")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = os.path.join(out_dir, f"{tc_id}_{stamp}.json")
    latest_path = os.path.join(out_dir, f"{tc_id}_latest.json")

    underscored = tc_id.replace("-", "_")

    cmd = [
        _sys.executable, "-m", "pytest",
        os.path.join(ats_root, "tests"),
        "-p", "engine.dom_inspector_plugin",
        "-k", underscored,
        "--tb=short",
        "-s",
        "-v",
    ]
    if not headless:
        cmd.append("--headed")

    env = os.environ.copy()
    env["ATS_ROOT"] = ats_root
    env["PYTHONPATH"] = ats_root + os.pathsep + env.get("PYTHONPATH", "")
    env["ATS_COVERAGE_SNAPSHOT_OUT"] = snap_path
    env["PYTHONUNBUFFERED"] = "1"
    if variant:
        env["ATS_VARIANT"] = variant

    log(f"[inspector] running: pytest -k {underscored}{' --headed' if not headless else ''}")
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=ats_root, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "pytest run exceeded 10 minute timeout"}
    except Exception as e:
        return {"status": "error", "message": f"failed to spawn pytest: {e}"}

    # Forward a summary of pytest output to the UI log
    for line in (proc.stdout or "").splitlines()[-30:]:
        log(f"[pytest] {line}")

    if not os.path.exists(snap_path):
        return {
            "status": "error",
            "message": "No snapshot file produced — plugin may not have loaded or test never ran",
            "pytest_exit": proc.returncode,
            "pytest_tail": (proc.stdout or "")[-500:],
        }

    with open(snap_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    snapshots = data.get("snapshots", [])
    log(f"[inspector] captured {len(snapshots)} action snapshots")

    nav_map = build_navigation_map(snapshots)

    # Persist a structured artifact at a fixed-name path (always overwrite the
    # previous run for this TC). This is the file the chat-side reviewer reads.
    artifact = {
        "tc_id": tc_id,
        "flow": flow_id,
        "variant": variant,
        "captured_at": stamp,
        "pytest_exit": proc.returncode,
        "snapshots_count": len(snapshots),
        "navigation_map": nav_map,
        "raw_snapshots": snapshots,
    }
    try:
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, default=str)
    except Exception as e:
        log(f"[inspector] WARN: failed to write latest artifact: {e}")

    return {
        "status": "success",
        "tc_id": tc_id,
        "flow": flow_id,
        "driver": "pytest",
        "snapshots_count": len(snapshots),
        "pytest_exit": proc.returncode,
        "navigation_map": nav_map,
        "snapshot_file": snap_path,
        "artifact_file": latest_path,
    }


def _inspect_via_replay(ats_root, flow_id, tc_id, headless=True,
                        stop_before_terminal=False, variant=None, on_log=None):
    """Original custom-replay engine. Used only when no generated test exists."""
    tc_file = os.path.join(ats_root, "tests", "flows", flow_id, "test_cases.json")
    if not os.path.exists(tc_file):
        return {"status": "error", "message": f"test_cases.json not found for flow '{flow_id}'"}

    with open(tc_file, "r", encoding="utf-8") as f:
        tcs = json.load(f)
    tc = next((t for t in tcs if t.get("tc_id") == tc_id), None)
    if not tc:
        return {"status": "error", "message": f"TC '{tc_id}' not found in flow '{flow_id}'"}

    steps = tc.get("steps", [])
    if not steps:
        return {"status": "error", "message": f"TC '{tc_id}' has no recorded steps"}

    value_map = _load_variant_data(ats_root, flow_id, tc_id, variant)
    if value_map:
        steps = _apply_values_to_steps(steps, value_map)
        if on_log:
            on_log(f"[inspector] applied variant '{variant or 'default'}' ({len(value_map)} values)")
    steps = _rewrite_dynamic_ids(steps)

    replay = replay_and_snapshot(steps, headless=headless,
                                 stop_before_terminal=stop_before_terminal, on_log=on_log)
    gaps = analyze_gaps(replay)
    suggestions = build_suggestions(gaps, tc_id)

    return {
        "status": "success",
        "tc_id": tc_id,
        "flow": flow_id,
        "driver": "replay",
        "snapshots_count": len(replay["snapshots"]),
        "replay_errors": replay["errors"],
        "gaps_summary": {k: len(v) for k, v in gaps.items()},
        "gaps": gaps,
        "suggestions": suggestions,
    }


def inspect_temp_recording(ats_root, headless=True, stop_before_terminal=False, on_log=None):
    """Replay the live temp_recording.py (not yet saved) and return suggestions."""
    sys.path.insert(0, os.path.dirname(__file__))
    from recorder_parser import parse_steps

    parsed = parse_steps(ats_root)
    if parsed.get("status") != "success":
        return parsed
    steps = parsed.get("steps", [])
    if not steps:
        return {"status": "error", "message": "No steps found in temp recording"}

    replay = replay_and_snapshot(steps, headless=headless,
                                 stop_before_terminal=stop_before_terminal, on_log=on_log)
    gaps = analyze_gaps(replay)
    suggestions = build_suggestions(gaps, "PENDING")

    return {
        "status": "success",
        "tc_id": "PENDING",
        "snapshots_count": len(replay["snapshots"]),
        "replay_errors": replay["errors"],
        "gaps_summary": {k: len(v) for k, v in gaps.items()},
        "gaps": gaps,
        "suggestions": suggestions,
    }


# ── CLI ──

def _emit(event_data):
    print(json.dumps(event_data, default=str), flush=True)


def main():
    if len(sys.argv) < 2:
        _emit({"status": "error", "message": "Usage: dom_inspector.py <analyze|analyze-temp> [args]"})
        sys.exit(1)

    mode = sys.argv[1]
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    headless = "--headed" not in sys.argv
    stop_terminal = "--stop-terminal" in sys.argv

    if mode == "analyze":
        if len(sys.argv) < 4:
            _emit({"status": "error", "message": "Usage: analyze <flow_id> <tc_id> [--variant <name>]"})
            sys.exit(1)
        flow_id, tc_id = sys.argv[2], sys.argv[3]
        variant = None
        if "--variant" in sys.argv:
            i = sys.argv.index("--variant")
            if i + 1 < len(sys.argv):
                variant = sys.argv[i + 1]
        result = inspect_tc(
            ats_root, flow_id, tc_id,
            headless=headless,
            stop_before_terminal=stop_terminal,
            variant=variant,
            on_log=lambda m: _emit({"event": "log", "message": m}),
        )
        _emit({"event": "result", **result})

    elif mode == "analyze-temp":
        result = inspect_temp_recording(
            ats_root,
            headless=headless,
            stop_before_terminal=stop_terminal,
            on_log=lambda m: _emit({"event": "log", "message": m}),
        )
        _emit({"event": "result", **result})

    else:
        _emit({"status": "error", "message": f"Unknown mode: {mode}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
