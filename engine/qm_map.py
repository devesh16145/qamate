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
    def __init__(self, path=None, max_pages=80):
        self.path, self.max_pages = path, max_pages
        self.pages, self.moves = {}, []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.pages, self.moves = data.get("pages") or {}, data.get("moves") or []
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
                json.dump({"version": 1, "pages": self.pages, "moves": self.moves}, f, indent=1, ensure_ascii=False)
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
        have = set(canonical_words(" ".join(page.get("controls") or []) + " " + page["route"] + " " +
                                   " ".join(page.get("headings") or [])))
        return len(want & have) + min(page.get("visits", 0), 5) * 0.1


FIELD_ROLES = {"textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "spinbutton", "slider"}
_CONTROL = re.compile(r'^([a-z]+) "(.*)"$')
_INITIALS = re.compile(r"^[A-Z]{1,3}(?:\s|$)")   # avatar initials ("KK", "OR Owen Russel ...")


def describe_pages(page_map, limit=25):
    """The map for a person: the navigation shared by every page once, then per page its
    fields, actions and links (with where each link led)."""
    shared = page_map._everywhere()
    nav = [_CONTROL.sub(r"\2", c) for c in shared if _CONTROL.match(c)]
    lines = ["**On every page:** " + ", ".join(nav)] if nav else []
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
    return lines


def quick_scan(page, page_map, *, max_pages=12, max_seconds=20, emit=lambda e: None):
    """Learn an app's main pages before planning: follow the same-origin links in its
    navigation, and the "New ..."/"Create ..." links on the pages reached. Only plain links
    (GET navigations) are followed; nothing is clicked, typed or submitted, and links that
    look like logout/delete/export/download are skipped. Ends back on the starting page."""
    started, start_url = time.monotonic(), page.url
    origin = urlsplit(start_url)[:2]
    flow = Flow(page)
    visited, queue = set(), []

    def enqueue(observation, depth):
        here = route_of(observation.url)
        for el in observation.elements:
            if el.role != "link" or not el.url or not el.label or _UNSAFE_LINK.search(el.label):
                continue
            target = urljoin(observation.url, el.url)
            parts = urlsplit(target)
            if parts.scheme not in ("http", "https") or parts[:2] != origin or _UNSAFE_LINK.search(target):
                continue
            nav = any(r in CHROME_REGIONS for r, _ in el.regions)
            creates = "create" in canonical_words(el.label)
            if (nav and depth == 0) or creates:
                if route_of(target) not in visited and all(route_of(q[0]) != route_of(target) for q in queue):
                    queue.append((target, el.label, here, depth + 1))

    first = observe(page)
    visited.add(page_map.see(first))
    enqueue(first, 0)
    while queue and len(visited) < max_pages and time.monotonic() - started < max_seconds:
        url, label, came_from, depth = queue.pop(0)
        if route_of(url) in visited:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            flow.settle()
            obs = observe(page)
            shared = set(page_map._everywhere())
            for _ in range(3):   # a page still fetching its content: give it a moment
                if any(c not in shared for c in page_controls(obs)):
                    break
                page.wait_for_timeout(400)
                obs = observe(page)
        except Exception:
            continue
        visited.add(page_map.see(obs))
        page_map.went(came_from, label, obs.url)
        emit({"event": "log", "message": f"Learned {route_of(obs.url)}"})
        if depth < 2:
            enqueue(obs, depth)
    try:
        if page.url != start_url:
            page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
            flow.settle()
    except Exception:
        pass
    page_map.save()
    return {"pages": len(visited), "ms": round((time.monotonic() - started) * 1000)}
