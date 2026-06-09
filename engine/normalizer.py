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
    return adjusted


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
