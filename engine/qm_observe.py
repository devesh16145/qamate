"""Page observation for the fast explorer: Playwright's AI snapshot, parsed and completed.

`page.aria_snapshot(mode="ai")` returns a compact accessibility tree with element refs
(`[ref=e12]`) in tens of milliseconds and covers iframes. Refs act directly through the
`aria-ref=e12` selector and are turned into stable test locators by qm_selectors.

This module parses that text into elements the explorer can rank and reason about:
role, accessible name, state (checked, disabled, ...), the regions they sit in (dialog,
navigation, form, table row, ...), link targets and any inline text.

The snapshot alone is not enough on real apps: icon-only buttons, fields labelled by
plain text beside them, and some controls Playwright itself can name all arrive without
a name. `enrich()` asks the page about exactly those elements -- visible text, title,
tooltip, icon, test id, the text next to a field -- so they can be planned and matched
like any other control. The label's source is kept, so a guess is never passed off as
the control's real name.
"""
import json
import re
import time
from dataclasses import dataclass, field

from qm_ground import words

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
# What these show inline is their VALUE (typed text, chosen option), never their name.
VALUE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"}

# One snapshot line is `key`, `key:` or `key: value`, where key = role "name" [attr]...
# Playwright wraps the key in single quotes when it contains ': ', ' #', braces and the
# like (labels such as "Status: Active" or "Total: $14.15"), and a value in double quotes.
_NAME = r'"(?:[^"\\]|\\.)*"|/(?:[^/\\]|\\.)*/'
_KEY = re.compile(r'^(?P<role>[a-z/][\w/-]*)(?:\s+(?P<name>' + _NAME + r'))?(?P<attrs>(?:\s+\[[^\]]*\])*)\s*$')
_UNQUOTED_KEY = re.compile(r'^[a-z/][\w/-]*(?:\s+(?:' + _NAME + r'))?(?:\s+\[[^\]]*\])*')
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
    parent: object = None                          # nearest ancestor Element (with a ref)
    children: list = field(default_factory=list)   # descendant Elements one level down
    context: str = ""                              # nearby labels that tell repeated controls apart
    content: str = ""                              # a nameless control's inner labels (card links)
    fallback: str = ""                             # label taken from the page when the snapshot has none
    label_source: str = ""                         # where `fallback` came from: text, title, icon, nearby, id
    props: dict = field(default_factory=dict)      # /placeholder and other snapshot properties
    region_refs: list = field(default_factory=list)   # ref of each entry in `regions` (None when it has none)
    aliases: list = field(default_factory=list)       # what else a user would call it: the visible label beside a field

    @property
    def label(self):
        """What a user would call it: the accessible name; else its visible text (not for
        fields -- their text is their value); else the labels inside it (a card); else
        what the page itself says about it (see enrich)."""
        if self.name:
            if self.role in VALUE_ROLES and self.inline and self.name != self.inline and self.name.endswith(self.inline):
                return self.name[: -len(self.inline)].strip()    # "Department Marketing": name + current value
            return self.name
        own = "" if self.role in VALUE_ROLES else (self.inline or " ".join(self.text).strip())
        for text in (own, self.content, self.fallback, self.props.get("placeholder", "")):
            if text and words(text):
                return text
        return own or self.content

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
        if self.aliases:
            state.append("labelled " + ", ".join(f'"{a}"' for a in self.aliases[:2]))
        if not self.name and self.label and self.label == self.fallback and self.label_source in ("icon", "nearby", "id"):   # a guess, say so
            state.append({"icon": "unnamed, from its icon", "nearby": "unnamed, from the text beside it",
                          "id": "unnamed, from its id"}[self.label_source])
        if state:
            bits.append("(" + ", ".join(state) + ")")
        where = self.region("dialog", "alertdialog", "row", "form", "navigation", "listitem")
        if where:
            bits.append(f"in {where[0]}" + (f' "{where[1][:40]}"' if where[1] else ""))
        if self.context:
            bits.append(f"near {self.context[:80]}")
        return " ".join(bits)


