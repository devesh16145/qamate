"""App memory for the fast explorer: the pages of an app the agent has already seen.

For each page (by route, with record ids folded: /companies/55/show -> /companies/:id/show)
it keeps the controls a user works with -- exact labels, roles -- and where clicking a
link or button led. It fills up for free while tasks run (every observation and every
navigation is recorded) and, for a fresh project, from a quick scan of the app's
navigation and its "New ..." pages.

The planner gets the part relevant to the task, so it can plan a multi-page task with
the app's real labels ("Create Company", not a guessed "Save") instead of guessing for
pages it hasn't seen -- every wrong guess costs a planner round trip.

Stored per project in page_map.json; plain JSON, safe to delete (it is rebuilt).
"""
import json
import os
import re
import time
from urllib.parse import urljoin, urlsplit

from qm_ground import canonical_words
from qm_observe import observe
from qm_runtime import Flow, relative_url

CONTROL_ROLES = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch",
                 "spinbutton", "tab", "slider", "menuitem"}
CHROME_REGIONS = {"navigation", "banner", "menu", "menubar", "tablist"}
_ID = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{20,}|[A-Za-z0-9_-]{24,})$")
_UNSAFE_LINK = re.compile(r"\b(log ?out|sign ?out|logoff|delete|remove|destroy|export|download|print|"
                          r"reset|unsubscribe|deactivate|cancel)\b", re.I)


def route_of(url):
    """A page's route with record ids folded and the query dropped (#/orders/12 -> #/orders/:id)."""
    rel = relative_url(url)
    path, _, fragment = rel.partition("#")
    fold = lambda part: "/".join(":id" if _ID.match(seg) else seg for seg in part.split("?")[0].split("/"))
    fragment = fold(fragment)
    return fold(path) + ("#" + fragment if fragment not in ("", "/") else "")   # /app/#/ is /app/


def _control_line(el):
    return f'{el.role} "{el.label[:60]}"'


def _shape(el, levels=3):
    """Where an element sits structurally: its role, depth and its ancestors' roles. The
    links of a record list share a shape even when each sits in its own wrapper."""
    chain, node = [], el.parent
    while node is not None and len(chain) < levels:
        chain.append(node.role)
        node = node.parent
    return (el.role, el.depth, tuple(chain))


def page_controls(observation, limit=45):
    """The controls of a page, compactly: long runs of look-alike links or buttons (a list
    of records, a set of filter chips) become two examples and a count. Form fields are
    always listed -- their labels are what a plan needs."""
    groups, order = {}, []
    for el in observation.elements:
        if el.role not in CONTROL_ROLES or not el.label or el.attrs.get("aria-hidden"):
            continue
        chrome = any(r in CHROME_REGIONS for r, _ in el.regions)
        key = ("one", el.ref) if chrome or el.role not in ("link", "button") else _shape(el)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(el)
    lines = []
    for key in order:
        items = groups[key]
        if len(items) >= 5:
            lines += [_control_line(e) for e in items[:2]] + [f"... +{len(items) - 2} more {items[0].role}s"]
        else:
            lines += [_control_line(e) for e in items]
    return list(dict.fromkeys(lines))[:limit]


