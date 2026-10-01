"""
QAmate — App Explorer (L1)
=============================

Crawls a Project's web app and emits an **App Model**: the pages, their
interactive elements (each with self-healing locator strategies in the exact
`smart_locator` format), and the navigation graph. This is the input the Test
Synthesizer (L3) maps PRD requirements onto, so synthesized tests are born
self-healing.

Design:
- Authenticated, same-origin **breadth-first** crawl from the project base URL,
  reusing `dom_inspector`'s element snapshot (INTERACTIVE_DOM_JS / snapshot_page)
  and URL normalization. Auth comes from the project's captured storage_state.
- **Safe by default:** navigation follows only `<a href>` links (never clicks
  arbitrary buttons that might submit/mutate), and skips destructive/auth-exit
  links (logout/delete/...). Buttons/inputs are still *catalogued* as elements
  for synthesis — we just don't navigate through them. (SPA button-routing is a
  later enhancement.)
- Depth + page caps keep crawls bounded.

Output: projects/<id>/app_model.json  (schema_version 1)

CLI:
    python app_explorer.py <project_id> [--headed] [--max-pages N] [--max-depth N]
"""

import os
import re
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
from dom_inspector import snapshot_page, _normalize_url, DESTRUCTIVE_KEYWORDS
from normalizer import escape_css_id

SCHEMA_VERSION = 1

# Links we never follow during a crawl (would log us out or mutate data).
_SKIP_LINK_KEYWORDS = tuple(set(DESTRUCTIVE_KEYWORDS) | {"logout", "signout", "sign-out", "log-out"})

# input type → ARIA role for locator generation
_INPUT_ROLE = {
    "text": "textbox", "email": "textbox", "search": "searchbox", "tel": "textbox",
    "url": "textbox", "password": "textbox", "number": "spinbutton",
    "checkbox": "checkbox", "radio": "radio", "submit": "button", "button": "button",
}
_TAG_ROLE = {"a": "link", "button": "button", "select": "combobox", "textarea": "textbox"}


def _slug(s, fallback="el"):
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return out[:50] or fallback


# Zero-width characters (zwsp/zwnj/zwj/word-joiner/BOM) — div-soup apps use these as
# placeholder "names"; they break refs, locators, and Windows cp1252 console output.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _clean(s):
    return _ZERO_WIDTH.sub("", s or "").strip()


def _label(el):
    for k in ("aria_label", "text", "label_text", "context_label", "placeholder", "name", "id", "test_id"):
        v = _clean(el.get(k))
        if v:
            return v
    return ""


def _aria_role(el):
    role = el.get("role") or ""
    if role:
        return role
    tag = el.get("tag", "")
    if tag == "input":
        return _INPUT_ROLE.get((el.get("type") or "text").lower(), "textbox")
    return _TAG_ROLE.get(tag, "")