CELL_ROLES = {"cell", "gridcell", "columnheader", "rowheader"}


class _Row:
    """Placeholder for a table row's text while the tree is still being read."""
    def __init__(self, element, name):
        self.element, self.name = element, name


def _unquote(raw):
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw.replace('\\"', '"')


def _value(raw):
    """A YAML value as Playwright writes it: plain, or double-quoted with \\-escapes."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return json.loads(re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", raw))
        except Exception:
            return raw[1:-1]
    return raw


def _split(body):
    """'key', 'key:' or 'key: value' -> (key, value | None)."""
    if body.startswith("'"):
        chars, i = [], 1
        while i < len(body):
            if body[i] == "'":
                if body[i + 1:i + 2] == "'":      # '' is an escaped quote
                    chars.append("'")
                    i += 2
                    continue
                break
            chars.append(body[i])
            i += 1
        key, rest = "".join(chars), body[i + 1:]
    else:
        m = _UNQUOTED_KEY.match(body)
        key, rest = (m.group(0), body[m.end():]) if m else (body, "")
    rest = rest.strip()
    return key.strip(), (_value(rest[1:]) if rest.startswith(":") else None)


def _attrs(raw):
    out = {}
    for key, val in _ATTR.findall(raw or ""):
        out[key] = True if val == "" else val
    return out


def parse_tree(snapshot_text):
    """AI snapshot text -> (elements with refs, page text lines), before labels are completed."""
    elements, texts = [], []
    stack = []   # (indent, Element or None, role, name)
    for raw in snapshot_text.splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        indent = len(raw) - len(stripped)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        key, value = _split(stripped[2:].rstrip())
        if key.startswith("/"):                   # a property of the parent: /url, /placeholder
            if parent is not None and value is not None:
                if key == "/url":
                    parent.url = value
                else:
                    parent.props[key[1:]] = value
            continue
        if key == "text":
            text = value or ""
            texts.append(text)
            if parent is not None:
                parent.text.append(text)
            continue
        m = _KEY.match(key)
        if not m:
            continue
        role, raw_name = m.group("role"), m.group("name") or ""
        name = _unquote(raw_name[1:-1]) if raw_name.startswith('"') else raw_name[1:-1]
        attrs = _attrs(m.group("attrs"))
        inline = (value or "").strip()
        inside = [(r, n, e.ref if e is not None else None) for _, e, r, n in stack if r in REGION_ROLES]
        regions = [(r, n) for r, n, _ in inside]
        el = None
        if "ref" in attrs:
            ancestor = next((e for _, e, _, _ in reversed(stack) if e is not None), None)
            el = Element(ref=attrs.pop("ref"), role=role, name=name, attrs=attrs, inline=inline,
                         depth=indent // 2, regions=regions, parent=ancestor,
                         region_refs=[ref for _, _, ref in inside])
            if ancestor is not None:
                ancestor.children.append(el)
            elements.append(el)
        in_row = any(r == "row" for _, _, r, _ in stack)
        if role == "row":
            texts.append(_Row(el, name))             # one line per table row, filled in below
        elif role in CELL_ROLES and in_row:
            pass                                      # said by its row
        elif inline and not el:
            texts.append(inline)
        elif inline and role in ("paragraph", "heading", "generic", "cell", "gridcell", "status", "alert", "listitem"):
            texts.append(inline)
        if name and role not in CELL_ROLES and role not in INTERACTIVE_ROLES and role not in REGION_ROLES \
                and role not in ("img", "generic", "rowgroup", "row", "document", "application", "figure"):
            texts.append(name)                        # content named by its own text: headings, status, definitions...
        stack.append((indent, el, role, name))
    lines = []
    for text in texts:
        if isinstance(text, _Row):
            cells = [c.label for c in (text.element.children if text.element is not None else []) if c.role in CELL_ROLES]
            text = " | ".join(" ".join(c.split()) for c in cells if c) or text.name
        if text:
            lines.append(text)
    return elements, lines


def parse(snapshot_text):
    """AI snapshot text -> (elements with refs, page text lines). Offline: no page needed,
    so nameless controls keep only what the snapshot says about them."""
    elements, texts = parse_tree(snapshot_text)
    _add_content(elements)
    _add_context(elements)
    return elements, texts


def _subtree(el):
    out = []
    for child in el.children:
        out.append(child)
        out.extend(_subtree(child))
    return out


def _add_content(elements):
    """A link or button with no accessible name (a card wrapping a heading and an image)
    is still called something by users: the labels inside it."""
    for e in elements:
        if e.interactive and not (e.name or (e.role not in VALUE_ROLES and (e.inline or e.text))):
            inner = [n.name or n.inline or " ".join(n.text) for n in _subtree(e)[:12] if not n.interactive]
            e.content = " ".join(dict.fromkeys(l.strip() for l in inner if l and l.strip()))[:100]


def _add_context(elements):
    """For controls whose label repeats (six 'Add to cart' buttons), record the labels
    of their item -- the largest enclosing container that holds only this one copy of
    the control (a product card, a list item, a row) -- so a step can say which it means."""
    def key(e):
        return (e.role, (e.label or "").lower())
    counts = {}
    for e in elements:
        if e.interactive and e.label:
            counts[key(e)] = counts.get(key(e), 0) + 1
    for e in elements:
        if not (e.interactive and counts.get(key(e), 0) > 1):
            continue
        best, node = None, e.parent
        while node is not None:
            nodes = _subtree(node)
            if len(nodes) > 25 or any(n is not e and key(n) == key(e) for n in nodes):
                break
            best, node = node, node.parent
        if best is None:
            continue
        labels = [n.label for n in _subtree(best) if n is not e and n.label and key(n) != key(e)]
        labels += best.text
        e.context = " · ".join(dict.fromkeys(l.strip() for l in labels if l and l.strip()))[:160]


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


# ── completing labels from the page ───────────────────────────────────────────
DESCRIBE_JS = r"""el => {
  const clean = (s) => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const attr = (node, n) => clean(node.getAttribute && node.getAttribute(n));
  const tag = el.tagName.toLowerCase();
  const isField = /^(input|select|textarea)$/.test(tag) || el.isContentEditable ||
                  /^(textbox|combobox|listbox|spinbutton|slider|checkbox|radio|switch|searchbox)$/.test(attr(el, 'role'));
  const own = (node) => clean(node.nodeType === 3 ? node.textContent : (node.innerText || node.textContent || ''));
  const sibling = (dir) => {            // text right before / after the control, up to the next control or line break
    let node = el, acc = '';
    for (let i = 0; i < 5; i++) {
      node = dir > 0 ? node.nextSibling : node.previousSibling;
      if (!node) break;
      if (node.nodeType === 1) {
        if (/^(BR|HR)$/.test(node.tagName)) { if (acc) break; continue; }
        if (node.matches('input, select, textarea, button, [role=button], [role=checkbox], [role=radio]')) break;
      } else if (node.nodeType !== 3) continue;
      const t = own(node);
      if (t) { acc = dir > 0 ? acc + ' ' + t : t + ' ' + acc; break; }
    }
    return clean(acc);
  };
  const around = () => {                // the label-like text a user would read for this field
    const kind = (el.type || attr(el, 'role') || '').toLowerCase();
    const after = /^(checkbox|radio|switch)$/.test(kind);
    let text = after ? (sibling(1) || sibling(-1)) : (sibling(-1) || sibling(1));
    let node = el;
    for (let up = 0; !text && up < 3 && node.parentElement; up++) {
      const parent = node.parentElement;
      // A box holding several fields is a form row or section: its caption is not this field's label.
      if (parent.querySelectorAll('input, select, textarea').length > 1) break;
      const rest = clean((parent.innerText || '').replace(el.innerText || '', ''));
      if (rest && rest.length <= 60) { text = rest; break; }
      const prev = parent.previousElementSibling;
      if (prev && !/^H[1-6]$/.test(prev.tagName) && !prev.querySelector('input, select, textarea, button') &&
          own(prev) && own(prev).length <= 60) { text = own(prev); break; }
      node = parent;
    }
    if (!text) {                        // a field in a table: its column header
      const cell = el.closest('td'), table = el.closest('table');
      if (cell && table) {
        const head = table.querySelectorAll('thead th, tr:first-child th')[cell.cellIndex];
        if (head) text = own(head);
      }
    }
    return text.length <= 60 ? text : '';
  };
  const icon = () => {
    const nodes = [el, ...el.querySelectorAll('i, span, svg, use, img, [class*="icon"], [data-icon], [data-lucide]')].slice(0, 14);
    const skip = /^(fw|lg|sm|xs|xl|\d+x|spin|pulse|fixed|solid|regular|light|thin|brands|duotone|sharp|inverse|pull|stack|li|border|md|outline|fill|line|button|btn|wrapper|container|only|left|right|prefix|suffix)$/;
    for (const n of nodes) {
      const direct = attr(n, 'data-icon') || attr(n, 'data-lucide') || attr(n, 'data-feather') ||
                     (n.tagName && n.tagName.includes('-') && /icon/i.test(n.tagName) ? (attr(n, 'icon') || attr(n, 'name') || attr(n, 'svgicon') || attr(n, 'fonticon')) : '');
      if (direct) return direct;
      const href = attr(n, 'href') || attr(n, 'xlink:href');
      if (n.tagName && n.tagName.toLowerCase() === 'use' && href.includes('#')) return href.split('#').pop();
      const cls = typeof n.className === 'string' ? n.className : (n.className && n.className.baseVal) || '';
      for (const token of cls.split(/\s+/)) {
        const m = token.match(/^(?:fa[a-z]?|bi|mdi|ti|ri|pi|bx[sl]?|icon|icons|glyphicon|lucide|anticon|feather|oi|uil|la[a-z]?|ion|codicon|el-icon|tabler-icon|icofont|typcn|ph|gg|iconoir|cil|cib)-(.+)$/i);
        if (m && !skip.test(m[1])) return m[1];
      }
    }
    return '';
  };
  const firstImg = el.querySelector('img[alt]'), svgTitle = el.querySelector('svg title');
  const box = el.getBoundingClientRect(), style = getComputedStyle(el);
  // The accessible name as the author gave it. Playwright's AI snapshot omits it for some
  // controls its default snapshot does name (a button with an icon element inside, fields
  // labelled through <label for>), so it is read here.
  const root = el.getRootNode();
  const byIds = (ids) => clean((ids || '').split(/\s+/).map((id) => {
    const node = id && (root.getElementById ? root.getElementById(id) : document.getElementById(id));
    return node ? own(node) : ''; }).join(' '));
  const wrapping = el.closest('label');
  const named = byIds(attr(el, 'aria-labelledby')) || attr(el, 'aria-label') ||
                (el.labels && el.labels.length ? clean([...el.labels].map(own).join(' ')) : (wrapping && wrapping !== el ? own(wrapping) : ''));
  return {
    tag, field: isField, named: named.slice(0, 120),
    hidden: (box.width <= 1 && box.height <= 1) || style.visibility === 'hidden' || style.display === 'none',
    text: own(el).slice(0, 100),
    value: tag === 'input' && /^(button|submit|reset)$/.test(el.type) ? clean(el.value) : '',
    title: attr(el, 'title'), placeholder: attr(el, 'placeholder'),
    tooltip: attr(el, 'data-tooltip') || attr(el, 'data-original-title') || attr(el, 'data-bs-original-title') ||
             attr(el, 'data-bs-title') || attr(el, 'data-tip') || attr(el, 'data-title') || attr(el, 'mattooltip') || attr(el, 'aria-description'),
    alt: firstImg ? clean(firstImg.alt) : '', svg_title: svgTitle ? clean(svgTitle.textContent) : '',
    icon: icon(), nearby: isField ? around() : '',
    testid: attr(el, 'data-testid') || attr(el, 'data-test') || attr(el, 'data-test-id') || attr(el, 'data-qa') || attr(el, 'data-cy'),
    id: el.id || '', name: attr(el, 'name'),
  };
}"""

# What an icon's name usually means on a button.
ICON_MEANING = {
    "pencil": "edit", "pen": "edit", "edit": "edit", "trash": "delete", "bin": "delete", "delete": "delete",
    "xmark": "close", "times": "close", "close": "close", "x": "close", "plus": "add", "add": "add",
    "magnifying": "search", "search": "search", "gear": "settings", "cog": "settings", "settings": "settings",
    "download": "download", "upload": "upload", "eye": "view", "ellipsis": "more", "dots": "more", "more": "more",
    "kebab": "more", "bars": "menu", "hamburger": "menu", "menu": "menu", "floppy": "save", "save": "save",
    "copy": "copy", "clone": "copy", "filter": "filter", "funnel": "filter", "print": "print", "printer": "print",
    "refresh": "refresh", "rotate": "refresh", "sync": "refresh", "reload": "refresh", "bell": "notifications",
    "calendar": "calendar", "paperclip": "attach", "share": "share", "star": "favorite", "heart": "favorite",
    "question": "help", "help": "help", "info": "info", "logout": "log out", "home": "home", "house": "home",
    "cart": "cart", "basket": "cart", "envelope": "mail", "mail": "mail", "lock": "lock", "unlock": "unlock",
    "play": "play", "pause": "pause", "stop": "stop", "link": "link", "comment": "comment", "chat": "chat",
}
_ID_NOISE = {"btn", "button", "input", "field", "txt", "lbl", "ctl", "ctrl", "el", "id", "test", "data",
             "icon", "link", "action", "toggle", "trigger", "wrapper", "container", "component",
             # prefixes UI frameworks put on generated ids
             "mat", "mui", "ng", "react", "ember", "radix", "headlessui", "rc", "ant", "chakra", "cdk", "mdc", "css"}


def _humanize(identifier):
    """'btnSaveOrder' / 'add-to-cart' / 'edit-1041' -> 'save order' / 'add to cart' / 'edit';
    '' when nothing readable is left (generated ids such as 'mat-input-3381' or ':r1:')."""
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier or "")
    tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", parts) if t]
    words_only = [t for t in tokens if t.isalpha() and 3 <= len(t) <= 20 and t not in _ID_NOISE]
    short = [t for t in tokens if t in ("to", "of", "in", "on", "by", "ok", "go", "no")]
    if not words_only or len(words_only) > 5:
        return ""
    return " ".join(t for t in tokens if t in words_only or t in short)


def _icon_label(icon):
    tokens = [t for t in re.split(r"[^a-z0-9]+", (icon or "").lower()) if t and not t.isdigit()]
    for token in tokens:
        if token in ICON_MEANING:
            return ICON_MEANING[token]
    return " ".join(tokens[:3])


def pick_label(info):
    """(label, source) for a control the snapshot left nameless, from what the page says
    about it. Fields are named by placeholder, title or the text beside them; buttons and
    links by visible text, title/tooltip, image, icon and -- last -- a readable id."""
    if info.get("field"):
        order = [("named", "name"), ("placeholder", "placeholder"), ("title", "title"), ("tooltip", "title"),
                 ("nearby", "nearby")]
    else:
        order = [("named", "name"), ("text", "text"), ("value", "text"), ("title", "title"), ("tooltip", "title"),
                 ("alt", "title"), ("svg_title", "title")]
    for key, source in order:
        text = (info.get(key) or "").strip()
        if text and words(text):
            return text[:80], source
    if not info.get("field"):
        label = _icon_label(info.get("icon"))
        if label:
            return label, "icon"
    for key in ("testid", "name", "id"):
        label = _humanize(info.get(key))
        if label:
            return label, "id"
    return "", ""


# Containers whose name says what their contents are (a "Join date" group of three
# segments, a "Billing address" region): named only by the author, never by guesswork.
NAMED_CONTAINERS = {"group", "radiogroup", "region", "form", "dialog", "alertdialog", "table", "grid",
                    "tabpanel", "toolbar", "menu", "listbox", "tablist", "navigation"}


FIELD_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio", "switch", "listbox"}
_MAIN_REF = re.compile(r"^e\d+$")


def _label_cache(page):
    """Per-document cache of what the page said about a field (ref -> info). A ref names one
    element for the life of its document, and what stands beside a named field does not
    change, so it is asked once. Refs inside frames are not cached (a frame can reload)."""
    try:
        doc = page.evaluate("() => (window.__qmDoc = window.__qmDoc || String(Math.random()))")
    except Exception:
        return {}
    cache = getattr(page, "_qm_labels", None)
    if not cache or cache.get("doc") != doc:
        cache = {"doc": doc, "info": {}}
        try:
            page._qm_labels = cache
        except Exception:
            pass
    return cache["info"]


def enrich(page, elements, limit=80, budget_ms=500):
    """Complete what the snapshot says about controls from the page itself:
      * a control it left nameless gets a label (its real accessible name first, see module
        docstring);
      * a field named by its placeholder or title also gets the visible label beside it as
        an alias ("name@example.com" is the Email field);
      * a named container's recovered name becomes part of its contents' context."""
    nameless = [e for e in elements if not e.name and not e.attrs.get("aria-hidden")
                and ((e.interactive and not words(e.label)) or e.role in NAMED_CONTAINERS)]
    # real controls first, then clickable wrappers, then containers
    nameless.sort(key=lambda e: (e.role in NAMED_CONTAINERS, e.role not in INTERACTIVE_ROLES))
    named_fields = [e for e in elements if e.name and e.role in FIELD_ROLES and not e.attrs.get("aria-hidden")]
    cache = _label_cache(page)
    started = time.monotonic()

    def describe(el, cached):
        if cached and el.ref in cache:
            return cache[el.ref]
        if (time.monotonic() - started) * 1000 > budget_ms:
            return None
        try:
            info = page.locator(f"aria-ref={el.ref}").evaluate(DESCRIBE_JS, timeout=500)
        except Exception:
            return None
        if cached and _MAIN_REF.match(el.ref):
            cache[el.ref] = info
        return info

    for el in nameless[:limit]:
        info = describe(el, cached=False)       # its text may change while it stays nameless: always fresh
        if not info or info.get("hidden"):      # a visually hidden twin of a custom widget is not a control
            continue
        if el.role in NAMED_CONTAINERS and not el.interactive:
            label, source = (info.get("named") or "", "name")
        else:
            label, source = pick_label(info)
        if label and el.role in VALUE_ROLES and el.inline and label != el.inline and label.endswith(el.inline):
            label = label[: -len(el.inline)].strip()     # "Department Marketing": name + current value
        el.fallback, el.label_source = label, (source if label else "")
    for el in named_fields[:limit]:
        info = describe(el, cached=True)
        if not info or info.get("hidden"):
            continue
        known = {" ".join(words(el.label)), " ".join(words(el.inline))}
        for text in (info.get("nearby"), info.get("named")):
            text = (text or "").strip()
            if text and words(text) and " ".join(words(text)) not in known:
                el.aliases.append(text[:80])
                known.add(" ".join(words(text)))
    # A container's recovered name is part of where its contents are.
    renamed = {e.ref: e for e in elements if e.role in REGION_ROLES and e.fallback and not e.name}
    if renamed:
        for el in elements:
            for index, ref in enumerate(el.region_refs):
                if ref in renamed:
                    el.regions[index] = (renamed[ref].role, renamed[ref].fallback)
    return elements


def observe(page, timeout_ms=5000):
    """Snapshot the page (all frames), parse it and complete missing labels from the page."""
    started = time.monotonic()
    text = page.aria_snapshot(mode="ai", timeout=timeout_ms)
    elements, page_text = parse_tree(text)
    _add_content(elements)
    enrich(page, elements)
    _add_context(elements)
    try:
        title = page.title()
    except Exception:
        title = ""
    return Observation(url=page.url, title=title, text=text, elements=elements,
                       page_text=page_text, ms=round((time.monotonic() - started) * 1000))
