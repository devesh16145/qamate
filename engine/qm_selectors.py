"""Stable test locators for snapshot refs.

An `aria-ref=e12` handle is only valid for the current snapshot. To record a step we
ask Playwright for the best-practice locator of that exact element
(`Locator.normalize()`: test ids, then roles + accessible names, labels, ...), confirm it
matches exactly that one element, and render it as readable Python
(`page.get_by_role("button", name="Login")`). The rendering is verified to produce the
same selector, so the test targets precisely what the live step did.
"""
import json
import re

_PART_SPLIT = " >> "
_QUOTED = r'"((?:[^"\\]|\\.)*)"'


def selector_of(locator):
    """The Playwright selector string behind a Locator."""
    sel = getattr(getattr(locator, "_impl_obj", None), "_selector", None)
    if sel:
        return sel
    m = re.search(r"selector='(.*)'>$", repr(locator))
    return m.group(1) if m else ""


def _q(text):
    """Python string literal: double quotes, or single when the text has double quotes."""
    if '"' in text and "'" not in text:
        return repr(text)
    return json.dumps(text, ensure_ascii=False)


def _role_call(spec):
    m = re.match(r'^([a-z-]+)((?:\[[a-z-]+=(?:"(?:[^"\\]|\\.)*"[is]?|[^\]]+)\])*)$', spec)    # a quoted name may hold "]"
    if not m:
        return None
    role, attrs = m.group(1), m.group(2)
    args = [_q(role)]
    for key, raw in re.findall(r'\[([a-z-]+)=((?:"(?:[^"\\]|\\.)*"[is]?)|[^\]]+)\]', attrs):
        if key == "name":
            qm = re.match(_QUOTED + r"([is]?)$", raw)
            if not qm:
                return None
            args.append(f"name={_q(json.loads(chr(34) + qm.group(1) + chr(34)))}")
            if qm.group(2) == "s":
                args.append("exact=True")
        elif key in ("checked", "pressed", "selected", "expanded", "disabled"):
            if raw not in ("true", "false"):
                return None
            args.append(f"{key}={raw == 'true'}")
        elif key == "level" and raw.isdigit():
            args.append(f"level={raw}")
        elif key == "include-hidden" and raw == "true":
            args.append("include_hidden=True")
        else:
            return None
    return f"get_by_role({', '.join(args)})"


def _text_call(method, raw):
    m = re.match(_QUOTED + r"([is]?)$", raw)
    if not m:
        return None
    text = json.loads('"' + m.group(1) + '"')
    return f"{method}({_q(text)}{', exact=True' if m.group(2) == 's' else ''})"


def _part_call(part):
    if part.startswith("internal:role="):
        return _role_call(part[len("internal:role="):])
    if part.startswith("internal:testid="):
        m = re.match(r'^\[data-testid=' + _QUOTED + r's?\]$', part[len("internal:testid="):])
        return f"get_by_test_id({_q(json.loads(chr(34) + m.group(1) + chr(34)))})" if m else None
    if part.startswith("internal:label="):
        return _text_call("get_by_label", part[len("internal:label="):])
    if part.startswith("internal:text="):
        return _text_call("get_by_text", part[len("internal:text="):])
    for attr, method in (("placeholder", "get_by_placeholder"), ("alt", "get_by_alt_text"), ("title", "get_by_title")):
        prefix = f"internal:attr=[{attr}="
        if part.startswith(prefix) and part.endswith("]"):
            return _text_call(method, part[len(prefix):-1])
    if part.startswith("internal:has-text="):
        m = re.match(_QUOTED + r"i$", part[len("internal:has-text="):])
        return f"filter(has_text={_q(json.loads(chr(34) + m.group(1) + chr(34)))})" if m else None
    if part.startswith("internal:has-not-text="):
        m = re.match(_QUOTED + r"i$", part[len("internal:has-not-text="):])
        return f"filter(has_not_text={_q(json.loads(chr(34) + m.group(1) + chr(34)))})" if m else None
    if part.startswith("nth="):
        n = part[4:]
        return "first" if n == "0" else "last" if n == "-1" else f"nth({n})" if n.lstrip("-").isdigit() else None
    if part.startswith("internal:") or part.startswith("aria-ref="):
        return None
    return f"locator({_q(part)})"   # plain CSS, e.g. [data-test="login-button"]


def to_python(selector):
    """Readable Python for a selector, e.g. 'page.get_by_role("button", name="Login")'.
    Falls back to page.locator("<selector>") for forms it doesn't know."""
    out = "page"
    for part in selector.split(_PART_SPLIT):
        call = _part_call(part.strip())
        if call is None:
            return f"page.locator({_q(selector)})"
        out += "." + call
    return out