def element_to_model(el):
    """Map a dom_inspector element dict → an app-model element with self-healing
    locator strategies (primary + fallbacks + fingerprint) in smart_locator form."""
    tag = el.get("tag", "")
    synthetic = bool(el.get("synthetic"))
    # Placeholder ranks above the context label: the browser computes the accessible
    # name from the placeholder, so role+name locators stay valid for it.
    content_name = "" if tag in {"input", "textarea", "select"} else el.get("text")
    name = _clean(el.get("aria_label") or el.get("label_text") or content_name
                  or el.get("placeholder"))
    # Nameless-control rescue: a nearby sibling label ("Shipping Address" beside a bare
    # combobox div) names the element for the agent — but it is NOT the accessible
    # name, so role+name/text locators would not match. Flag it for strategy choice.
    context_named = False
    if not name:
        ctx = _clean(el.get("context_label"))
        if ctx:
            name, context_named = ctx, True
    # Use only the first line and collapse whitespace: many controls carry a
    # count badge on a second line (e.g. a tab "New" with "100" below), which
    # makes the accessible name "New\n100" — that both breaks code generation
    # and produces a brittle locator (the count changes). Keep the label.
    name = re.split(r'\n', name)[0].strip()
    name = re.sub(r'\s+', ' ', name)
    placeholder = _clean(el.get("placeholder"))
    el_id = (el.get("id") or "").strip()
    id_css = escape_css_id(el_id) if el_id and not re.search(r"\d{3,}", el_id) else ""
    test_id = (el.get("test_id") or "").strip()
    role = _aria_role(el)
    label = _label(el)
    # An existing option must never also match a later "Create <name>" option.
    role_name = name if role == "option" else name[:80]
    exact_option = {"exact": True} if role == "option" else {}

    fallbacks = []
    if test_id:                                       # the most STABLE handle an app exposes
        primary = {"by": "test_id", "value": test_id}
        attribute = el.get("test_id_attribute", "data-testid")
        if attribute in {"data-test-id", "data-test", "data-cy"}:
            escaped = test_id.replace("\\", "\\\\").replace('"', '\\"')
            primary = {"by": "css", "value": f'[{attribute}="{escaped}"]'}
    elif synthetic and name:
        # Role-less clickable div: get_by_role would NOT match it (no ARIA semantics),
        # so target it by its visible text instead.
        primary = {"by": "text", "value": name[:80]}
    elif role and name and not context_named:
        primary = {"by": "role", "role": role, "name": role_name, **exact_option}
    elif placeholder:
        primary = {"by": "placeholder", "value": placeholder}
    elif el_id and id_css:
        primary = {"by": "css", "value": id_css}
    elif context_named and role:
        # Context-named element: ground it the way a human would — "the <role>
        # near the 'Shipping Address' label" — via Playwright's layout selector.
        # A bare get_by_role would silently grab the FIRST such role on the page.
        attr_base = f'[role="{el.get("role")}"]' if el.get("role") else (tag or "*")
        safe_label = name[:40].replace('"', "")
        primary = {"by": "css", "value": f'{attr_base}:near(:text("{safe_label}"), 150)'}
    elif name and not context_named:
        primary = {"by": "text", "value": name[:80]}
    else:
        primary = {"by": "css", "value": tag or "*"}

    if test_id and primary.get("by") != "test_id":
        fallbacks.append({"by": "test_id", "value": test_id})
    if role and name and primary.get("by") != "role" and not synthetic and not context_named:
        fallbacks.append({"by": "role", "role": role, "name": role_name, **exact_option})  # survives a test-id rename
    if name and primary.get("by") != "text" and not context_named:
        fallbacks.append({"by": "text", "value": name[:80]})
    if tag in {"a", "button"} and name:
        # Card links and aria-hidden combobox placeholders need the clickable
        # ancestor, not the child text node. Recorder verifies node identity.
        escaped_text = name[:80].replace("\\", "\\\\").replace('"', '\\"')
        fallbacks.append({"by": "css", "value": f'{tag}:has-text("{escaped_text}")'})
    if placeholder and primary.get("by") != "placeholder":
        fallbacks.append({"by": "placeholder", "value": placeholder})
    # Form field names are generally stable across React remounts; generated IDs
    # need not be. The recorder still verifies uniqueness AND node identity.
    field_name = el.get("name") or ""
    if field_name and tag in {"input", "textarea", "select"}:
        escaped_name = field_name.replace("\\", "\\\\").replace('"', '\\"')
        fallbacks.append({"by": "css", "value": f'{tag}[name="{escaped_name}"]'})
    if id_css and primary.get("value") != id_css:
        fallbacks.append({"by": "css", "value": id_css})
    if context_named and role and primary.get("by") != "role":
        fallbacks.append({"by": "role", "role": role})   # bare role — last resort, may be ambiguous
    if synthetic and name and tag:
        fallbacks.append({"by": "css", "value": f'{tag}:has-text("{name[:60]}")'})

    fingerprint = {"tag": tag, "role": role, "name": name[:80], "text": (el.get("text") or "")[:80]}

    return {
        "ref": _slug(test_id or label, tag or "el"),
        "tag": tag,
        "role": role or ("button" if synthetic else ""),
        "synthetic_role": synthetic,
        "context_named": context_named,
        "rect": el.get("rect") or {},
        "name": name[:120],
        "placeholder": placeholder,
        "input_type": (el.get("type") or ""),
        "required": bool(el.get("required")),
        "disabled": bool(el.get("disabled")),
        "readonly": bool(el.get("readonly")),
        # Comprehensive capture: keep hidden elements too (users miss these), flagged.
        "visible": el.get("visible", True),
        "hidden_reason": el.get("hidden_reason", ""),
        "broken": bool(el.get("_broken")),
        "href": (el.get("href") or "")[:100],
        "container": el.get("container") or "",
        "entity_context": el.get("entity_context") or "",
        "field_context": el.get("field_context") or "",
        "primary": primary,
        "fallbacks": fallbacks,
        "fingerprint": fingerprint,
        "test_id": test_id,
        "test_id_attribute": el.get("test_id_attribute", "data-testid"),
        "options": el.get("options", []),
        "value": bool(el.get("value")) if el.get("type") == "password" else el.get("value", ""),
        "checked": bool(el.get("checked")),
        "node_id": el.get("node_id"),
        "autocomplete": el.get("autocomplete", ""),
    }


