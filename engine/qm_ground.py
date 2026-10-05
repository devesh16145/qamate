"""Grounding: which element on the page does a plain-language step mean?

The rule is *exact or ask*. A step's target is a control's name as a user reads it, and
an element is taken without a model only when its displayed name IS that target:

  exact      the same words                      "Add to cart"
  decorated  the same words once counts, badges and bracketed extras on the label are
             ignored                             "Cart" ~ "Cart, 2 items"; "Inbox" ~ "Inbox (3)"
  synonym    the same words up to common UI wording and plurals
                                                 "New contact" ~ "Create contact"

...and the element can take the action (a fill needs a field, a check needs a checkbox),
and it is the only such element once the step's `within`, an open dialog, the best
fitting role and "these links go to the same place" have been applied.

Everything else -- a label that merely contains the words or shares some of them, or
several equally good elements -- is a short ranked list for the decision model or the
planner. A wrong click that replays green is worse than a question, so nothing fuzzy
is ever accepted here. (The earlier weighted scorer accepted substring matches and
clicked "Ticket 1" for "Ticket 150"; see engine/probes.)
"""
import dataclasses
import functools
import re
import unicodedata
from dataclasses import dataclass

# ── words ─────────────────────────────────────────────────────────────────────
_KEEP = "#$."


@functools.lru_cache(maxsize=8192)
def _word_char(ch):
    if ch.isascii():
        return ch.isalnum() or ch in _KEEP
    return unicodedata.category(ch)[0] in "LMN"   # letters, marks (Indic vowel signs), numbers


def words(text):
    """Lower-cased word tokens in any script. '#', '$' and '.' stay inside tokens
    ("#1002", "$15.99", "v2.6"); other punctuation and symbols separate words."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    out, cur = [], []
    for ch in text:
        if _word_char(ch):
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return [w for w in (t.strip(".") for t in out) if w]


STOP_WORDS = {"the", "a", "an"}
# A role named in the step ("Login button", "Contacts tab") constrains the element's role.
# "search" and "menu" are not here: they are far more often part of a control's name.
ROLE_WORDS = {
    "button": {"button", "link"}, "btn": {"button", "link"},     # links styled as buttons, and back
    "link": {"link", "button"}, "tab": {"tab"},
    "checkbox": {"checkbox"}, "radio": {"radio"}, "switch": {"switch", "checkbox"},
    "toggle": {"switch", "checkbox", "button"},
    "dropdown": {"combobox", "listbox", "button"}, "select": {"combobox", "listbox"},
    "combobox": {"combobox"}, "option": {"option", "menuitem"},
    "field": {"textbox", "searchbox", "spinbutton", "combobox"},
    "input": {"textbox", "searchbox", "spinbutton", "combobox"},
    "textbox": {"textbox", "searchbox"}, "box": {"textbox", "searchbox", "checkbox", "combobox"},
    "icon": {"button", "link", "img"}, "heading": {"heading"}, "image": {"img"},
}
GENERIC_WORDS = set(ROLE_WORDS) | STOP_WORDS | {"text", "menu", "item", "control", "area", "page", "form",
                                                 "named", "labelled", "labeled", "to", "on", "in", "of", "for"}

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
_BRACKETED = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_NUMBER = re.compile(r"[#$]?[\d.]+%?")


def _singular(w):
    if not w.isascii() or len(w) <= 3 or w.endswith("ss"):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes", "zes", "ses")):
        return w[:-2]
    return w[:-1] if w.endswith("s") else w


def _plain(tokens):
    return [w for w in tokens if w not in STOP_WORDS] or list(tokens)


def canonical_words(text):
    """Words with UI synonyms and simple plurals folded, role and filler words dropped:
    'New Contacts' -> [create, contact]. Used for relevance, never for acceptance."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    for pattern, repl in _PHRASES:
        text = pattern.sub(repl, text)
    ws = words(text)
    core = [w for w in ws if w not in GENERIC_WORDS] or ws
    out = [_singular(SYNONYMS.get(w, w)) for w in core]
    return [w for w in out if not w.isdigit()] or out


def core_words(text):
    ws = words(text)
    return [w for w in ws if w not in GENERIC_WORDS] or ws


def _norm(text):
    return " ".join(words(text))


def _synonym_form(tokens):
    text = " ".join(tokens)
    for pattern, repl in _PHRASES:
        text = pattern.sub(repl, text)
    return [_singular(SYNONYMS.get(w, w)) for w in text.split()]


