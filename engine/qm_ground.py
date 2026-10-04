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


# Interchangeable UI wording, so a planner that guessed "New contact" or "Sign in" for a
# page it hadn't seen still lands on "Create contact" / "Log in" without a re-plan.
SYNONYMS = {
    "new": "create", "add": "create",
    "submit": "save", "update": "save", "apply": "save", "confirm": "save",
    "modify": "edit", "change": "edit",
    "remove": "delete", "find": "search", "next": "continue", "proceed": "continue",
    "close": "cancel", "dismiss": "cancel", "register": "signup",
}
_PHRASES = [(re.compile(r"\b(?:log|sign)[\s-]*in\b"), "login"),
            (re.compile(r"\b(?:log|sign)[\s-]*out\b"), "logout"),
            (re.compile(r"\bsign[\s-]*up\b"), "signup")]


def words(text):
    return [w for w in re.split(r"[^a-z0-9#$.]+", (text or "").lower()) if w]


def core_words(text):
    ws = words(text)
    core = [w for w in ws if w not in GENERIC_WORDS]
    return core or ws


def canonical_words(text):
    """Core words with synonyms and simple plurals folded: 'New Contacts' -> create contact."""
    text = (text or "").lower()
    for pattern, repl in _PHRASES:
        text = pattern.sub(repl, text)
    out = []
    for w in core_words(text):
        w = SYNONYMS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    # Counts decorate labels ("1 contact", "Inbox (3)"); they don't name the control.
    return [w for w in out if not w.isdigit()] or out


def _norm(text):
    return " ".join(words(text))


POINTER_OPS = {"click", "dblclick", "hover"}


@dataclass
class Candidate:
    element: object
    score: float
    why: str
    group: str = ""   # the clickable thing it stands for: a card's heading and the card link are one
    exact: float = 0.0    # 1.0: the label IS the target; 0.95: same words up to synonyms (fitting control only)


def shown_text(element, limit=40):
    """Everything an element displays: its own label and text plus its descendants'."""
    parts, stack = [element.label, element.inline, *element.text], list(element.children)
    while stack and len(parts) < limit:
        node = stack.pop(0)
        parts += [node.label, node.inline, *node.text]
        stack.extend(node.children)
    return " ".join(p for p in parts if p)


def clickable_container(element, levels=4):
    """The link/button (or pointer-cursor element) a non-interactive element sits in --
    clicking a card's heading clicks the card."""
    node = element.parent
    for _ in range(levels):
        if node is None:
            return None
        if node.interactive:
            return node
        node = node.parent
    return None


def name_score(target, label):
    if not label:
        return 0.0
    t, l = _norm(target), _norm(label)
    tc = " ".join(core_words(target))
    if l and (l == t or l == tc):
        return 1.0
    ct, cl = canonical_words(target), canonical_words(label)
    if ct and ct == cl:
        return 0.95   # same words up to synonyms/plurals: "New Contact" ~ "Create contact"
    if len(tc) >= 3 and (tc in l or l in tc):
        return 0.85
    a, b = set(ct), set(cl)
    if not a or not b:
        return 0.0
    return 0.8 * len(a & b) / len(a | b)


def role_score(op, element, target_text):
    wanted = ROLES_FOR.get(op)
    hinted = {ROLE_WORDS[w] for w in words(target_text) if w in ROLE_WORDS}
    if hinted and element.role in hinted:
        return 1.0
    if hinted and (wanted is None or element.role in wanted):
        return 0.5   # "the Contacts tab" is not the "Contacts" link
    if wanted is None:
        return 0.8 if element.interactive or op.startswith("expect") else 0.5
    if element.role in wanted:
        return 1.0
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
        container = clickable_container(el) if op in POINTER_OPS and not el.interactive else None
        if hint_role and hint_name and el.role == hint_role and _norm(el.label) == _norm(hint_name):
            ns, rs = 1.0, 1.0
        else:
            ns = max(name_score(target, el.label), name_score(target, el.name))
            rs = role_score(op, container or el, target)
        if ns == 0:
            continue
        score = 0.75 * ns + 0.25 * rs + context_score(step.get("within"), el)
        if el.attrs.get("disabled") and not op.startswith("expect"):
            score *= 0.5
        if op == "expect_text" and step.get("value") and _norm(step["value"]) in _norm(shown_text(el)):
            score += 0.2   # "Company shows X": the control displaying X, not the bare label "Company"
        why = f"name {ns:.2f}, role {rs:.2f}" + (f", inside {container.role}" if container else "")
        out.append(Candidate(el, round(score, 3), why, (container or el).ref, exact=ns if ns >= 0.95 and rs >= 0.9 else 0.0))
    # One candidate per clickable thing; on a tie prefer readable text over an image.
    best = {}
    for c in sorted(out, key=lambda c: (-c.score, c.element.role == "img")):
        best.setdefault(c.group, c)
    return sorted(best.values(), key=lambda c: -c.score)[:limit]


def decide(candidates, *, sure=0.8, margin=0.15):
    """The clear winner, or None when a model (or a person) should choose."""
    if not candidates:
        return None
    top = candidates[0]
    second = candidates[1].score if len(candidates) > 1 else 0.0
    if top.score >= sure and top.score - second >= margin:
        return top
    # "Contacts" means the control labelled exactly that, not a "0 contacts" tab that only
    # contains the word -- unless another control is labelled exactly the same.
    if top.score >= sure and top.exact and not any(c.exact >= top.exact for c in candidates[1:]):
        return top
    # Look-alike links that go to the same place (a name in a header and in a sidebar) are
    # the same choice.
    close = [c for c in candidates if top.score - c.score < margin]
    if top.score >= sure and top.element.role == "link" and top.element.url and \
            all(c.element.role == "link" and c.element.url == top.element.url for c in close):
        return top
    return None
