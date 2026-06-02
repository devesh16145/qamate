"""
Agrim ATS — App Explorer (L1)
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


def _label(el):
    return (el.get("aria_label") or el.get("text") or el.get("placeholder")
            or el.get("name") or el.get("id") or "").strip()


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
    name = (el.get("aria_label") or el.get("text") or "").strip()
    # Use only the first line and collapse whitespace: many controls carry a
    # count badge on a second line (e.g. a tab "New" with "100" below), which
    # makes the accessible name "New\n100" — that both breaks code generation
    # and produces a brittle locator (the count changes). Keep the label.
    name = re.split(r'\n', name)[0].strip()
    name = re.sub(r'\s+', ' ', name)
    placeholder = (el.get("placeholder") or "").strip()
    el_id = (el.get("id") or "").strip()
    test_id = (el.get("test_id") or "").strip()
    role = _aria_role(el)
    label = _label(el)

    fallbacks = []
    if test_id:                                       # the most STABLE handle an app exposes
        primary = {"by": "test_id", "value": test_id}
    elif role and name:
        primary = {"by": "role", "role": role, "name": name[:80]}
    elif placeholder:
        primary = {"by": "placeholder", "value": placeholder}
    elif el_id and not re.search(r"\d{3,}", el_id):   # skip dynamic-looking ids
        primary = {"by": "css", "value": f"#{el_id}"}
    elif name:
        primary = {"by": "text", "value": name[:80]}
    else:
        primary = {"by": "css", "value": tag or "*"}

    if test_id and primary.get("by") != "test_id":
        fallbacks.append({"by": "test_id", "value": test_id})
    if role and name and primary.get("by") != "role":   # role+name survives a test-id rename
        fallbacks.append({"by": "role", "role": role, "name": name[:80]})
    if name and primary.get("by") != "text":
        fallbacks.append({"by": "text", "value": name[:80]})
    if placeholder and primary.get("by") != "placeholder":
        fallbacks.append({"by": "placeholder", "value": placeholder})
    if el_id and not re.search(r"\d{3,}", el_id) and primary.get("value") != f"#{el_id}":
        fallbacks.append({"by": "css", "value": f"#{el_id}"})

    fingerprint = {"tag": tag, "role": role, "name": name[:80], "text": (el.get("text") or "")[:80]}

    return {
        "ref": _slug(label, tag or "el"),
        "tag": tag,
        "role": role,
        "name": name[:120],
        "placeholder": placeholder,
        "input_type": (el.get("type") or ""),
        "required": bool(el.get("required")),
        "disabled": bool(el.get("disabled")),
        # Comprehensive capture: keep hidden elements too (users miss these), flagged.
        "visible": el.get("visible", True),
        "hidden_reason": el.get("hidden_reason", ""),
        "broken": bool(el.get("_broken")),
        "primary": primary,
        "fallbacks": fallbacks,
        "fingerprint": fingerprint,
        "test_id": test_id,
    }


# Comprehensive DOM capture — EVERY interactive element (visible OR hidden),
# flagged with why it's hidden, plus broken images. The deterministic
# data-collection half of "exploration".
_COMPREHENSIVE_DOM_JS = r"""() => {
    const sels = ['button','a[href]','a','input:not([type=hidden])','select','textarea',
        '[role=button]','[role=link]','[role=tab]','[role=checkbox]','[role=radio]',
        '[role=combobox]','[role=menuitem]','[role=option]','[role=switch]','[onclick]','[contenteditable=true]'];
    const seen = new Set(); const out = [];
    for (const sel of sels) {
        let nodes; try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of nodes) {
            if (seen.has(el)) continue; seen.add(el);
            const r = el.getBoundingClientRect(); const s = window.getComputedStyle(el);
            let reason = '';
            if (s.display === 'none') reason = 'display:none';
            else if (s.visibility === 'hidden') reason = 'visibility:hidden';
            else if (parseFloat(s.opacity || '1') === 0) reason = 'opacity:0';
            else if (r.width === 0 || r.height === 0) reason = 'zero-size';
            else if (el.getAttribute('aria-hidden') === 'true') reason = 'aria-hidden';
            out.push({
                tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                text: (el.innerText || el.textContent || '').trim().slice(0, 150),
                aria_label: el.getAttribute('aria-label') || '', placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '', type: el.getAttribute('type') || '', id: el.id || '',
                test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
                value: el.value || '', checked: !!el.checked, disabled: !!el.disabled,
                required: !!el.required || el.getAttribute('aria-required') === 'true',
                visible: reason === '', hidden_reason: reason,
            });
        }
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
        ctx_args = {"viewport": {"width": 1280, "height": 720}}
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
