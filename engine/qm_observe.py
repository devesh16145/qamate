"""Page observation for the fast explorer: Playwright's AI snapshot, parsed.

`page.aria_snapshot(mode="ai")` returns a compact accessibility tree with element refs
(`[ref=e12]`) in tens of milliseconds and covers iframes. Refs act directly through the
`aria-ref=e12` selector and are turned into stable test locators by qm_selectors.

This module parses that text into elements the explorer can rank and reason about:
role, accessible name, state (checked, disabled, ...), the regions they sit in (dialog,
navigation, form, table row, ...), link targets and any inline text.
"""
import json
import re
import time
from dataclasses import dataclass, field

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch",
    "slider", "spinbutton", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "tab", "treeitem", "listbox",
}
REGION_ROLES = {
    "dialog", "alertdialog", "navigation", "main", "banner", "contentinfo", "form",
    "search", "region", "article", "complementary", "menu", "menubar", "tablist",
    "table", "grid", "row", "list", "listitem", "toolbar", "group",
}

_LINE = re.compile(
    r'^(?P<role>[a-z][a-z-]*)'
    r'(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?:\s+\[[^\]]*\])*)'
    r'(?::\s*(?P<inline>.*))?$')
_ATTR = re.compile(r'\[([a-z-]+)(?:=([^\]]*))?\]')


@dataclass
class Element:
    ref: str
    role: str
    name: str = ""
    attrs: dict = field(default_factory=dict)
    inline: str = ""
    url: str = ""
    depth: int = 0
    regions: list = field(default_factory=list)   # [(role, name), ...] outermost first
    text: list = field(default_factory=list)       # text lines directly inside

    @property
    def label(self):
        """What a user would call it: accessible name, else its visible text."""
        return self.name or self.inline or " ".join(self.text).strip()

    @property
    def interactive(self):
        return self.role in INTERACTIVE_ROLES or self.attrs.get("cursor") == "pointer"

    def region(self, *roles):
        for role, name in reversed(self.regions):
            if not roles or role in roles:
                return role, name
        return None

    def summary(self):
        """One line for a model prompt: role "label" (state) in region."""
        bits = [f'{self.role} "{self.label}"' if self.label else self.role]
        state = [k if v is True else f"{k}={v}" for k, v in self.attrs.items()
                 if k not in ("ref", "cursor", "active")]
        if state:
            bits.append("(" + ", ".join(state) + ")")
        where = self.region("dialog", "alertdialog", "row", "form", "navigation", "listitem")
        if where:
            bits.append(f"in {where[0]}" + (f' "{where[1][:40]}"' if where[1] else ""))
        return " ".join(bits)


def _unquote(raw):
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw.replace('\\"', '"')


def _attrs(raw):
    out = {}
    for key, val in _ATTR.findall(raw or ""):
        out[key] = True if val == "" else val
    return out


def parse(snapshot_text):
    """AI snapshot text -> (elements with refs, page text lines)."""
    elements, texts = [], []
    stack = []   # (indent, Element or None, role, name)
    for raw in snapshot_text.splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        indent = len(raw) - len(stripped)
        body = stripped[2:].rstrip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if body.startswith("/url:"):
            if parent is not None:
                parent.url = body[5:].strip()
            continue
        if body.startswith("text:"):
            text = body[5:].strip().strip('"')
            texts.append(text)
            if parent is not None:
                parent.text.append(text)
            continue
        m = _LINE.match(body)
        if not m:
            continue
        role, name = m.group("role"), _unquote(m.group("name") or "")
        attrs = _attrs(m.group("attrs"))
        inline = (m.group("inline") or "").strip().strip('"')
        regions = [(r, n) for _, _, r, n in stack if r in REGION_ROLES]
        el = None
        if "ref" in attrs:
            el = Element(ref=attrs.pop("ref"), role=role, name=name, attrs=attrs, inline=inline,
                         depth=indent // 2, regions=regions)
            elements.append(el)
        if inline and not el:
            texts.append(inline)
        elif inline and role in ("paragraph", "heading", "generic", "cell", "status", "alert", "listitem"):
            texts.append(inline)
        if name and role in ("heading", "cell", "status", "alert"):
            texts.append(name)
        stack.append((indent, el, role, name))
    return elements, texts


@dataclass
class Observation:
    url: str
    title: str
    text: str           # the raw AI snapshot
    elements: list
    page_text: list
    ms: int

    def by_ref(self, ref):
        return next((e for e in self.elements if e.ref == ref), None)

    def interactive(self):
        return [e for e in self.elements if e.interactive]


def observe(page, timeout_ms=5000):
    """Snapshot the page (all frames) and parse it."""
    started = time.monotonic()
    text = page.aria_snapshot(mode="ai", timeout=timeout_ms)
    elements, page_text = parse(text)
    try:
        title = page.title()
    except Exception:
        title = ""
    return Observation(url=page.url, title=title, text=text, elements=elements,
                       page_text=page_text, ms=round((time.monotonic() - started) * 1000))