# Comprehensive DOM capture — EVERY interactive element (visible OR hidden),
# flagged with why it's hidden, plus broken images. The deterministic
# data-collection half of "exploration".
_COMPREHENSIVE_DOM_JS = r"""() => {
    if (!window.__qamateNodes) window.__qamateNodes = {ids:new WeakMap(), next:0, document:crypto.randomUUID()};
    const nodeId = el => {
        const store = window.__qamateNodes;
        if (!store.ids.has(el)) store.ids.set(el, ++store.next);
        return store.document + ':' + store.ids.get(el);
    };
    const sels = ['button','a[href]','a','input:not([type=hidden])','select','textarea',
        '[role=button]','[role=link]','[role=tab]','[role=checkbox]','[role=radio]',
        '[role=combobox]','[role=menuitem]','[role=option]','[role=switch]','[onclick]','[contenteditable=true]'];
    const clean = (t) => (t || '').replace(/[\u200b\u200c\u200d\u2060\ufeff]/g, '').trim();
    const entityContext = el => {
        const container = el.closest('tr,[role=row],article,li,[data-test$="-item"],[data-testid$="-item"]');
        return container ? clean(container.innerText).replace(/\s+/g, ' ').slice(0, 140) : '';
    };
    // Nearby-label rescue for nameless controls (div-soup forms): the visible label
    // ("Shipping Address") is often a SIBLING text node, not an associated <label>.
    const nearLabel = (el) => {
        let cur = el;
        for (let d = 0; d < 3 && cur; d++) {
            let sib = cur.previousElementSibling, hops = 0;
            while (sib && hops < 3) {
                if (!sib.querySelector('input,select,textarea,button,[role]')) {
                    const t = clean(sib.innerText || sib.textContent);
                    if (t && t.length <= 60) return t.split('\n')[0];
                }
                sib = sib.previousElementSibling; hops++;
            }
            cur = cur.parentElement;
        }
        return '';
    };
    // Container context: same-name elements in different page regions (a sidebar
    // tab "Request Product" vs the card button "Request Product") are different
    // INTENTS — label which region each element lives in. Landmark ancestors
    // first; geometric zones as the fallback for div-soup apps with no landmarks.
    const landmark = (el) => {
        const c = el.closest('[role=dialog],dialog,nav,[role=navigation],aside,[role=tablist],[role=menu],header,footer,table,form');
        if (!c) return '';
        const r = (c.getAttribute && c.getAttribute('role')) || '';
        if (r === 'dialog' || c.tagName === 'DIALOG') return 'dialog';
        if (r === 'menu') return 'menu';
        if (r === 'navigation' || c.tagName === 'NAV') return 'nav';
        if (c.tagName === 'ASIDE') return 'sidebar';
        if (r === 'tablist') return 'tabs';
        if (c.tagName === 'HEADER') return 'header';
        if (c.tagName === 'FOOTER') return 'footer';
        if (c.tagName === 'TABLE') return 'table';
        if (c.tagName === 'FORM') return 'form';
        return '';
    };
    const zone = (r) => {
        const vw = window.innerWidth || 1280, vh = window.innerHeight || 800;
        const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
        if (cx < vw * 0.18) return 'left-rail';
        if (cy < vh * 0.12) return 'top-bar';
        return '';
    };
    const seen = new Set(); const out = [];
    // ONE comma-joined query so elements come back in TRUE document order —
    // per-selector passes bucket by tag ('button' all before '[role=button]'),
    // which breaks .nth(k) ordinals computed from snapshot order.
    let nodes; try { nodes = document.querySelectorAll(sels.join(',')); } catch (e) { nodes = []; }
    {
        for (const el of nodes) {
            if (seen.has(el)) continue; seen.add(el);
            const r = el.getBoundingClientRect(); const s = window.getComputedStyle(el);
            let reason = '';
            if (s.display === 'none') reason = 'display:none';
            else if (s.visibility === 'hidden') reason = 'visibility:hidden';
            else if (parseFloat(s.opacity || '1') === 0) reason = 'opacity:0';
            else if (r.width === 0 || r.height === 0) reason = 'zero-size';
            else if (el.getAttribute('aria-hidden') === 'true') reason = 'aria-hidden';
            const aria_label = clean(el.getAttribute('aria-label'));
            const text = clean(el.innerText || el.textContent).slice(0, 150);
            const placeholder = clean(el.getAttribute('placeholder'));
            const label_text = (el.labels && el.labels[0]) ? clean(el.labels[0].textContent).slice(0, 80) : '';
            out.push({
                tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                node_id: nodeId(el),
                autocomplete: el.getAttribute('aria-autocomplete') || (el.hasAttribute('list') ? 'list' : ''),
                text: text, aria_label: aria_label, placeholder: placeholder,
                name: el.getAttribute('name') || '', type: el.getAttribute('type') || '', id: el.id || '',
                test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
                test_id_attribute: ['data-testid','data-test-id','data-test','data-cy'].find(a => el.getAttribute(a)) || '',
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => ({label:o.label, value:o.value, disabled:o.disabled, selected:o.selected})) : [],
                value: el.value || '', checked: !!el.checked, disabled: !!el.disabled,
                readonly: !!el.readOnly,
                required: !!el.required || el.getAttribute('aria-required') === 'true',
                visible: reason === '', hidden_reason: reason,
                label_text: label_text,
                field_context: (el.matches('input,textarea,select,[role=combobox]') ? label_text || nearLabel(el) : ''),
                context_label: (aria_label || text || placeholder || label_text) ? '' : nearLabel(el),
                href: (el.tagName === 'A' ? (el.getAttribute('href') || '') : '').slice(0, 100),
                container: landmark(el) || zone(r),
                entity_context: entityContext(el),
                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
            });
        }
    }
    // Pass 2: role-less clickable elements (cursor:pointer) the semantic selectors
    // missed — div-buttons, custom dropdown triggers, clickable rows. Take only
    // BOUNDARY elements (parent is not also pointer) so a pointer container's
    // whole subtree doesn't flood in; cap to keep the percept token-light.
    let synth = 0;
    for (const el of document.querySelectorAll('div,span,li,td,p')) {
        if (synth >= 40) break;
        if (seen.has(el)) continue;
        const s = window.getComputedStyle(el);
        if (s.cursor !== 'pointer') continue;
        const p = el.parentElement;
        if (p && (seen.has(p) || window.getComputedStyle(p).cursor === 'pointer')) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0 || r.height > 120) continue;
        if (el.querySelector('button,a[href],input,select,textarea,[role]')) continue;
        const text = clean(el.innerText || el.textContent);
        if (!text || text.length > 80) continue;
        seen.add(el); synth++;
        out.push({
            tag: el.tagName.toLowerCase(), role: '', text: text.slice(0, 150),
            node_id: nodeId(el),
            aria_label: el.getAttribute('aria-label') || '', placeholder: '',
            name: '', type: '', id: el.id || '',
            test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
            test_id_attribute: ['data-testid','data-test-id','data-test','data-cy'].find(a => el.getAttribute(a)) || '',
            value: '', checked: false, disabled: false, required: false,
            visible: true, hidden_reason: '', synthetic: true, label_text: '',
            href: '', container: landmark(el) || zone(r),
            entity_context: entityContext(el),
            rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
        });
    }
    // Broken images — a common breakage signal users overlook.
    for (const img of document.querySelectorAll('img')) {
        if (img.complete && img.naturalWidth === 0 && img.getAttribute('src'))
            out.push({tag: 'img', text: '(broken image)', _broken: true,
                      name: img.getAttribute('alt') || img.getAttribute('src') || '', visible: true, hidden_reason: ''});
    }
    return out;
}"""


