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

Two kinds of field never reach the accessibility tree as fields at all, and are added here:
rich-text editors built on a bare `contenteditable` box (no role), and editors that live in
their own frame (the frame's whole body is the text box).
"""
import json
import re
import time
import unicodedata
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
    selector: str = ""                                # how to reach it when the snapshot gave it no ref (see _add_editables)
    hints: list = field(default_factory=list)         # selectors that may identify it in a test, most stable first
    options: list = field(default_factory=list)       # a dropdown's choices as listed: [(label, chosen), ...]
    box: tuple = None                                 # (x, y, width, height) in the viewport when the snapshot was taken
    offscreen: bool = False                           # outside the page and not scrollable into view: a closed drawer, a hidden slide
    framed: bool = False                              # inside an <iframe> (its ref and box belong to that frame)

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
        if self.offscreen:
            state.append("off-screen")
        if self.aliases:
            state.append("labelled " + ", ".join(f'"{a}"' for a in self.aliases[:2]))
        if not self.name and self.label and self.label == self.fallback and self.label_source in ("icon", "nearby", "id"):   # a guess, say so
            state.append({"icon": "unnamed, from its icon", "nearby": "unnamed, from the text beside it",
                          "id": "unnamed, from its id"}[self.label_source])
        if self.options:               # what a `select` step may choose, and what is chosen now
            shown = " | ".join(label[:30] for label, _ in self.options[:12])
            more = f" | +{len(self.options) - 12} more" if len(self.options) > 12 else ""
            chosen = next((label for label, on in self.options if on), None)
            state.append(f"options: {shown}{more}" + (f'; chosen "{chosen[:30]}"' if chosen else ""))
        if state:
            bits.append("(" + ", ".join(state) + ")")
        where = self.region("dialog", "alertdialog", "row", "form", "navigation", "listitem")
        if where:
            bits.append(f"in {where[0]}" + (f' "{where[1][:40]}"' if where[1] else ""))
        if self.context:
            bits.append(f"near {self.context[:80]}")
        return " ".join(bits)


CELL_ROLES = {"cell", "gridcell", "columnheader", "rowheader"}
# Wording inside a sentence: with the text around it, it reads as one line
# ("Account Number : **1004** , Balance : **0**").
PHRASE_ROLES = {"strong", "emphasis", "code", "time", "mark", "subscript", "superscript", "insertion", "deletion"}


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


def _glyphless(text):
    """Text without icon-font glyphs. Icon fonts draw their icons with private-use characters,
    which browsers count as a button's text: a cart button is then "named" by one unreadable
    character and its real label (title, tooltip) is never looked up."""
    if not text or text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Co").strip()


def _box(raw):
    try:
        x, y, w, h = (int(float(part)) for part in str(raw).split(","))
        return x, y, w, h
    except Exception:
        return None


def parse_tree(snapshot_text, owners=None):
    """AI snapshot text -> (elements with refs, page text lines), before labels are completed.
    `owners`, when given, receives the element each text line sits in (None at the top)."""
    elements, texts, said_in = [], [], []
    stack = []   # (indent, Element or None, role, name)
    phrase = None    # the stack entry whose running text the last line of `texts` belongs to

    def say(text, piece_of=None):
        """Add page text. Pieces of one sentence (bare text and bold/italic/code wording
        directly inside the same element) are joined into one line."""
        nonlocal phrase
        if piece_of is not None and piece_of is phrase and texts and isinstance(texts[-1], str):
            texts[-1] = f"{texts[-1]} {text}"
        else:
            texts.append(text)
            said_in.append(next((e for _, e, _, _ in reversed(stack) if e is not None), None))
        phrase = piece_of

    for raw in snapshot_text.splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        indent = len(raw) - len(stripped)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        key, value = _split(stripped[2:].rstrip())
        if isinstance(value, str):
            value = _glyphless(value)
        if key.startswith("/"):                   # a property of the parent: /url, /placeholder
            if parent is not None and value is not None:
                if key == "/url":
                    parent.url = value
                else:
                    parent.props[key[1:]] = value
            continue
        if key == "text":
            text = value or ""
            if parent is not None and parent.role in VALUE_ROLES and stack[-1][1] is parent:
                parent.inline = parent.inline or text     # what a field holds (listed below it when it has a placeholder)
                continue
            say(text, stack[-1] if stack else None)
            if parent is not None:
                parent.text.append(text)
            continue
        m = _KEY.match(key)
        if not m:
            continue
        role, raw_name = m.group("role"), m.group("name") or ""
        name = _glyphless(_unquote(raw_name[1:-1]) if raw_name.startswith('"') else raw_name[1:-1])
        attrs = _attrs(m.group("attrs"))
        box = _box(attrs.pop("box")) if "box" in attrs else None
        inline = (value or "").strip()
        inside = [(r, n, e.ref if e is not None else None) for _, e, r, n in stack if r in REGION_ROLES]
        regions = [(r, n) for r, n, _ in inside]
        el = None
        if "ref" in attrs:
            ancestor = next((e for _, e, _, _ in reversed(stack) if e is not None), None)
            el = Element(ref=attrs.pop("ref"), role=role, name=name, attrs=attrs, inline=inline,
                         depth=indent // 2, regions=regions, parent=ancestor,
                         region_refs=[ref for _, _, ref in inside], box=box,
                         framed=any(r == "iframe" for _, _, r, _ in stack))
            if ancestor is not None:
                ancestor.children.append(el)
            elements.append(el)
        in_row = any(r == "row" for _, _, r, _ in stack)
        if role == "option" and el is None:
            # A native dropdown lists its choices without refs: they belong to the dropdown.
            owner = next((e for _, e, _, _ in reversed(stack) if e is not None), None)
            if owner is not None and owner.role in ("combobox", "listbox"):
                owner.options.append((name or inline, bool(attrs.get("selected"))))
        elif role == "row":
            say(_Row(el, name))                       # one line per table row, filled in below
        elif role in CELL_ROLES and in_row:
            pass                                      # said by its row
        elif inline and role in PHRASE_ROLES:
            say(inline, stack[-1] if stack else None)
        elif role == "link" and (name or inline) and stack and stack[-1] is phrase:
            say(name or inline, stack[-1])            # a link in the middle of a sentence is part of what it says
        elif inline and (not el or (role not in INTERACTIVE_ROLES and role not in VALUE_ROLES)):
            say(inline)                               # what any piece of content says (a field's inline text is its value)
        if name and role not in CELL_ROLES and role not in INTERACTIVE_ROLES and role not in REGION_ROLES \
                and role not in ("img", "generic", "rowgroup", "row", "document", "application", "figure"):
            say(name)                                 # content named by its own text: headings, status, definitions...
        stack.append((indent, el, role, name))
    lines = []
    for text, owner in zip(texts, said_in):
        if isinstance(text, _Row):
            owner = text.element or owner
            cells = [c.label for c in (text.element.children if text.element is not None else []) if c.role in CELL_ROLES]
            text = " | ".join(" ".join(c.split()) for c in cells if c) or text.name
        if text:
            lines.append(text)
            if owners is not None:
                owners.append(owner)
    return elements, lines


def parse(snapshot_text):
    """AI snapshot text -> (elements with refs, page text lines). Offline: no page needed,
    so nameless controls keep only what the snapshot says about them."""
    elements, texts = parse_tree(snapshot_text)
    _add_content(elements)
    _add_plain_names(elements)
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


def _add_plain_names(elements):
    """A button whose name starts with its icon's name ("upload Click to upload", from an
    icon labelled "upload") is also known by its words alone."""
    for e in elements:
        if not (e.interactive and e.name):
            continue
        icons = [n.name for n in _subtree(e)[:8] if n.role == "img" and n.name]
        if not icons:
            continue
        rest = f" {e.name} "
        for icon in icons:
            rest = rest.replace(f" {icon} ", " ", 1)
        rest = " ".join(rest.split())
        if rest and rest != e.name and words(rest) and rest not in e.aliases:
            e.aliases.append(rest)


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
        # what the item says about itself -- its name, price, status -- not its other buttons
        labels = [n.label for n in _subtree(best) if n is not e and n.label and key(n) != key(e)
                  and (n.role == "link" or n.role not in INTERACTIVE_ROLES)]
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
    viewport: tuple = None                 # (width, height) of the window, when known
    text_on_screen: list = None            # for each line of page_text: is it in the window now?

    def by_ref(self, ref):
        return next((e for e in self.elements if e.ref == ref), None)

    def interactive(self):
        return [e for e in self.elements if e.interactive]


# ── completing labels from the page ───────────────────────────────────────────
DESCRIBE_JS = r"""(el, asField) => {
  const clean = (s) => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const attr = (node, n) => clean(node.getAttribute && node.getAttribute(n));
  const tag = el.tagName.toLowerCase();
  const isField = !!asField || /^(input|select|textarea)$/.test(tag) || el.isContentEditable ||
                  /^(textbox|combobox|listbox|spinbutton|slider|checkbox|radio|switch|searchbox)$/.test(attr(el, 'role'));
  const seen = (node) => node.nodeType !== 1 || !node.checkVisibility || node.checkVisibility({ visibilityProperty: true });
  // What a node says to someone looking at the page: nothing when it is hidden (a message
  // kept in the page for later is not the label of the field next to it).
  const own = (node) => node.nodeType === 3 ? clean(node.textContent) : (seen(node) ? clean(node.innerText || node.textContent || '') : '');
  // A label's own words, without the text of the control it wraps (a <select>'s options).
  const labelText = (node) => {
    let text = '';
    for (const child of node.childNodes) {
      if (child.nodeType === 3) text += child.textContent;
      else if (child.nodeType === 1 && !/^(SELECT|TEXTAREA|INPUT|BUTTON|OPTION|SCRIPT|STYLE)$/.test(child.tagName)) text += ' ' + labelText(child) + ' ';
    }
    return clean(text);
  };
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
    const cell = el.closest('td, [role=gridcell], [role=cell]'), table = el.closest('table, [role=grid], [role=table]');
    const header = () => {             // a field in a table: its column header
      if (!cell || !table) return '';
      if (cell.tagName === 'TD') {
        const head = table.querySelectorAll('thead th, tr:first-child th')[cell.cellIndex];
        return head ? own(head) : '';
      }
      const index = [...cell.parentElement.children].indexOf(cell);
      const head = table.querySelectorAll('[role=columnheader]')[index];
      return head ? own(head) : '';
    };
    // Alone in its cell, a field is what its column says -- not the text of the cell before it.
    if (!text && cell && cell.querySelectorAll('input, select, textarea').length === 1) text = header();
    // A <label> (or legend) written before the field in its own form row is its label even when
    // the page forgot to tie the two together -- and a better one than a unit or hint after the
    // field ("machines", "kg"). Looked for before settling on text that follows.
    const before = () => {
      let box = el.parentElement;
      for (let up = 0; up < 7 && box && box !== document.body; up++, box = box.parentElement) {
        // (fields of any make: a date typed as three editable segments is three fields, and the
        // label before them names the group, not its first segment)
        if (box.querySelectorAll('input:not([type=hidden]), select, textarea, [contenteditable=""], [contenteditable="true"], ' +
                                 '[role=spinbutton], [role=textbox], [role=combobox], [role=slider]').length > 1) return '';
        for (const label of box.querySelectorAll('label, legend')) {
          if (label.contains(el) || label.querySelector('input, select, textarea')) continue;
          if (!(label.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
          const said = attr(label, 'title') || labelText(label);
          if (seen(label) && said && said.length <= 60) return said;
        }
      }
      return '';
    };
    if (!after && !sibling(-1)) text = before() || text;
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
    if (!text) text = header();
    return text.length <= 60 ? text : '';
  };
  // A rich-text editor stands in for a <textarea> the page keeps hidden just before it:
  // that textarea's label is the editor's label.
  const replaced = () => {
    let node = el;
    for (let up = 0; up < 8 && node && node !== document.body; up++) {
      const prev = node.previousElementSibling;
      if (prev && prev.tagName === 'TEXTAREA' && prev.labels && prev.labels.length) return clean([...prev.labels].map(labelText).join(' '));
      node = node.parentElement;
    }
    return '';
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
    return node ? clean(node.innerText || node.textContent || '') : ''; }).join(' '));     // a hidden element may still name one
  const wrapping = el.closest('label');
  const named = byIds(attr(el, 'aria-labelledby')) || attr(el, 'aria-label') ||
                (el.labels && el.labels.length ? clean([...el.labels].map(labelText).join(' ')) : (wrapping && wrapping !== el ? labelText(wrapping) : ''));
  return {
    tag, field: isField, named: named.slice(0, 120),
    hidden: (box.width <= 1 && box.height <= 1) || style.visibility === 'hidden' || style.display === 'none',
    text: own(el).slice(0, 100),
    value: tag === 'input' && /^(button|submit|reset)$/.test(el.type) ? clean(el.value) : '',
    title: attr(el, 'title'), placeholder: attr(el, 'placeholder') || attr(el, 'data-placeholder') || attr(el, 'aria-placeholder'),
    tooltip: attr(el, 'data-tooltip') || attr(el, 'data-original-title') || attr(el, 'data-bs-original-title') ||
             attr(el, 'data-bs-title') || attr(el, 'data-tip') || attr(el, 'data-title') || attr(el, 'mattooltip') || attr(el, 'aria-description'),
    alt: firstImg ? clean(firstImg.alt) : '', svg_title: svgTitle ? clean(svgTitle.textContent) : '',
    icon: icon(), nearby: isField ? ((asField && replaced()) || around()) : '',
    testid: attr(el, 'data-testid') || attr(el, 'data-test') || attr(el, 'data-test-id') || attr(el, 'data-qa') || attr(el, 'data-cy'),
    id: el.id || '', name: attr(el, 'name'),
    // what it opens, when it says so: aria-controls="cart-drawer", href="#filters"
    controls: attr(el, 'aria-controls') || ((attr(el, 'href').match(/^#([A-Za-z][\w-]{2,})$/) || [])[1] || ''),
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
    return _humanize(icon)          # readable words, or nothing (sprite ids such as "svgc9ba0888...")


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
    for key in ("testid", "name", "id", "controls"):
        label = _humanize(info.get(key))
        if label:
            return label, "id"
    return "", ""


# Containers whose name says what their contents are (a "Join date" group of three
# segments, a "Billing address" region): named only by the author, never by guesswork.
NAMED_CONTAINERS = {"group", "radiogroup", "region", "form", "dialog", "alertdialog", "table", "grid",
                    "tabpanel", "toolbar", "menu", "listbox", "tablist", "navigation"}


FIELD_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio", "switch", "listbox"}


def _label_cache(page):
    """Per-document cache of what the page said about a field (ref -> info). A ref names one
    element for the life of its document, and what stands beside a named field does not
    change, so it is asked once. Elements inside frames are not cached (a frame can reload).
    (Whether an element is in a frame is read from the tree, not from its ref: after a
    navigation the main document's own refs carry a prefix too -- "f1e12".)"""
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


# One call that asks the page about many elements at once. A snapshot taken with boxes says
# where every element is; the same rectangle finds the element again in the page. Each
# target is [ref, "x,y,w,h", role, what]: what & 1 = describe it (DESCRIBE_JS), what & 2 =
# can it be brought into view at all? An element the page has moved since the snapshot (an
# image finished loading above it) is simply not found here; the caller asks about it by ref.
MANY_JS = r"""
({ targets }) => {
  const describe = %s;
  const page = document.scrollingElement || document.documentElement;
  const key = (el) => { const r = el.getBoundingClientRect();
    return Math.round(r.x) + ',' + Math.round(r.y) + ',' + Math.round(r.width) + ',' + Math.round(r.height); };
  const wanted = new Map();
  targets.forEach((t, i) => { if (!wanted.has(t[1])) wanted.set(t[1], []); wanted.get(t[1]).push(i); });
  const input = (el, types) => el.tagName === 'INPUT' && types.test(el.type);
  const kind = {
    button: (el) => el.tagName === 'BUTTON' || el.tagName === 'SUMMARY' || input(el, /^(button|submit|reset|image|file)$/),
    link: (el) => el.tagName === 'A' || el.tagName === 'AREA',
    textbox: (el) => el.tagName === 'TEXTAREA' || el.isContentEditable || (el.tagName === 'INPUT' && !input(el, /^(checkbox|radio|button|submit|reset|image|file|range|hidden)$/)),
    searchbox: (el) => el.tagName === 'INPUT', combobox: (el) => el.tagName === 'SELECT' || el.tagName === 'INPUT',
    listbox: (el) => el.tagName === 'SELECT', checkbox: (el) => input(el, /^checkbox$/), radio: (el) => input(el, /^radio$/),
    slider: (el) => input(el, /^range$/), spinbutton: (el) => input(el, /^number$/), iframe: (el) => el.tagName === 'IFRAME',
  };
  const fits = (el, role) => (el.getAttribute('role') === role || (kind[role] && kind[role](el)) ? 2 : 1);
  const best = new Array(targets.length).fill(null);
  const visit = (scope) => {
    for (const el of scope.querySelectorAll('*')) {
      if (el.shadowRoot) visit(el.shadowRoot);
      const list = wanted.get(key(el));
      if (!list) continue;
      for (const i of list) {
        const score = fits(el, targets[i][2]);
        if (!best[i] || score > best[i].score) best[i] = { el, score };      // of equals, the outermost
      }
    }
  };
  visit(document);
  // Outside the page's own scroll area, and inside nothing that scrolls: no scrolling shows it.
  const reachable = (el) => {
    const r = el.getBoundingClientRect();
    const out = { x: r.right + scrollX <= 0 || r.left + scrollX >= page.scrollWidth,
                  y: r.bottom + scrollY <= 0 || r.top + scrollY >= page.scrollHeight };
    if (!out.x && !out.y) return true;
    for (let box = el.parentElement; box && box !== document.body; box = box.parentElement) {
      const style = getComputedStyle(box);
      if (out.x && /auto|scroll/.test(style.overflowX) && box.scrollWidth > box.clientWidth + 1) return true;
      if (out.y && /auto|scroll/.test(style.overflowY) && box.scrollHeight > box.clientHeight + 1) return true;
    }
    return false;
  };
  const info = {}, off = [];
  targets.forEach((t, i) => {
    if (!best[i]) return;
    if (t[3] & 1) { try { info[t[0]] = describe(best[i].el, false); } catch (e) {} }
    if ((t[3] & 2) && !reachable(best[i].el)) off.push(t[0]);
  });
  return { info, off };
}
""" % DESCRIBE_JS.replace("%", "%%")


def _ask_many(page, elements, describe, metrics):
    """(info by ref, refs that are off-screen) for main-document elements, in one call.
    Elements the page has moved since the snapshot, and elements in frames, are left out
    (the caller asks about those one by one)."""
    if not metrics:
        return {}, set()
    sx, sy, width, height = metrics[:4]
    wanted = {e.ref for e in describe}
    targets = []
    for el in elements:
        if el.box is None or el.framed:
            continue
        x, y, w, h = el.box
        outside = x + w + sx <= 0 or x + sx >= width or y + h + sy <= 0 or y + sy >= height
        what = (1 if el.ref in wanted else 0) | (2 if outside and (w or h) else 0)
        if what:
            targets.append([el.ref, f"{x},{y},{w},{h}", el.role, what])
    if not targets:
        return {}, set()
    try:
        answer = page.evaluate(MANY_JS, {"targets": targets}) or {}
    except Exception:
        return {}, set()
    return answer.get("info") or {}, set(answer.get("off") or [])


def enrich(page, elements, limit=40, budget_ms=400, metrics=None):
    """Complete what the snapshot says about controls from the page itself:
      * a control it left nameless gets a label (its real accessible name first, see module
        docstring);
      * a field named by its placeholder or title also gets the visible label beside it as
        an alias ("name@example.com" is the Email field);
      * a named container's recovered name becomes part of its contents' context;
      * elements no scrolling can bring into view (a closed side drawer) are marked off-screen.
    All of it is asked in one call (see MANY_JS); what that cannot reach is asked one element
    at a time, within `limit` and `budget_ms`."""
    nameless = [e for e in elements if not e.name and not e.attrs.get("aria-hidden")
                and ((e.interactive and not words(e.label)) or e.role in NAMED_CONTAINERS)]
    # real controls first, then clickable wrappers, then containers
    nameless.sort(key=lambda e: (e.role in NAMED_CONTAINERS, e.role not in INTERACTIVE_ROLES))
    named_fields = [e for e in elements if e.name and e.role in FIELD_ROLES and not e.attrs.get("aria-hidden")]
    # A button that shows only a count (a cart with "0", a bell with "3") is called by its icon or title.
    counters = [e for e in elements if e.interactive and e.role not in FIELD_ROLES and e.label and words(e.label)
                and not any(ch.isalpha() for ch in e.label) and not e.attrs.get("aria-hidden")]
    cache = _label_cache(page)
    answered, off = _ask_many(page, elements,
                              nameless + [e for e in named_fields + counters if e.ref not in cache], metrics)
    for el in elements:
        if el.ref in off:
            el.offscreen = True
    started, asked = time.monotonic(), 0

    def same_field(el, info):
        """A named field found again by its place must still carry that name: if the page
        moved meanwhile, another field may sit where this one was."""
        said = set(words(" ".join(str(info.get(k) or "") for k in ("named", "placeholder", "title", "text"))))
        return bool(said & set(words(el.name)))

    def describe(el, cached):
        nonlocal asked
        if el.ref in answered and (not el.name or el.role not in FIELD_ROLES or same_field(el, answered[el.ref])):
            info = answered[el.ref]
        elif cached and el.ref in cache:
            return cache[el.ref]
        else:
            if asked >= limit or (time.monotonic() - started) * 1000 > budget_ms:
                return None
            asked += 1
            try:
                info = page.locator(f"aria-ref={el.ref}").evaluate(DESCRIBE_JS, timeout=500)
            except Exception:
                return None
        if cached and not el.framed:
            cache[el.ref] = info
        return info

    for el in nameless:
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
    for el in counters:
        info = describe(el, cached=True)
        if info and not info.get("hidden"):
            label, _ = pick_label({**info, "named": "", "text": "", "value": ""})
            if label:
                el.aliases.append(label)
    for el in named_fields:
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


# Editable boxes with no role: the accessibility tree shows them as plain content, or (when
# empty) not at all. For each: a path from the document root (valid right now) and selectors
# a test could use for it -- an id or naming attribute, the editor's class -- most stable first.
EDITABLE_PATHS_JS = r"""
() => {
  const out = [];
  const state = /^(is|has|ng|js)-|blank|empty|focus|active|hover|select|disabled|valid|dirty|touched|pristine|open|show|hidden|visible/i;
  const hints = (el) => {
    const list = [], tag = el.tagName.toLowerCase();
    if (el.id && !/\d{3,}|[:.]/.test(el.id)) list.push('#' + CSS.escape(el.id));
    for (const name of ['data-testid', 'data-test', 'data-qa', 'data-cy', 'aria-label', 'data-placeholder', 'placeholder', 'name']) {
      const value = el.getAttribute(name);
      if (value) list.push('[' + name + '=' + JSON.stringify(value) + ']');
    }
    for (const cls of el.classList)
      if (/^[A-Za-z][A-Za-z-]*[A-Za-z]$/.test(cls) && cls.length <= 30 && !state.test(cls)) list.push(tag + '.' + CSS.escape(cls) + '[contenteditable]');
    list.push('[contenteditable]:not([contenteditable="false"])');
    return list;
  };
  const path = (el) => {
    const parts = [];
    for (let node = el; node && node.parentElement; node = node.parentElement)
      parts.unshift(node.tagName.toLowerCase() + ':nth-child(' + ([...node.parentElement.children].indexOf(node) + 1) + ')');
    return 'html > ' + parts.join(' > ');
  };
  for (const el of document.querySelectorAll('[contenteditable]:not([contenteditable="false"])')) {
    if (out.length >= 6) break;
    if (el.tagName === 'BODY' || (el.parentElement && el.parentElement.isContentEditable)) continue;
    const role = el.getAttribute('role');
    if (role && role !== 'presentation' && role !== 'none') continue;      // the tree already shows it as that
    const box = el.getBoundingClientRect();
    if (box.width < 3 || box.height < 3) continue;
    if (el.checkVisibility ? !el.checkVisibility({ visibilityProperty: true }) : getComputedStyle(el).visibility === 'hidden') continue;
    out.push({ path: path(el), hints: hints(el) });
  }
  return out;
}
"""


def _alias(el, text):
    text = (text or "").strip()
    known = {" ".join(words(t)) for t in (el.label, el.inline, *el.aliases)}
    if text and words(text) and " ".join(words(text)) not in known:
        el.aliases.append(text[:80])


def _add_editables(page, elements):
    """Rich-text editors built on a bare contenteditable box (no role) become text boxes,
    named by their placeholder or the label beside them and reached by their place in the
    page -- in the page itself and inside its frames (an app, or its editor, embedded in one)."""
    scopes = [("", False)] + [(f"aria-ref={frame.ref} >> internal:control=enter-frame >> ", True)
                              for frame in [e for e in elements if e.role == "iframe" and e.children][:4]]
    number = 0
    for scope, framed in scopes:
        try:
            found = (page.locator(scope + "html").evaluate("(root) => (" + EDITABLE_PATHS_JS.strip() + ")()", timeout=500)
                     if framed else page.evaluate(EDITABLE_PATHS_JS))
        except Exception:
            continue
        stable = scope
        if framed and found:
            # In a test the frame is found by what identifies it (its title, name, id), not by this look's ref.
            try:
                from qm_selectors import selector_of
                stable = selector_of(page.locator(scope.split(" >> ")[0]).normalize()) + " >> internal:control=enter-frame >> "
            except Exception:
                continue
        for box in found or []:
            try:
                info = page.locator(scope + box["path"]).evaluate(DESCRIBE_JS, True, timeout=500)
            except Exception:
                continue
            number += 1
            label, source = pick_label(info)
            el = Element(ref=f"x{number}", role="textbox", selector=scope + box["path"], framed=framed,
                         hints=[stable + hint for hint in (box.get("hints") or [])],
                         inline=(info.get("text") or "")[:100], fallback=label, label_source=source if label else "")
            _alias(el, info.get("nearby"))
            elements.append(el)


def _add_frame_editors(page, elements):
    """An editor that lives in its own frame (the frame's body is the editable area) is a
    text box, called what the page calls it: the label of the field it stands in for, the
    text beside the frame, the frame's title."""
    cache = _label_cache(page)
    for frame in [e for e in elements if e.role == "iframe" and e.children][:6]:
        root, key = frame.children[0], "frame:" + frame.ref
        if key not in cache:
            try:
                editable = bool(page.locator(f"aria-ref={root.ref}").evaluate(
                    "el => !!(el.isContentEditable || (el.ownerDocument && el.ownerDocument.designMode === 'on'))", timeout=500))
                info = page.locator(f"aria-ref={frame.ref}").evaluate(DESCRIBE_JS, True, timeout=500) if editable else {}
            except Exception:
                continue
            cache[key] = [info.get("nearby"), info.get("named"), info.get("title")] if editable else None
        names = cache[key]
        if names is None:
            continue
        root.role = "textbox"
        if not root.name and not root.fallback:
            root.fallback, root.label_source = next(((n, "nearby") for n in names if n and words(n)), ("", ""))
        for name in names:
            _alias(root, name)


def _away(element):
    while element is not None:
        if element.offscreen:
            return True
        element = element.parent
    return False


_BOXES = re.compile(r" \[box=[^\]]*\]")
_METRICS_JS = ("() => { const page = document.scrollingElement || document.documentElement;"
               " return [Math.round(scrollX), Math.round(scrollY), page.scrollWidth, page.scrollHeight,"
               " innerWidth, innerHeight]; }")


def on_screen(element, viewport):
    """Is any of the element inside the window right now? (False when that is not known.)"""
    if not viewport or element is None or element.box is None or element.framed or element.offscreen:
        return False
    x, y, w, h = element.box
    return x < viewport[0] and y < viewport[1] and x + w > 0 and y + h > 0


def observe(page, timeout_ms=5000):
    """Snapshot the page (all frames), parse it and complete missing labels from the page."""
    started = time.monotonic()
    try:
        metrics = page.evaluate(_METRICS_JS)
    except Exception:
        metrics = None
    try:
        text = page.aria_snapshot(mode="ai", boxes=True, timeout=timeout_ms)
    except TypeError:                              # a Playwright without element boxes
        text, metrics = page.aria_snapshot(mode="ai", timeout=timeout_ms), None
    owners = []
    elements, page_text = parse_tree(text, owners)
    text = _BOXES.sub("", text)                    # positions are not part of what the page shows
    _add_content(elements)
    _add_plain_names(elements)
    enrich(page, elements, metrics=metrics)
    # What is said in a closed drawer or on a slide that is not showing comes after what is on show.
    away = [_away(owner) for owner in owners]
    viewport = tuple(metrics[4:6]) if metrics and len(metrics) >= 6 else None
    shown = [on_screen(owner, viewport) for owner in owners]
    order = [i for i, gone in enumerate(away) if not gone] + [i for i, gone in enumerate(away) if gone]
    page_text, shown = [page_text[i] for i in order], [shown[i] for i in order]
    _add_frame_editors(page, elements)
    _add_editables(page, elements)
    _add_context(elements)
    try:
        title = page.title()
    except Exception:
        title = ""
    return Observation(url=page.url, title=title, text=text, elements=elements,
                       page_text=page_text, ms=round((time.monotonic() - started) * 1000),
                       viewport=viewport, text_on_screen=shown)