def python_matches(page, expr, selector):
    """True when the Python expression builds exactly `selector` on this page."""
    try:
        return selector_of(eval(expr, {"page": page})) == selector
    except Exception:
        return False


_NAMED_BY_CONTENT = {"button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
                     "heading", "cell", "columnheader", "rowheader", "treeitem", "switch", "checkbox", "radio"}
_SCOPE_ROLES = ("row", "dialog", "alertdialog", "listitem", "form", "article", "region", "group", "navigation")


def handle(element):
    """The selector that reaches a parsed element right now: its snapshot ref, or -- for
    the few elements the snapshot does not list -- where it is in the page."""
    return getattr(element, "selector", "") or f"aria-ref={element.ref}"


def _same_element(page, target, selector):
    """`selector` matches exactly one element, and it is `target`."""
    try:
        candidate = page.locator(selector)
        if candidate.count() != 1:
            return False
        target.evaluate("el => { window.__qmPick = el; }")
        return bool(candidate.evaluate("el => el === window.__qmPick"))
    except Exception:
        return False


def _role_candidates(page, role, name, regions):
    """Canonical role+name selectors (built by Playwright itself): plain, then scoped to a
    named region; then the same including content the app hides from assistive technology.
    (Apps do ship visible, working screens under a stray aria-hidden -- a modal or drawer
    that forgot to clean up -- and role locators match nothing there unless told to look.)"""
    out = []
    for hidden in ({}, {"include_hidden": True}):
        try:
            out.append(selector_of(page.get_by_role(role, name=name, **hidden)))
            out.append(selector_of(page.get_by_role(role, name=name, exact=True, **hidden)))
            for scope_role, scope_name in reversed(regions or []):
                if scope_role in _SCOPE_ROLES and scope_name:
                    scope = page.get_by_role(scope_role, name=scope_name, **hidden)
                    out.append(selector_of(scope.get_by_role(role, name=name, **hidden)))
                    out.append(selector_of(scope.get_by_role(role, name=name, exact=True, **hidden)))
        except Exception:
            pass
    return out


def locator_for_ref(page, ref, role=None, name=None, regions=None, via=None, hints=()):
    """Best stable locator for snapshot element `ref` (or the element `via` selects).
    `hints` are selectors to prefer when one of them is exactly this element.

    Preference: a test id (most stable) > role + accessible name > the same scoped to its
    row/dialog/... > Playwright's own best-practice locator. Each candidate must match
    exactly this element right now. Returns {selector, python, unique, positional}:
    `positional` means only an index (nth) could tell it apart, which breaks if the list
    order changes.
    """
    target = page.locator(via or f"aria-ref={ref}")
    normalized = selector_of(target.normalize())
    test_id = normalized.startswith("internal:testid=") or bool(re.match(r'^\[data-[\w-]+=', normalized))
    candidates = ([normalized] if test_id else []) + list(hints)
    if role and name:
        candidates += _role_candidates(page, role, name, regions)
    candidates.append(normalized)
    selector, unique = normalized, False
    for candidate in dict.fromkeys(c for c in candidates if c):
        if _same_element(page, target, candidate):
            selector, unique = candidate, True
            break
    expr = to_python(selector)
    if not python_matches(page, expr, selector):
        expr = f"page.locator({_q(selector)})"
    return {"selector": selector, "python": expr, "unique": unique,
            "positional": bool(re.search(r"(^| >> )nth=", selector))}


def locator_for(page, element):
    """locator_for_ref for a parsed qm_observe.Element. A name recovered from the page counts
    when it is the control's real accessible name (Playwright's role locators see it even
    where the AI snapshot does not print it); a label that was guessed never does."""
    name = element.name or (element.fallback if getattr(element, "label_source", "") == "name" else None)
    if not name and element.role in _NAMED_BY_CONTENT and element.label and element.label != element.fallback:
        name = element.label      # its own text is its name; every candidate is verified against the element anyway
    if getattr(element, "selector", "") and not element.name:
        name = None                   # reached by its place in the page: it has no role or name to go by
    return locator_for_ref(page, element.ref, element.role, name or None, element.regions, via=handle(element),
                           hints=getattr(element, "hints", ()))


# A selector built on an id or attribute the app generated: a record's number, a framework's
# counter. It finds the element now and may not on the next run.
_GENERATED = re.compile(r"\d{3,}|:r[0-9a-z]+:|\b(?:mat|mui|ember|react-select|radix|headlessui|rc|el-id|ng)-[\w-]*\d")


def generated(selector):
    """True for a plain CSS selector (not a role, label or text locator) that leans on a
    generated-looking value: '#grid-image-40-212469', 'input[name="quantity[252341]"]'."""
    return bool(selector) and not selector.startswith("internal:") and " >> internal:" not in selector \
        and bool(_GENERATED.search(selector))