def label_variants(label, keep_numbers):
    """Token lists a label may be read as: as written, without bracketed extras, and
    (unless the target itself names a number) without counts and their unit word."""
    out = []
    for text in dict.fromkeys((label, _BRACKETED.sub(" ", label))):
        tokens = _plain(words(text))
        if not tokens:
            continue
        out.append(tokens)
        if keep_numbers or not any(_NUMBER.fullmatch(t) for t in tokens):
            continue
        no_counts = [t for t in tokens if not _NUMBER.fullmatch(t)]
        no_units = [t for i, t in enumerate(tokens)
                    if not _NUMBER.fullmatch(t) and not (i and _NUMBER.fullmatch(tokens[i - 1]))]
        out += [v for v in (no_counts, no_units) if v]
    return out


def readings(target):
    """Ways to read a step's target: the whole text as the control's name, or -- when it
    starts or ends with a role word -- the rest as the name with that role required."""
    tokens = _plain(words(target))
    out = [(tokens, None)]
    if len(tokens) > 1:
        if tokens[-1] in ROLE_WORDS:
            out.append((tokens[:-1], ROLE_WORDS[tokens[-1]]))
        if tokens[0] in ROLE_WORDS:
            out.append((tokens[1:], ROLE_WORDS[tokens[0]]))
    return [(t, r) for t, r in out if t]


MATCH_RANK = {"exact": 3, "decorated": 2, "synonym": 1, "partial": 0}


def match_kind(target, label, role=None):
    """('exact' | 'decorated' | 'synonym' | 'partial' | None, relevance 0..1) of a label
    for a target. `role` is the element's role (for targets that name one)."""
    if not label:
        return None, 0.0
    best = (None, 0.0)
    for tokens, roles in readings(target):
        if roles is not None and role not in roles:
            continue
        has_number = any(_NUMBER.fullmatch(t) for t in tokens)
        variants = label_variants(label, keep_numbers=has_number)
        if not variants:
            continue
        if variants[0] == tokens:
            return "exact", 1.0
        kind = None
        if tokens in variants or [_singular(t) for t in tokens] in [[_singular(t) for t in v] for v in variants]:
            kind = "decorated"
        elif _synonym_form(tokens) in [_synonym_form(v) for v in variants]:
            kind = "synonym"
        if kind and MATCH_RANK[kind] > MATCH_RANK.get(best[0], -1):
            best = (kind, 0.97 if kind == "decorated" else 0.95)
    if best[0]:
        return best
    def bag(text):                          # numbers count here: "Ticket 150" is not "Ticket 1"
        tokens = set(_synonym_form(_plain(words(text))))
        return tokens - GENERIC_WORDS or tokens
    a, b = bag(target), bag(label)
    if not a or not b or not a & b:
        return None, 0.0
    return "partial", round(0.2 + 0.6 * len(a & b) / len(a | b), 3)