def _comprehensive_snapshot(page):
    try:
        return page.evaluate(_COMPREHENSIVE_DOM_JS)
    except Exception as e:
        return [{"_error": str(e)[:200]}]


# JS: collect same-origin <a href> targets on the current page (for crawling).
_LINKS_JS = r"""() => {
    const origin = location.origin;
    const out = [];
    for (const a of document.querySelectorAll('a[href]')) {
        try {
            const u = new URL(a.getAttribute('href'), location.href);
            if (u.origin !== origin) continue;
            const text = (a.innerText || a.textContent || '').trim().slice(0, 80);
            out.push({ href: u.href, text });
        } catch (e) { /* skip bad href */ }
    }
    return out;
}"""


def _should_skip_link(href, text):
    blob = f"{href} {text}".lower()
    return any(kw in blob for kw in _SKIP_LINK_KEYWORDS)


def explore(ats_root, project_id, max_pages=40, max_depth=3, headless=True, on_log=None):
    """Crawl the project's app and write app_model.json. Returns a summary dict."""
    from playwright.sync_api import sync_playwright

    def log(m):
        (on_log or (lambda s: print(s, flush=True)))(str(m))

    project = project_store.get_project(ats_root, project_id)
    if not project:
        return {"status": "error", "message": f"project '{project_id}' not found"}

    base_url = project_store.resolve_base_url(project)
    if not base_url:
        return {"status": "error", "message": "project has no base URL"}

    ss_path = project_store.storage_state_path(ats_root, project_id)
    storage_state = ss_path if os.path.exists(ss_path) else None
    log(f"[explorer] base={base_url} auth={'yes' if storage_state else 'none'} "
        f"max_pages={max_pages} max_depth={max_depth}")

    base_origin = re.match(r"(https?://[^/]+)", base_url)
    base_origin = base_origin.group(1) if base_origin else base_url

    pages = []
    visited = set()
    queue = [(base_url, 0)]
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, channel="chrome",
                                    args=["--disable-gpu", "--no-sandbox"])
        ctx_args = {"no_viewport": True} if not headless else {"viewport": {"width": 1280, "height": 720}}
        if storage_state:
            ctx_args["storage_state"] = storage_state
        context = browser.new_context(**ctx_args)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(20000)

        # ── Breakage capture: console errors, uncaught JS exceptions, and failed
        # requests, keyed by the page they happen on. These are the "errors that
        # cause breakage" surfaced during exploration. ──
        issues_by_url = {}

        def _issue(kind, text, target_url=None):
            # Focus on the APP's own breakage — drop third-party tracker/CDN/ad
            # failures and the duplicate "Failed to load resource" console spam.
            if kind == "request_failed" and target_url and base_origin and base_origin not in target_url:
                return
            if kind == "console_error" and str(text).startswith("Failed to load resource"):
                return
            try:
                issues_by_url.setdefault(_normalize_url(page.url), []).append({"type": kind, "text": str(text)[:300]})
            except Exception:
                pass
        page.on("console", lambda m: _issue("console_error", m.text) if getattr(m, "type", "") == "error" else None)
        page.on("pageerror", lambda e: _issue("js_exception", e))
        page.on("requestfailed", lambda r: _issue("request_failed", f"{getattr(r, 'method', '')} {getattr(r, 'url', '')}", getattr(r, "url", "")))

        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            norm = _normalize_url(url)
            if norm in visited:
                continue
            visited.add(norm)

            try:
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
            except Exception as e:
                errors.append({"url": url, "error": str(e)[:200]})
                log(f"[explorer] skip {url}: {str(e)[:80]}")
                continue

            title = ""
            try:
                title = page.title()
            except Exception:
                pass

            raw_elements = _comprehensive_snapshot(page)
            elements, seen_refs, hidden_count = [], {}, 0
            for el in raw_elements:
                if "_error" in el:
                    continue
                if not _label(el):
                    continue
                m = element_to_model(el)
                if not m.get("visible", True):
                    hidden_count += 1
                # de-dupe refs within a page
                ref = m["ref"]
                if ref in seen_refs:
                    seen_refs[ref] += 1
                    m["ref"] = f"{ref}-{seen_refs[ref]}"
                else:
                    seen_refs[ref] = 0
                elements.append(m)

            # Collect same-origin links to crawl
            links_out = []
            try:
                for lnk in page.evaluate(_LINKS_JS):
                    href, text = lnk.get("href", ""), lnk.get("text", "")
                    if _should_skip_link(href, text):
                        continue
                    links_out.append(href)
                    if depth + 1 <= max_depth and _normalize_url(href) not in visited:
                        queue.append((href, depth + 1))
            except Exception:
                pass

            page_issues = issues_by_url.get(norm, [])
            pages.append({
                "id": _slug(title or norm.replace(base_origin, "") or "home", "page"),
                "url": norm,
                "title": title,
                "depth": depth,
                "element_count": len(elements),
                "visible_count": len(elements) - hidden_count,
                "hidden_count": hidden_count,
                "elements": elements,
                "issues": page_issues,
                "links_out": sorted(set(_normalize_url(h) for h in links_out)),
            })
            log(f"[explorer] [{len(pages)}/{max_pages}] {norm}  "
                f"({len(elements)} elements, {hidden_count} hidden, {len(page_issues)} issue(s), depth {depth})")

        context.close()
        browser.close()

    total_issues = sum(len(p.get("issues", [])) for p in pages)
    model = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "base_url": base_url,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "page_count": len(pages),
        "element_count": sum(p["element_count"] for p in pages),
        "hidden_count": sum(p.get("hidden_count", 0) for p in pages),
        "issues_count": total_issues,
        "pages": pages,
        "errors": errors,
    }
    out_path = project_store.app_model_path(ats_root, project_id)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2)
    log(f"[explorer] done: {len(pages)} pages, {model['element_count']} elements "
        f"({model['hidden_count']} hidden), {total_issues} breakage issue(s) -> {out_path}")

    return {
        "status": "success",
        "project_id": project_id,
        "page_count": len(pages),
        "element_count": model["element_count"],
        "hidden_count": model["hidden_count"],
        "issues_count": total_issues,
        "app_model_path": out_path,
        "errors": len(errors),
    }


# ── CLI ──

def main(argv):
    if len(argv) < 2:
        print(json.dumps({"status": "error", "message": "usage: app_explorer.py <project_id> [--headed] [--max-pages N] [--max-depth N]"}))
        return 1
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_id = argv[1]
    headless = "--headed" not in argv
    max_pages, max_depth = 40, 3
    if "--max-pages" in argv:
        max_pages = int(argv[argv.index("--max-pages") + 1])
    if "--max-depth" in argv:
        max_depth = int(argv[argv.index("--max-depth") + 1])
    res = explore(ats_root, project_id, max_pages=max_pages, max_depth=max_depth,
                  headless=headless, on_log=lambda m: print(json.dumps({"event": "log", "message": m}), flush=True))
    print(json.dumps({"event": "result", **res}))
    return 0 if res.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
