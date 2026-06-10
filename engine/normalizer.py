"""Deterministic page-normalization detectors.

Fixes common web anti-patterns BEFORE the LLM sees the page, so the agent
never has to reason about them: IDs that break CSS parsing, accessible-name
collisions (get_by_role("button", name="Paid") also matching "Unpaid"),
hidden styled inputs that need force, MUI number inputs that reject fill().

Pure functions — no browser, no LLM — unit-tested in engine/tests/test_normalizer.py.
"""
import re

# An id Playwright/CSS can address with the plain #id shorthand.
_SAFE_CSS_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def escape_css_id(el_id):
    """CSS selector for an element id. '#foo' when the id is CSS-safe; the
    attribute form [id='...'] when it contains spaces/colons/etc. ('Last 7 days',
    MUI ':r3:') that break the # shorthand. Single quotes inside so the selector
    can be embedded in a double-quoted rawLine without escaping."""
    el_id = (el_id or "").strip()
    if not el_id:
        return ""
    if _SAFE_CSS_ID.match(el_id):
        return f"#{el_id}"
    return "[id='" + el_id.replace("\\", "\\\\").replace("'", "\\'") + "']"


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def disambiguate(models):
    """Fix accessible-name collisions across element models IN PLACE.

    Playwright's get_by_role(name=...) is substring + case-insensitive by
    default, so name='Paid' also matches 'Unpaid' and 'Paid 0'. For every
    role-strategy whose name is a substring of ANOTHER element's name (same
    role), set exact=True. Strategies whose names are fully identical cannot
    be fixed by exact — those get ambiguous=True so act() warns.

    Returns the number of strategies adjusted."""
    by_role = {}
    for m in models:
        for strat in [m.get("primary") or {}] + list(m.get("fallbacks") or []):
            if strat.get("by") == "role" and strat.get("name"):
                by_role.setdefault(strat["role"], []).append((m, strat))

    adjusted = 0
    for role, pairs in by_role.items():
        names = [_norm(s.get("name")) for _, s in pairs]
        for i, (m, strat) in enumerate(pairs):
            mine = names[i]
            if not mine:
                continue
            others = [n for j, n in enumerate(names) if j != i]
            if any(mine == o for o in others):
                m["ambiguous"] = True
            elif any(mine in o for o in others):
                strat["exact"] = True
                adjusted += 1

    # Fully-identical PRIMARY locators (12 cards x the same 'Incorrect?' button):
    # assign each member its DOM-order ordinal so act()/codegen can target THIS
    # element with .nth(k). Without it, the live click resolves to the FIRST
    # match (the agent picked the card button's ref but clicked the sidebar tab)
    # and the recorded bare locator fails Playwright strict mode at replay.
    groups = {}
    for m in models:
        if m.get("source") == "aria":
            continue   # appended after natives, no rect — ordinal would be meaningless
        p = m.get("primary") or {}
        if p.get("by") == "role" and p.get("name"):
            key = ("role", p.get("role"), _norm(p["name"]), bool(p.get("exact")))
        elif p.get("by") == "text" and p.get("value"):
            key = ("text", _norm(p["value"]))
        else:
            continue
        groups.setdefault(key, []).append(m)
    for members in groups.values():
        if len(members) > 1:
            for k, m in enumerate(members):
                m["match_index"] = k   # snapshot-order GUESS; act() re-derives the
                                       # true ordinal from the live element's rect
    return adjusted


def compact_elements(models, limit=40, include_hidden=False):
    """Build observe()'s token-light element list from full element models.

    Groups visually identical REPEATS — same role + name + container, e.g. the
    'Request Product' button on every one of 12 product cards — into ONE entry
    with repeats=N. Without grouping, repeated card actions flood the element
    cap and crowd unique controls (the search box) out of the percept entirely
    (benchmark BENCH-010: observe() missed the catalog search input).

    Adds 'ctx' (page region: sidebar/nav/dialog/left-rail/...) so same-name
    elements in different regions are distinguishable intents, and 'href' for
    links so SPA navigation targets are visible without clicking.

    Pure. Returns (items, hidden_total)."""
    items, hidden_total, groups = [], 0, {}
    for m in models:
        visible = m.get("visible", True)
        if not visible:
            hidden_total += 1
            if not include_hidden:
                continue
        name = (m.get("name") or "")[:60]
        key = (m.get("role") or m.get("tag"), _norm(name), m.get("container") or "")
        if visible and key[1] and key in groups:
            groups[key]["repeats"] = groups[key].get("repeats", 1) + 1
            continue
        if len(items) >= limit:
            continue   # cap new entries, but keep counting repeats of listed groups
        item = {"ref": m["ref"], "role": m.get("role") or m.get("tag"), "name": name}
        if m.get("input_type"):
            item["type"] = m["input_type"]
        if m.get("container"):
            item["ctx"] = m["container"]
        if m.get("href"):
            item["href"] = m["href"]
        if not visible:
            item["hidden_reason"] = m.get("hidden_reason", "")
        if m.get("disabled"):
            item["disabled"] = True
        if m.get("broken"):
            item["broken"] = True
        if m.get("ambiguous"):
            item["ambiguous"] = True
        items.append(item)
        if visible and key[1]:
            groups[key] = item
    return items, hidden_total


def infer_hints(model):
    """Interaction hints the harness auto-applies so the FIRST attempt is right.
    Returns a list of hint strings (empty for ordinary elements)."""
    hints = []
    itype = (model.get("input_type") or "").lower()
    if not model.get("visible", True) and itype in ("radio", "checkbox"):
        hints.append("force-click")        # hidden styled input (Tailwind pattern)
    if itype == "number":
        hints.append("sequential-fill")    # MUI number inputs reject fill()
    if model.get("synthetic_role"):
        hints.append("synthetic")          # role-less clickable div, role synthesized
    return hints


def match_option(options, value):
    """Pick the dropdown option that best matches `value`. Handles decorated
    option text like 'SUPERTECH LIMITED(9850763440)SUPERTECH INDIA PVT LTD.'
    when the agent asked for 'SUPERTECH LIMITED'. Returns the option string
    to click, or None."""
    if not options:
        return None
    want = _norm(value)
    if not want:
        return None
    normed = [(o, _norm(o)) for o in options if o and _norm(o)]
    for o, n in normed:                    # 1. exact
        if n == want:
            return o
    for o, n in normed:                    # 2. option starts with the value
        if n.startswith(want):
            return o
    for o, n in normed:                    # 3. value appears inside the option
        if want in n:
            return o
    for o, n in normed:                    # 4. option appears inside the value
        if n in want:
            return o
    want_tokens = set(want.split())        # 5. token overlap (>= 60%)
    if want_tokens:
        best, best_score = None, 0.0
        for o, n in normed:
            score = len(want_tokens & set(n.split())) / len(want_tokens)
            if score > best_score:
                best, best_score = o, score
        if best_score >= 0.6:
            return best
    return None