# ── role fit ──────────────────────────────────────────────────────────────────
POINTER_OPS = {"click", "dblclick", "hover"}
VALUE_ROLES = {"textbox", "searchbox", "spinbutton", "combobox", "slider"}
CHECK_ROLES = {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"}
_TIERS = {   # roles that can take the action, best fit first
    "fill": [{"textbox", "searchbox"}, {"combobox", "spinbutton"}, {"slider"}],
    "select": [{"combobox", "listbox"}, {"button"}],
    "check": [CHECK_ROLES],
    "upload": [{"button", "link"}, {"textbox"}],
    "expect_value": [VALUE_ROLES],
    "expect_checked": [CHECK_ROLES],
    "click": [{"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "treeitem",
               "switch", "checkbox", "radio"}, {"combobox", "listbox"}, VALUE_ROLES],
}
_TIERS["dblclick"] = _TIERS["click"]


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


def fit(op, element, container=None):
    """How well the element can take the action: 0 is the natural kind of control, larger
    is a poorer fit, None means it cannot (a paragraph can't be filled)."""
    tiers = _TIERS.get(op)
    if tiers is None:
        # hover, press, expect_visible/hidden/text: anything named can take it, but a control
        # comes before the plain text that labels it ("Date of Birth" the field, not its caption).
        return 0 if (container or element).interactive else 1
    actor = container or element
    for index, roles in enumerate(tiers):
        if actor.role in roles:
            return index
    if op in ("click", "dblclick", "upload") and actor.interactive:
        return len(tiers)                  # a clickable <div>
    return None


# Containers: clicking "the navigation" or "the form" means nothing -- only what is in them.
STRUCTURAL = {"main", "banner", "contentinfo", "navigation", "form", "search", "region", "complementary",
              "dialog", "alertdialog", "table", "grid", "treegrid", "rowgroup", "list", "menu", "menubar",
              "tablist", "tabpanel", "toolbar", "group", "radiogroup", "listbox", "tree", "feed", "document",
              "application", "iframe"}


def plain(element):
    """A piece of plain content -- a line of text, a table cell, a list entry, a caption --
    that is not known to be a control. Apps do react to clicks on such things (a cell that
    opens for editing, a `<summary>`, a file in a list), so one named exactly like the
    target is worth a try when no control is; see plain_pick."""
    return not element.interactive and not element.children and element.role not in STRUCTURAL


def plain_pick(candidates, step):
    """The one piece of plain content named exactly as the step says (its `within` honoured
    like for any control), or None. Never a synonym: only the words on the page."""
    pool = [dataclasses.replace(c, tier=0) for c in candidates
            if c.tier is None and c.match in ("exact", "decorated") and plain(c.element)]
    return decide(pool, step) if pool else None


def shown_text(element, limit=40):
    """Everything an element displays: its own label and text plus its descendants'."""
    parts, stack = [element.label, element.inline, *element.text], list(element.children)
    while stack and len(parts) < limit:
        node = stack.pop(0)
        parts += [node.label, node.inline, *node.text]
        stack.extend(node.children)
    return " ".join(p for p in parts if p)


def reading_text(element, limit=120):
    """What an element says, once and in reading order: its own text, then its children's."""
    out = []

    def visit(node):
        if len(out) >= limit:
            return
        own = node.inline or " ".join(node.text)
        if own:
            out.append(own)
        elif not node.children and node.name:
            out.append(node.name)             # a leaf named by an attribute (an image, an icon button)
        for child in node.children:
            visit(child)
    visit(element)
    return " ".join(" ".join(out).split())


def _strict(text):
    return " ".join((text or "").casefold().split())


def context(element, levels=6, item_size=30):
    """What says where an element is: the labels of its item (row, card, list entry) and the
    names and headings of the sections and dialogs it sits in. Returns (words, labels):
    every word, and each label as written (lower-cased) for telling "Galaxy S20" from
    "Galaxy S20+" and "Galaxy S20 Ultra"."""
    labels = {_strict(part) for part in (element.context or "").split(" · ")}
    labels.update(_strict(name) for _, name in element.regions)
    twin = (element.role, _norm(element.label))
    node, own_item = element.parent, True
    for _ in range(levels):
        if node is None:
            break
        inside, stack = [], list(node.children)
        while stack and len(inside) <= item_size:
            inner = stack.pop()
            inside.append(inner)
            stack.extend(inner.children)
        # Once a container also holds another control like this one (the next row's "Edit"),
        # its labels no longer say which one this is.
        if any(n is not element and (n.role, _norm(n.label)) == twin for n in inside):
            own_item = False
        if own_item:
            labels.add(_strict(node.name))
            labels.update(_strict(t) for t in node.text)
            if len(inside) <= item_size:             # a small container is the item: all its labels
                for inner in inside:
                    if inner is not element:
                        labels.add(_strict(inner.label))
                        labels.update(_strict(t) for t in inner.text)
            else:
                for child in node.children:          # a larger section is named by its heading
                    if child.role in ("heading", "caption", "legend"):
                        labels.add(_strict(child.label))
        node = node.parent
    labels.discard("")
    return {w for label in labels for w in words(label)}, labels


def context_words(element):
    return context(element)[0]


@dataclass
class Candidate:
    element: object
    score: float
    why: str
    group: str = ""        # the clickable thing it stands for: a card's heading and the card link are one
    match: str = "partial"  # exact | decorated | synonym | partial
    tier: object = None     # role fit (0 best); None = cannot take the action
    context: object = None  # share of the step's `within` words found around it (None: no `within`)
    shows: bool = False     # for a text check: the element displays the expected text
    in_item: int = 0        # a label around it IS the step's `within`: 2 as written, 1 punctuation aside, 0 no
    fresh: bool = False     # it is part of what the last action opened (a calendar, a menu, a panel)

    @property
    def exact(self):
        """1.0 / 0.97 / 0.95 for an exact / decorated / synonym name on a control that can take
        the action, else 0 -- kept for callers that only need 'is this a real match?'."""
        if self.tier is None:
            return 0.0
        return {"exact": 1.0, "decorated": 0.97, "synonym": 0.95}.get(self.match, 0.0)


def rank(step, observation, limit=10):
    """Candidates for a step {op, target, within?, role?, name_hint?, value?}, best first:
    real name matches on fitting controls, then everything that merely resembles it."""
    op = step["op"]
    target = step.get("target") or ""
    hint_role, hint_name = step.get("role"), step.get("name_hint")
    want = set(words(step.get("within") or ""))
    value = _norm(step.get("value")) if op == "expect_text" and step.get("value") else ""
    out = []
    for el in observation.elements:
        if not el.label or el.attrs.get("aria-hidden"):   # e.g. the hidden native <select> behind a custom dropdown
            continue
        container = clickable_container(el) if op in POINTER_OPS and not el.interactive else None
        actor = container or el
        if hint_role and hint_name and el.role == hint_role and _norm(el.label) == _norm(hint_name):
            kind, relevance = "exact", 1.0
        else:
            kind, relevance = match_kind(target, el.label, actor.role)
            if el.name and el.name != el.label:
                other = match_kind(target, el.name, actor.role)
                if MATCH_RANK.get(other[0], -1) > MATCH_RANK.get(kind, -1):
                    kind, relevance = other
            for alias in getattr(el, "aliases", ()):
                # The visible label beside a field named by its placeholder: a real match, one
                # notch below the control's own name.
                other = match_kind(target, alias, actor.role)
                if other[0] in ("exact", "decorated"):
                    other = ("decorated", 0.97)
                if MATCH_RANK.get(other[0], -1) > MATCH_RANK.get(kind, -1):
                    kind, relevance = other
        if kind is None:
            continue
        tier = fit(op, el, container)
        if tier is not None and el.attrs.get("disabled") and not op.startswith("expect"):
            tier += 10                      # a disabled control is the last resort
        around_words, around_labels = context(el) if want else (set(), set())
        context_share = len(want & around_words) / len(want) if want else None
        in_item = 0
        if want:
            in_item = 2 if _strict(step.get("within")) in around_labels else \
                1 if _norm(step.get("within")) in {_norm(label) for label in around_labels} else 0
        score = relevance - (0.35 if tier is None else 0.02 * min(tier, 5)) + 0.1 * (context_share or 0) + (0.05 if in_item else 0)
        shows = bool(value) and value in _norm(shown_text(el))
        if shows:
            score += 0.05                   # "Company shows X": the control displaying X
        out.append(Candidate(el, round(score, 3), f"{kind} name" + ("" if tier is not None else ", wrong kind of control")
                             + (f", inside {container.role}" if container else ""),
                             actor.ref, kind, tier, context_share, shows, in_item))
    # One candidate per clickable thing; prefer readable text over an image of the same name.
    best = {}
    for c in sorted(out, key=lambda c: (-MATCH_RANK[c.match] if c.tier is not None else 1, -c.score, c.element.role == "img")):
        best.setdefault(c.group, c)
    ordered = sorted(best.values(), key=lambda c: (-(MATCH_RANK[c.match] if c.tier is not None else -1), -c.score))
    return ordered[:limit]


_REAL_URL = re.compile(r"^(?!#$|javascript:|about:)\S")


def decide(candidates, step=None):
    """The element to act on without asking anyone, or None.

    Only real name matches on controls that can take the action are considered, in order
    of strictness (exact, then decorated, then synonym). The step's `within` must be
    fully matched when given. Among several, an open dialog wins, then what is on screen,
    then what the last step opened, then the best-fitting role; links that go to one real
    address count as one."""
    for kind in ("exact", "decorated", "synonym"):
        pool = [c for c in candidates if c.match == kind and c.tier is not None]
        if not pool:
            continue
        if pool[0].context is not None:
            pool = [c for c in pool if c.context == 1.0]
            if not pool:
                return None                # right name, but not where the step said
            closest = max(c.in_item for c in pool)      # "Galaxy S20" itself, not "Galaxy S20+" or "... Ultra"
            if closest:
                pool = [c for c in pool if c.in_item == closest]
        in_dialog = [c for c in pool if c.element.region("dialog", "alertdialog")]
        if in_dialog and len(in_dialog) < len(pool):
            pool = in_dialog
        in_view = [c for c in pool if not getattr(c.element, "offscreen", False)]
        if in_view and len(in_view) < len(pool):
            pool = in_view                 # not the copy in a closed drawer or on a slide that is not showing
        opened = [c for c in pool if c.fresh]
        if opened and len(opened) < len(pool):
            pool = opened                  # the one in what the last step opened, like the one in an open dialog
        best_tier = min(c.tier for c in pool)
        pool = [c for c in pool if c.tier == best_tier]
        for narrower in ([c for c in pool if c.shows],                      # a text check: where the text is
                         [c for c in pool if c.element.role != "img"]):     # a caption over its own picture
            if narrower and len(narrower) < len(pool):
                pool = narrower
        if len(pool) == 1:
            return pool[0]
        if step and step.get("op") in ("expect_visible", "expect_hidden"):
            return pool[0]                 # several things with this name: any of them answers "is it shown?"
        urls = {c.element.url for c in pool}
        if all(c.element.role == "link" for c in pool) and len(urls) == 1 and _REAL_URL.match(next(iter(urls)) or ""):
            return pool[0]                 # the same destination, listed twice
        return None                        # several equally good: ask
    return None
