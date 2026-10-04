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
    m = re.match(r"^([a-z-]+)((?:\[[^\]]*\])*)$", spec)
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


_SCOPE_ROLES = ("row", "dialog", "alertdialog", "listitem", "form", "article", "region", "group", "navigation")


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
    """Canonical role+name selectors (built by Playwright itself), plain then scoped."""
    out = []
    try:
        out.append(selector_of(page.get_by_role(role, name=name)))
        out.append(selector_of(page.get_by_role(role, name=name, exact=True)))
        for scope_role, scope_name in reversed(regions or []):
            if scope_role in _SCOPE_ROLES and scope_name:
                scope = page.get_by_role(scope_role, name=scope_name)
                out.append(selector_of(scope.get_by_role(role, name=name)))
                out.append(selector_of(scope.get_by_role(role, name=name, exact=True)))
    except Exception:
        pass
    return out


def locator_for_ref(page, ref, role=None, name=None, regions=None):
    """Best stable locator for snapshot element `ref`.

    Preference: a test id (most stable) > role + accessible name > the same scoped to its
    row/dialog/... > Playwright's own best-practice locator. Each candidate must match
    exactly this element right now. Returns {selector, python, unique, positional}:
    `positional` means only an index (nth) could tell it apart, which breaks if the list
    order changes.
    """
    target = page.locator(f"aria-ref={ref}")
    normalized = selector_of(target.normalize())
    test_id = normalized.startswith("internal:testid=") or bool(re.match(r'^\[data-[\w-]+=', normalized))
    candidates = [normalized] if test_id else []
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
    """locator_for_ref for a parsed qm_observe.Element."""
    return locator_for_ref(page, element.ref, element.role, element.name or None, element.regions)