class PageMap:
    def __init__(self, path=None, max_pages=150):
        self.path, self.max_pages = path, max_pages
        self.pages, self.moves = {}, []
        self.frontier = []   # links found but not yet visited: the next scan continues from here
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.pages, self.moves = data.get("pages") or {}, data.get("moves") or []
                self.frontier = data.get("frontier") or []
            except Exception:
                pass   # unreadable map: start over; it is only a cache

    def __len__(self):
        return len(self.pages)

    # ── learning ────────────────────────────────────────────────────────────
    def see(self, observation):
        route = route_of(observation.url)
        controls = page_controls(observation)
        if not controls:
            return route
        page = self.pages.get(route) or {"route": route, "visits": 0}
        page.update({"url": relative_url(observation.url), "title": observation.title, "controls": controls,
                     "headings": [e.label for e in observation.elements if e.role == "heading" and e.label][:6],
                     "seen": round(time.time())})
        page["visits"] += 1
        self.pages[route] = page
        if len(self.pages) > self.max_pages:   # forget the least recently seen
            oldest = min(self.pages.values(), key=lambda p: p.get("seen", 0))
            self.pages.pop(oldest["route"], None)
        return route

    def reveal(self, route, kind, label, controls):
        """Opening `label` (a menu, tab, collapsible section or dialog) on a page showed
        `controls` that are hidden until then."""
        page = self.pages.get(route)
        if page is not None and controls:
            page.setdefault("views", {})[f"{kind}: {label}"[:70]] = controls[:30]

    def went(self, from_url, label, to_url):
        """Clicking `label` on one page led to another."""
        move = {"from": route_of(from_url), "label": label[:60], "to": route_of(to_url)}
        if move["from"] != move["to"] and move not in self.moves:
            self.moves.append(move)
            self.moves = self.moves[-300:]

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "pages": self.pages, "moves": self.moves, "frontier": self.frontier[:300]},
                          f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ── for the planner ─────────────────────────────────────────────────────
    def for_task(self, task, current_url=None, budget=3500):
        """Lines describing the known pages most relevant to `task`, within `budget` chars."""
        if not self.pages:
            return []
        current = route_of(current_url) if current_url else None
        everywhere = self._everywhere()
        want = set(canonical_words(task))
        lines = ["Everywhere: " + ", ".join(everywhere)] if everywhere else []
        ranked = sorted(self.pages.values(), key=lambda p: -self._relevance(p, want))
        used = sum(len(l) for l in lines)
        for page in ranked:
            if page["route"] == current:
                continue
            leads = {m["label"]: m["to"] for m in self.moves if m["from"] == page["route"]}
            parts = []
            for c in page.get("controls") or []:
                if c in everywhere:
                    continue
                label = c.split('"', 1)[1][:-1] if '"' in c else ""
                parts.append(c + (f" -> {leads[label]}" if label in leads else ""))
            for view, controls in (page.get("views") or {}).items():   # behind a menu, tab or section
                parts.append(f"[open {view}] " + ", ".join(controls[:12]))
            line = f"{page['route']} ({page.get('title') or ''}): " + "; ".join(parts)
            if used + len(line) > budget:
                continue
            lines.append(line)
            used += len(line)
        return lines

    def _everywhere(self):
        if len(self.pages) < 2:
            return []
        counts = {}   # in order of first appearance, so the listing is stable
        for page in self.pages.values():
            for c in dict.fromkeys(page.get("controls") or []):
                counts[c] = counts.get(c, 0) + 1
        need = max(2, int(0.6 * len(self.pages) + 0.5))
        return [c for c, n in counts.items() if n >= need]

    @staticmethod
    def _relevance(page, want):
        views = " ".join(k + " " + " ".join(v) for k, v in (page.get("views") or {}).items())
        have = set(canonical_words(" ".join(page.get("controls") or []) + " " + page["route"] + " " +
                                   " ".join(page.get("headings") or []) + " " + views))
        return len(want & have) + min(page.get("visits", 0), 5) * 0.1


