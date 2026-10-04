"""Grounding: which element on the page does a plain-language step mean?

Deterministic and fast (no model call). Each candidate is scored on
  * name match: the step's target words vs the element's label;
  * role fit: a fill wants a text box, a check wants a checkbox, ...;
  * context: words from the step's `within` (e.g. a product name or row text) found
    in the element's item context or region names.
A clear winner is used directly. Otherwise the short ranked list goes to the decision
model (Jev) or the planner -- never the whole page.
"""
import re
from dataclasses import dataclass

GENERIC_WORDS = {
    "button", "btn", "link", "field", "input", "box", "textbox", "text", "dropdown", "select",
    "menu", "tab", "the", "a", "an", "to", "on", "in", "of", "for", "icon", "checkbox",
    "option", "item", "control", "area", "page", "form", "named", "labelled", "labeled",
}
ROLE_WORDS = {"button": "button", "link": "link", "tab": "tab", "checkbox": "checkbox",
              "radio": "radio", "dropdown": "combobox", "select": "combobox", "menu": "menuitem",
              "option": "option", "switch": "switch", "toggle": "switch", "search": "searchbox"}
ROLES_FOR = {
    "fill": {"textbox", "searchbox", "spinbutton", "combobox"},
    "select": {"combobox", "listbox"},
    "check": {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"},
    "click": {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option",
              "checkbox", "radio", "switch", "treeitem", "combobox"},
    "hover": None, "expect_visible": None, "expect_hidden": None, "expect_text": None,
    "expect_value": {"textbox", "searchbox", "spinbutton", "combobox"},
    "expect_checked": {"checkbox", "radio", "switch"}, "upload": {"button", "textbox"},
    "press": None,
}


def words(text):
    return [w for w in re.split(r"[^a-z0-9#$.]+", (text or "").lower()) if w]


def core_words(text):
    ws = words(text)
    core = [w for w in ws if w not in GENERIC_WORDS]
    return core or ws


def _norm(text):
    return " ".join(words(text))


@dataclass
class Candidate:
    element: object
    score: float
    why: str


def name_score(target, label):
    if not label:
        return 0.0
    t, l = _norm(target), _norm(label)
    tc = " ".join(core_words(target))
    if l and (l == t or l == tc):
        return 1.0
    if len(tc) >= 3 and (tc in l or l in tc):
        return 0.85
    a, b = set(core_words(target)), set(words(label))
    if not a or not b:
        return 0.0
    return 0.8 * len(a & b) / len(a | b)


def role_score(op, element, target_text):
    wanted = ROLES_FOR.get(op)
    hinted = {ROLE_WORDS[w] for w in words(target_text) if w in ROLE_WORDS}
    if hinted and element.role in hinted:
        return 1.0
    if wanted is None:
        return 0.8 if element.interactive or op.startswith("expect") else 0.5
    if element.role in wanted:
        return 0.9 if hinted else 1.0
    if element.interactive and op == "click":
        return 0.6   # e.g. a clickable generic div
    return 0.1


def context_score(within, element):
    """`within` names one item ("Plan A", "Sauce Labs Onesie", "#1002"): every word
    counts, and a full match must clearly beat items that share some words."""
    if not within:
        return 0.0
    want = set(words(within))
    have = set(words(element.context)) | {w for _, n in element.regions for w in words(n)}
    if not want:
        return 0.0
    hit = len(want & have) / len(want)
    return 0.4 * hit * hit - (0.15 if hit == 0 else 0.0)


def rank(step, observation, limit=10):
    """Candidates for a step dict {op, target, within?, role?, name?}, best first."""
    op = step["op"]
    target = step.get("target") or ""
    hint_role, hint_name = step.get("role"), step.get("name_hint")
    out = []
    for el in observation.elements:
        if not el.label and not el.interactive:
            continue
        if hint_role and hint_name and el.role == hint_role and _norm(el.label) == _norm(hint_name):
            ns, rs = 1.0, 1.0
        else:
            ns = max(name_score(target, el.label), name_score(target, el.name))
            rs = role_score(op, el, target)
        if ns == 0:
            continue
        score = 0.75 * ns + 0.25 * rs + context_score(step.get("within"), el)
        if el.attrs.get("disabled") and not op.startswith("expect"):
            score *= 0.5
        out.append(Candidate(el, round(score, 3), f"name {ns:.2f}, role {rs:.2f}"))
    out.sort(key=lambda c: -c.score)
    return out[:limit]


def decide(candidates, *, sure=0.8, margin=0.15):
    """The clear winner, or None when a model (or a person) should choose."""
    if not candidates:
        return None
    top = candidates[0]
    second = candidates[1].score if len(candidates) > 1 else 0.0
    if top.score >= sure and top.score - second >= margin:
        return top
    return None