def nth_locator(page, element):
    """For one of several identical items chosen by position: role + name + its place among
    them -- page.get_by_role("link", name="iPhone", exact=True).nth(1). None if it cannot be
    told by role and name at all."""
    name = element.name or (element.label if element.role in _NAMED_BY_CONTENT else "")
    if not name:
        return None
    target = page.locator(handle(element))
    try:
        same = page.get_by_role(element.role, name=name, exact=True)
        target.evaluate("el => { window.__qmPick = el; }")
        for index in range(min(same.count(), 40)):
            if same.nth(index).evaluate("el => el === window.__qmPick"):
                selector = selector_of(same.nth(index))
                expr = to_python(selector)
                if python_matches(page, expr, selector):
                    return {"selector": selector, "python": expr, "unique": True, "positional": True}
                return None
    except Exception:
        return None
    return None


def text_locator(page, text):
    """A locator for plain text the page shows -- a caption, a label, a line in a list --
    when exactly one visible element shows exactly this text. Same shape as locator_for_ref;
    None when the text is not there or is there more than once."""
    try:
        found = page.get_by_text(text, exact=True)
        if found.count() == 1 and found.is_visible():
            selector = selector_of(found)
        elif found.filter(visible=True).count() == 1:
            selector = selector_of(found.filter(visible=True))
        else:
            return None
    except Exception:
        return None
    expr = to_python(selector)
    if not python_matches(page, expr, selector):
        expr = f"page.locator({_q(selector)})"
    return {"selector": selector, "python": expr, "unique": True, "positional": False}


def label_locator(page, text, kinds):
    """A field found through its <label> (or aria-label) although the accessibility tree does
    not list it -- the real checkbox, radio button or file field an app hides behind a styled
    label. `kinds` are the input types wanted ('checkbox', 'radio', 'file', 'text'). None
    unless exactly one such field carries this label."""
    try:
        found = page.get_by_label(text, exact=True)
        if found.count() != 1:
            return None
        kind = found.evaluate("el => el.tagName === 'INPUT' ? (/^(checkbox|radio|file)$/.test(el.type) ? el.type : 'text') :"
                              " /^(TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable ? 'text' : ''", timeout=1000)
    except Exception:
        return None
    if kind not in kinds:
        return None
    selector = selector_of(found)
    expr = to_python(selector)
    if not python_matches(page, expr, selector):
        expr = f"page.locator({_q(selector)})"
    return {"selector": selector, "python": expr, "unique": True, "positional": False, "kind": kind}


_ITEM_ROLES = ("row", "listitem", "article", "group", "region", "dialog", "form")


def scoped_locator(page, element, anchors):
    """For a control that repeats (the "Edit" of every row): a locator that finds its item
    by what the item says instead of by position --
        page.get_by_role("row").filter(has_text="Bach").get_by_role("link", name="Edit")
    `anchors` are texts that identify the item, most specific first. Returns the same shape
    as locator_for_ref, or None when no anchored locator pins down exactly this element."""
    target = page.locator(handle(element))
    roles = list(dict.fromkeys(r for r, _ in reversed(element.regions) if r in _ITEM_ROLES))
    name = element.name or (element.fallback if getattr(element, "label_source", "") == "name" else "")
    for hidden in ({}, {"include_hidden": True}):
        for role in roles:
            for anchor in anchors:
                anchor = (anchor or "").strip()
                if not anchor or len(anchor) > 80:
                    continue
                try:
                    scope = page.get_by_role(role, **hidden).filter(has_text=anchor)
                    inner = (scope.get_by_role(element.role, name=name, exact=True, **hidden) if name
                             else scope.get_by_role(element.role, **hidden))
                    selector = selector_of(inner)
                except Exception:
                    continue
                if selector and _same_element(page, target, selector):
                    expr = to_python(selector)
                    if python_matches(page, expr, selector):
                        return {"selector": selector, "python": expr, "unique": True, "positional": False}
    return None


def anchored_locator(page, element, text):
    """page.get_by_role(<role>).filter(has_text=<text>) when that is exactly this element --
    a row, card or section found by what it says. None otherwise."""
    target = page.locator(handle(element))
    for hidden in ({}, {"include_hidden": True}):
        try:
            selector = selector_of(page.get_by_role(element.role, **hidden).filter(has_text=text))
        except Exception:
            continue
        if selector and _same_element(page, target, selector):
            expr = to_python(selector)
            if python_matches(page, expr, selector):
                return {"selector": selector, "python": expr, "unique": True, "positional": False}
    return None