FIELD_ROLES = {"textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "spinbutton", "slider"}
_CONTROL = re.compile(r'^([a-z]+) "(.*)"$')
_INITIALS = re.compile(r"^[A-Z]{1,3}(?:\s|$)")   # avatar initials ("KK", "OR Owen Russel ...")


def describe_pages(page_map, limit=25):
    """The map for a person: the navigation shared by every page once, then per page its
    fields, actions and links (with where each link led)."""
    shared = page_map._everywhere()
    nav = [m.group(2) for m in map(_CONTROL.match, shared)
           if m and m.group(1) in ("link", "button", "menuitem", "tab") and len(m.group(2)) <= 30
           and not _INITIALS.match(m.group(2))]
    lines = ["**On every page:** " + ", ".join(nav[:12]) + (f" (+{len(nav) - 12})" if len(nav) > 12 else "")] if nav else []
    leads = {(m["from"], m["label"]): m["to"] for m in page_map.moves}
    for page in sorted(page_map.pages.values(), key=lambda p: p["route"])[:limit]:
        fields, actions, links, more, records = [], [], [], [], 0
        for c in page.get("controls") or []:
            if c in shared:
                continue
            match = _CONTROL.match(c)
            if not match:
                more.append(c.strip(". "))      # "... +23 more links"
                continue
            role, label = match.groups()
            if role in FIELD_ROLES:
                fields.append(label)
            elif role == "link":
                to = leads.get((page["route"], label))
                if to:
                    links.insert(sum(1 for l in links if "→" in l), f"{label} → `{to}`")   # where it goes, first
                elif len(label) <= 30 and not _INITIALS.match(label):
                    links.append(label)
                else:
                    records += 1      # a record card or avatar: data, not navigation
            elif len(label) > 40:
                records += 1          # a record card rendered as a button
            else:
                actions.append(label)
        parts = [f"{name}: " + ", ".join(items[:cap]) + (f" (+{len(items) - cap})" if len(items) > cap else "")
                 for name, items, cap in (("fields", fields, 14), ("actions", actions, 10), ("links", links, 8)) if items]
        if records:
            parts.append(f"{records} record{'s' if records != 1 else ''} (cards/rows)")
        parts += more[:2]
        lines.append(f"- `{page['route']}` — " + ("; ".join(parts) or "no controls"))
        for view, controls in (page.get("views") or {}).items():
            kind, _, label = view.partition(": ")
            shown = [_CONTROL.sub(r"\2", c) for c in controls if _CONTROL.match(c)]
            lines.append(f"  - behind **{label}** ({kind}): " + ", ".join(shown[:12]) +
                         (f" (+{len(shown) - 12})" if len(shown) > 12 else ""))
    return lines


# Controls that only open something on the same page: collapsed sections and nav groups,
# menu buttons, <details> toggles, tabs. Comboboxes/listboxes are form fields, not openers.
REVEALERS = ('[aria-expanded="false"]:not([role="combobox"]):not(select):not(input), '
             '[aria-haspopup]:not([aria-haspopup="false"]):not([aria-haspopup="listbox"]):not([role="combobox"])'
             ':not(select):not(input), summary, [role="tab"]:not([aria-selected="true"])')
_REVEALER_INFO = """el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  const popup = el.getAttribute('aria-haspopup'), role = el.getAttribute('role') || '';
  return {
    visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    submits: (el.tagName === 'BUTTON' && el.type === 'submit' && !!el.form) ||
             (el.tagName === 'INPUT' && ['submit', 'image'].includes(el.type)),
    kind: role === 'tab' ? 'tab' : (popup && popup !== 'false') ? (popup === 'dialog' ? 'dialog' : 'menu') : 'section',
    inNav: !!el.closest('nav, [role="navigation"], header, [role="banner"]'),
    label: (el.getAttribute('aria-label') || el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
  };
}"""
_UNSAFE_CLICK = re.compile(
    r"\b(delete|remove|destroy|erase|purge|archive|submit|save|send|pay|purchase|buy|checkout|confirm|approve|"
    r"reject|publish|apply|import|export|download|upload|print|reset|log ?out|sign ?out|logoff|deactivate|"
    r"unsubscribe|run|execute|start|stop|restart|sync|refresh|clear|close account)\b", re.I)
_SCAN_NOISE = {"explore", "scan", "map", "learn", "app", "site", "page", "list", "main", "user", "flow", "follow",
               "link", "form", "only", "how", "together", "more", "their", "its", "them", "with", "and", "the", "of",
               "click", "submit", "anything", "don't", "dont", "do", "not", "please", "everything", "http", "https"}
_PENDING = "() => window.__qmProbe ? window.__qmProbe.pending.size : 0"


_DOM_LINKS = """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  label: (a.getAttribute('aria-label') || a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
  href: a.href,
  nav: !!a.closest('nav, [role="navigation"], header, [role="banner"], [role="menu"], [role="menubar"]'),
  visible: !!(a.offsetWidth || a.offsetHeight || a.getClientRects().length),
})).filter(l => l.label && l.href)"""


def _links_of(page):
    """Every <a href> on the page, whatever its role (the snapshot gives addresses only to
    role=link, not to menu items or tabs that are links underneath): (label, url, in_nav, visible)."""
    try:
        return [(l["label"], l["href"], l["nav"], l["visible"]) for l in page.evaluate(_DOM_LINKS)]
    except Exception:
        return []


def quick_scan(page, page_map, *, max_pages=12, max_seconds=20, emit=lambda e: None,
               reveal=True, focus="", max_depth=2, reveals_per_page=8):
    """Learn an app's pages: follow the same-origin links in its navigation and the
    "New ..."/"Create ..." links on the pages reached, and -- with `reveal` -- open each
    page's menus, tabs and collapsible sections to record what they hide (following any
    links they reveal). Links are opened by address (plain GET); the only clicks are on
    controls the page itself marks as openers (aria-expanded/aria-haspopup, <summary>,
    tabs), never on submit buttons or anything labelled delete/save/export/run/...;
    nothing is typed. Links that look like logout/delete/export/download are skipped.
    Continues from where an earlier scan stopped (the map's frontier); links related to
    `focus` go first. Ends back on the starting page."""
    started, start_url = time.monotonic(), page.url
    origin = urlsplit(start_url)[:2]
    # Stay inside the app: same origin AND under the start page's folder (an app served at
    # /crm/ doesn't wander into /docs/ or another app on the same host).
    scope = urlsplit(start_url).path or "/"
    scope = scope if scope.endswith("/") else scope.rsplit("/", 1)[0] + "/"
    flow = Flow(page)
    deadline = started + max_seconds
    want = set(canonical_words(focus)) - _SCAN_NOISE
    start_route = route_of(start_url)
    visited = {r for r in page_map.pages if r != start_route}     # known pages: continue past them
    queue = [tuple(q) for q in page_map.frontier if route_of(q[0]) not in visited]
    stats = {"new": 0, "views": 0}

    def enqueue(links, here, depth, revealed=False):
        for label, target, nav, _visible in links:
            if _UNSAFE_LINK.search(label) or _UNSAFE_LINK.search(target):
                continue
            parts = urlsplit(target)
            if parts.scheme not in ("http", "https") or parts[:2] != origin or not (parts.path or "/").startswith(scope):
                continue
            if not ((nav and depth == 0) or revealed or "create" in canonical_words(label)):
                continue
            route = route_of(target)
            if route not in visited and all(route_of(q[0]) != route for q in queue):
                queue.append((target, label, here, depth + 1))
        if want:   # what the request is about first, then breadth
            queue.sort(key=lambda q: (-len(want & set(canonical_words(q[1] + " " + q[0]))), q[3]))

    def wait_for_data():
        flow.settle()
        end = min(deadline, time.monotonic() + 5)
        while time.monotonic() < end:   # slow internal APIs: give in-flight requests a few seconds
            try:
                if not page.evaluate(_PENDING):
                    break
            except Exception:
                break
            page.wait_for_timeout(250)
        flow.settle()

    def open_views(here, depth):
        """Open the page's menus, tabs and sections one at a time; record what each shows."""
        try:
            handles = page.locator(REVEALERS).element_handles()
        except Exception:
            return
        base = observe(page)
        shown = {(l[0], l[1]) for l in _links_of(page) if l[3]}
        opened, tried = 0, set()
        for handle in handles:
            if opened >= reveals_per_page or time.monotonic() > deadline:
                break
            try:
                info = handle.evaluate(_REVEALER_INFO)
            except Exception:
                continue   # gone after an earlier click
            label = info["label"] or "menu"
            if (not info["visible"] or info["disabled"] or info["submits"] or label in tried
                    or _UNSAFE_CLICK.search(label) or (info["inNav"] and depth > 0)):
                continue   # shared navigation menus are opened once, on the first page
            tried.add(label)   # row menus repeat ("Actions" x25): one is enough
            before_url = page.url
            try:
                handle.click(timeout=3000)
                wait_for_data()
            except Exception:
                continue
            opened += 1
            if route_of(page.url) != here:          # it navigated: that's a page, not a view
                obs = observe(page)
                in_scope = urlsplit(obs.url)[:2] == origin and (urlsplit(obs.url).path or "/").startswith(scope)
                if in_scope and route_of(obs.url) not in visited:
                    visited.add(page_map.see(obs))
                    stats["new"] += 1
                page_map.went(before_url, label, obs.url)
                try:
                    page.goto(before_url, wait_until="domcontentloaded", timeout=15000)
                    wait_for_data()
                except Exception:
                    return
                base = observe(page)
                shown = {(l[0], l[1]) for l in _links_of(page) if l[3]}
                continue
            obs = observe(page)
            seen = set(page_controls(base, limit=300))
            new = [c for c in page_controls(obs, limit=300) if c not in seen]
            if new:
                page_map.reveal(here, info["kind"], label, new)
                stats["views"] += 1
            if depth < max_depth:   # links that this opened up (now visible, before hidden or absent)
                enqueue([l for l in _links_of(page) if l[3] and (l[0], l[1]) not in shown], here, depth, revealed=True)
            if info["kind"] in ("menu", "dialog") or any(e.role in ("dialog", "alertdialog") for e in obs.elements):
                page.keyboard.press("Escape")       # close menus and dialogs without choosing anything
                flow.settle()
                try:
                    if handle.get_attribute("aria-expanded") == "true":   # still open: toggle it shut
                        handle.click(timeout=2000)
                        flow.settle()
                except Exception:
                    pass
            base = observe(page)
            shown = {(l[0], l[1]) for l in _links_of(page) if l[3]}

    first = observe(page)
    page_map.see(first)
    visited.add(start_route)
    enqueue(_links_of(page), start_route, 0)
    if reveal:
        open_views(start_route, 0)
    while queue and len(visited) < max_pages and time.monotonic() < deadline:
        url, label, came_from, depth = queue.pop(0)
        if route_of(url) in visited:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            wait_for_data()
            obs = observe(page)
            shared = set(page_map._everywhere())
            for _ in range(3):   # a page still rendering its content: give it a moment
                if any(c not in shared for c in page_controls(obs)):
                    break
                page.wait_for_timeout(400)
                obs = observe(page)
        except Exception:
            continue
        here = page_map.see(obs)
        visited.add(here)
        stats["new"] += 1
        page_map.went(came_from, label, obs.url)
        emit({"event": "log", "message": f"Learned {here}"})
        if depth < max_depth:
            enqueue(_links_of(page), here, depth)
        if reveal and time.monotonic() < deadline:
            open_views(here, depth)
    page_map.frontier = [list(q) for q in queue if route_of(q[0]) not in visited][:300]
    try:
        if page.url != start_url:
            page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
            flow.settle()
    except Exception:
        pass
    page_map.save()
    return {"pages": len(page_map), "new": stats["new"], "views": stats["views"],
            "queued": len(page_map.frontier), "ms": round((time.monotonic() - started) * 1000)}
