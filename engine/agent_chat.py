"""
Agrim ATS — Conversational Agent (Pydantic AI)
==============================================

The autonomous explorer/test-author turned into a real **agent you can chat
with**: it keeps conversation memory, calls tools, drives a live browser, and
iterates with you — the Claude-Code loop, embedded in the product.

This replaces the fire-and-forget loop in `agent_recorder.py` (one LLM call =
one action, no memory, no chat) with a persistent Pydantic AI `Agent` whose
tools wrap the engine functions we already have:

    navigate / inspect_page / click / fill / select_option   -> drive the app
        (dom_inspector comprehensive snapshot + smart_locator, both reused)
    add_checkpoint / get_recorded_flow / create_test_case     -> author a test
        (recorder_parser.generate_from_review, reused as-is)

Nothing in the test pipeline is rebuilt — a test the agent authors is an
ordinary, runnable, self-healing test case, identical to a hand-recorded one.

── Why async + a pinned browser thread ──────────────────────────────────────
Pydantic AI runs *sync* tool functions on rotating anyio worker threads
(ephemeral). Playwright's **sync** API binds its objects to the thread that
created them, so it must never hop threads. We therefore:
  * run the agent on an asyncio loop (main thread),
  * own a single-worker ThreadPoolExecutor (`_BROWSER`) and create + use ALL
    Playwright objects only on that one thread,
  * make every tool `async` and marshal the actual browser work onto `_BROWSER`
    via `run_in_executor`.
This lets us reuse the existing **sync** snapshot/smart_locator/codegen code
unchanged while keeping Playwright single-threaded and loop-free.

── IPC (newline-delimited JSON, same transport as runner.py) ────────────────
Electron -> stdin:
    {"action":"init","project_id":?,"env":?,"provider":?,"headed":bool,"start_path":?}
    {"action":"chat","message":"..."}
    {"action":"reset"}            # clear conversation memory (keep the browser)
    {"action":"shutdown"}
Python -> stdout:
    {"event":"ready", url,title,provider,model,auth,project}
    {"event":"thinking","delta":...}        # reasoning tokens (MiMo)
    {"event":"text","delta":...}            # assistant tokens (streamed)
    {"event":"tool_call","tool":...,"args":{...}}
    {"event":"tool_result","tool":...,"summary":...}
    {"event":"turn_complete","text": <full assistant message>}
    {"event":"log","message":...} / {"event":"error","message":...}

CLI:
    python agent_chat.py --selftest     # offline wiring check (TestModel, no browser)
"""

import os
import re
import sys
import json
import asyncio
import datetime
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
import agent_sessions
from app_explorer import _comprehensive_snapshot, element_to_model, _label, _LINKS_JS, _should_skip_link
from dom_inspector import DESTRUCTIVE_KEYWORDS, _normalize_url
import llm as _llm
from smart_locator import smart_locator, SelfHealError, to_locator
from recorder_parser import generate_from_review
from agent_recorder import _locator_str, _q  # reuse the proven codegen helpers

from pydantic_ai import Agent, RunContext, capture_run_messages, BinaryContent, ToolReturn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

# Max tool calls per chat turn — a bound on runaway loops AND on turn duration
# (each call is a model round-trip + a browser action). When hit, we preserve the
# full history and summarize findings so the user can say "continue". Override
# with ATS_AGENT_TOOL_BUDGET.
TOOL_BUDGET = int(os.environ.get("ATS_AGENT_TOOL_BUDGET") or 30)

# Per-request OUTPUT cap (max_tokens) — NOT the context window. A model's context length (MiMo's
# ~1M) is how much it can READ; max_tokens only bounds how much it WRITES per turn. The old fixed
# 4096 truncated reasoning models mid-thought. Default 0 = DO NOT cap output: omit max_tokens so the
# model uses its own (large) output limit. Set ATS_AGENT_MAX_TOKENS or a per-provider "max_tokens"
# in config.json to impose an explicit ceiling (e.g. for cost/latency control).
AGENT_MAX_TOKENS = int(os.environ.get("ATS_AGENT_MAX_TOKENS") or 0)


# ── stdout: JSON lines, ASCII-only (the Electron runner pipes through a cp1252
# console — non-ASCII crashes it; ensure_ascii=True escapes everything safely) ─
_OUT_LOCK = threading.Lock()


def emit(obj):
    with _OUT_LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def log(msg):
    emit({"event": "log", "message": str(msg)})


def _read_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _trim_dangling_tool_calls(messages):
    """Drop trailing model responses whose tool calls were never executed (the run
    stopped on the tool-call budget mid-request). Without this the history ends with
    an 'unprocessed tool call' and pydantic-ai rejects the next user prompt."""
    msgs = list(messages)
    returned = set()
    for m in msgs:
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart):
                returned.add(p.tool_call_id)
    while msgs:
        last = msgs[-1]
        if isinstance(last, ModelResponse) and any(
                isinstance(p, ToolCallPart) and p.tool_call_id not in returned
                for p in getattr(last, "parts", [])):
            msgs.pop()
        else:
            break
    return msgs


# ── Attachments & context files: turn docs/images into prompt content ─────────
# Documents are EXTRACTED TO TEXT (works on every model, incl. text-only ones);
# images are passed as multimodal BinaryContent (works on vision-capable models —
# GPT-4o / Claude, and MiMo if mimo-v2.5-pro accepts images; if it does not, switch
# the model tag to the omnimodal mimo-v2.5). Every helper is wrapped so a bad file
# never crashes a turn — the worst case is a "could not read" note.

TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".log",
             ".yml", ".yaml", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".jsx",
             ".tsx", ".py", ".java", ".c", ".cpp", ".cs", ".go", ".rb", ".php", ".sh",
             ".bat", ".sql", ".ini", ".toml", ".env", ".properties", ".conf"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

MAX_TEXT_PER_FILE = 200_000     # chars of extracted text kept per document
MAX_TEXT_TOTAL = 500_000        # chars across all attachments in one message
MAX_IMAGE_BYTES = 12_000_000    # ~12 MB cap per image
MAX_PDF_BYTES = 20_000_000      # ~20 MB cap when sending a PDF as a binary doc


# ── Scoped project folder (the agent EXPLORES it like a repo — it is NOT imported) ─────
# A context folder may be a whole project tree, so listing/searching must hide the usual
# junk and never escape the folder. _is_junk_name is MIRRORED in main.js `_isJunkName`
# (the UI browser + @-search use the same convention) — keep them in sync.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".idea", ".vscode",
             "dist", "build", ".next", ".cache", ".pytest_cache", ".mypy_cache", ".gradle",
             "target", ".tox", "coverage", ".turbo", ".parcel-cache", "obj"}
MAX_SCAN_BYTES = 2_000_000      # files larger than this are skipped from listing/search


def _is_junk_name(name):
    """True for files/dirs to hide by default when exploring a scoped folder: VCS/deps/caches
    and any dot-entry (.git, .claude, .minimax, .DS_Store, ...)."""
    return (not name) or name in SKIP_DIRS or name.startswith(".")


def _scoped_path(base, rel):
    """Resolve `rel` inside `base`; returns the absolute path, or None if it escapes the folder."""
    base_n = os.path.normpath(base or "")
    if not base_n:
        return None
    safe = os.path.normpath(os.path.join(base_n, rel or ""))
    try:
        if safe == base_n or os.path.commonpath([safe, base_n]) == base_n:
            return safe
    except Exception:
        pass
    return None


def _search_context(base, query, content=True, max_hits=40):
    """Filename + (optional) content grep over a scoped folder, junk/big/binary skipped."""
    ql = (query or "").lower()
    names, hits = [], []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not _is_junk_name(d)]   # prune junk dirs in place
        for fn in sorted(files):
            if _is_junk_name(fn):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, base).replace("\\", "/")
            if ql in fn.lower() and len(names) < max_hits:
                names.append(rel)
            if content and len(hits) < max_hits:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                try:
                    if os.path.getsize(full) > MAX_SCAN_BYTES:
                        continue
                    per_file = 0
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if ql in line.lower():
                                hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
                                per_file += 1
                                if per_file >= 3 or len(hits) >= max_hits:
                                    break
                except Exception:
                    pass
        if len(names) >= max_hits and len(hits) >= max_hits:
            break
    return {"ok": True, "query": query, "name_matches": names, "content_matches": hits}


def _read_text_file(path, limit=MAX_TEXT_PER_FILE):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read(limit + 1)
    return data[:limit], len(data) > limit


def _extract_pdf(path, limit=MAX_TEXT_PER_FILE):
    try:
        from pypdf import PdfReader
    except Exception:
        return None
    try:
        reader = PdfReader(path)
        out, total = [], 0
        for page in reader.pages:
            t = page.extract_text() or ""
            out.append(t)
            total += len(t)
            if total >= limit:
                break
        return "\n".join(out)[:limit].strip()
    except Exception:
        return None


def _extract_docx(path, limit=MAX_TEXT_PER_FILE):
    try:
        import docx  # python-docx
    except Exception:
        return None
    try:
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)[:limit].strip()
    except Exception:
        return None


def _extract_xlsx(path, limit=MAX_TEXT_PER_FILE, max_rows=200):
    try:
        import openpyxl
    except Exception:
        return None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"# Sheet: {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= max_rows:
                    out.append("... (truncated)")
                    break
                out.append(",".join("" if c is None else str(c) for c in row))
            if sum(len(x) for x in out) >= limit:
                break
        wb.close()
        return "\n".join(out)[:limit].strip()
    except Exception:
        return None


def extract_file_text(path):
    """Extract readable text from a document. Returns (text, truncated). text is None
    when the file is not text-extractable (an image, or an unsupported/locked binary)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            return _read_text_file(path)
        if ext == ".pdf":
            return (_extract_pdf(path) or None), False
        if ext == ".docx":
            return (_extract_docx(path) or None), False
        if ext in (".xlsx", ".xlsm"):
            return (_extract_xlsx(path) or None), False
    except Exception:
        return None, False
    return None, False


def _binary_part(path, mime, max_bytes=MAX_IMAGE_BYTES):
    """Load a file as a multimodal BinaryContent part (None if missing / too big)."""
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as f:
            return BinaryContent(data=f.read(), media_type=mime)
    except Exception:
        return None


def _attachment_paths(attachments):
    """Normalize the IPC 'attachments' field (list of paths or {path,name}) to
    [(abs_path, display_name), ...]."""
    out = []
    for a in attachments or []:
        p = a if isinstance(a, str) else (a.get("path") if isinstance(a, dict) else "")
        if p:
            name = (a.get("name") if isinstance(a, dict) and a.get("name") else os.path.basename(p))
            out.append((p, name))
    return out


def _build_user_prompt(message, attachments):
    """Assemble the prompt for agent.run(). Returns a plain str when there are no
    binary (image/PDF) parts, else a list [text, BinaryContent, ...] — the shape
    pydantic-ai accepts for multimodal input. Docs are inlined as labeled text blocks;
    images/scanned-PDFs are appended as binary parts."""
    pairs = _attachment_paths(attachments)
    if not pairs:
        return message
    text_blocks, parts, notes, used = [], [], [], 0
    for path, name in pairs:
        if not os.path.exists(path):
            notes.append(f"[not found: {name}]")
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_MIME:
            bc = _binary_part(path, IMAGE_MIME[ext])
            notes.append(f"[image: {name}]" if bc else f"[image '{name}' unreadable or >12MB]")
            if bc:
                parts.append(bc)
            continue
        text, truncated = extract_file_text(path)
        if text:
            budget = max(0, MAX_TEXT_TOTAL - used)
            chunk = text[:budget]
            used += len(chunk)
            suffix = " (truncated)" if (truncated or len(chunk) < len(text)) else ""
            text_blocks.append(f"--- Attached file: {name}{suffix} ---\n{chunk}\n--- end of {name} ---")
            notes.append(f"[doc: {name}]")
        elif ext == ".pdf":
            bc = _binary_part(path, "application/pdf", max_bytes=MAX_PDF_BYTES)
            notes.append(f"[pdf: {name}]" if bc else f"[pdf '{name}' unreadable or >20MB]")
            if bc:
                parts.append(bc)
        else:
            notes.append(f"[unsupported, not sent: {name}]")
    body = message or ""
    if notes:
        body = (body + "\n\n" if body else "") + "Attachments: " + "; ".join(notes)
    if text_blocks:
        body += "\n\n" + "\n\n".join(text_blocks)
    return ([body] + parts) if parts else body


# ── The single thread that owns ALL Playwright objects ───────────────────────
_BROWSER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw")


async def _bro(fn, *args, **kwargs):
    """Run a (sync) Playwright operation on the pinned browser thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_BROWSER, lambda: fn(*args, **kwargs))


# ── Live browser session (every method runs ON the browser thread) ───────────

def _is_closed_error(e):
    """True if an exception means the page/context/browser died (recoverable by restart)."""
    s = str(e).lower()
    return any(k in s for k in (
        "has been closed", "target page", "target closed", "browser has been closed",
        "browser has been disconnected", "crashed", "websocket"))


def _pytest_summary(text):
    """Pull the final pytest summary line (e.g. '1 passed in 2.3s' / '1 failed, ...') from output."""
    hits = re.findall(r"=+\s*(.*?(?:passed|failed|error|skipped|no tests ran).*?)\s*=+", text, re.IGNORECASE)
    if hits:
        return hits[-1].strip()[:200]
    for line in reversed(text.strip().splitlines()):
        low = line.lower()
        if any(k in low for k in ("passed", "failed", "error", "skipped", "no tests ran")):
            return line.strip()[:200]
    return ""


def _find_failure_artifacts(results_dir):
    """Locate the FAILED.png screenshot + captured ERRORS.txt from a verify run's results dir
    (conftest's capture_screenshot_on_failure writes both). Returns (screenshot_path|None, err_text|None)."""
    import glob
    shot, err = None, None
    sdir = os.path.join(results_dir or "", "screenshots")
    try:
        pngs = sorted(glob.glob(os.path.join(sdir, "*_FAILED.png")), key=os.path.getmtime, reverse=True)
        if pngs:
            shot = pngs[0]
        txts = sorted(glob.glob(os.path.join(sdir, "*_ERRORS.txt")), key=os.path.getmtime, reverse=True)
        if txts:
            with open(txts[0], "r", encoding="utf-8", errors="replace") as f:
                err = f.read()
    except Exception:
        pass
    return shot, err


# Values that look DYNAMIC (ids / dates / times / order numbers / uuids) — asserting on these
# makes a test fail the next run; the lint nudges the agent to assert on stable structure instead.
_DYNAMIC_RE = re.compile(r"(\b\d{4,}\b|\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}(:\d{2})?\b|[0-9a-fA-F]{8}-[0-9a-fA-F]{4})")


def _lint_recording(steps, assertions):
    """Quality warnings on a recorded flow, surfaced to the agent so it can improve BEFORE
    delivering — the cheap half of one-shot accuracy (the verify loop is the other half)."""
    warns = []
    checks = assertions or []
    if not checks:
        warns.append("No verification checkpoints — the test only clicks through without asserting any "
                     "outcome. Add at least one add_checkpoint at a meaningful result so it can actually fail.")
    for a in checks:
        val = str(a.get("value") or "")
        if val and _DYNAMIC_RE.search(val):
            warns.append(f"Checkpoint asserts on '{val[:40]}', which looks DYNAMIC (id/date/number) and will "
                         f"likely differ next run -> assert on stable text/structure instead (a heading, label, "
                         f"status, or that a row simply exists).")
    return warns


def _delete_test_case(ats_root, flow_id, tc_id):
    """Remove a test case from a flow: its test_cases.json entry, its test_data.json key, and the
    @pytest.mark.tc/def block in test_<flow>.py. Mirrors main.js's delete-test handler."""
    flow_dir = os.path.join(ats_root, "tests", "flows", flow_id)
    if not os.path.isdir(flow_dir):
        return {"ok": False, "error": f"flow '{flow_id}' not found"}
    removed, found = [], False
    tc_file = os.path.join(flow_dir, "test_cases.json")
    if os.path.isfile(tc_file):
        tcs = _read_json_file(tc_file, [])
        if isinstance(tcs, list):
            kept = [t for t in tcs if not (isinstance(t, dict) and t.get("tc_id") == tc_id)]
            if len(kept) != len(tcs):
                found = True
                with open(tc_file, "w", encoding="utf-8") as f:
                    json.dump(kept, f, indent=4)
                removed.append("test_cases.json")
    data_file = os.path.join(flow_dir, "test_data.json")
    if os.path.isfile(data_file):
        data = _read_json_file(data_file, {})
        if isinstance(data, dict) and tc_id in data:
            del data[tc_id]
            with open(data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            removed.append("test_data.json")
    func = "test_" + tc_id.replace("-", "_")
    pat = re.compile(r'(?:^|\n)(@pytest\.mark\.tc\("' + re.escape(tc_id) + r'"\)[\s\S]*?def '
                     + re.escape(func) + r'[\s\S]*?(?=\n@pytest|\n\nclass |\n\ndef [a-z]|\Z))', re.M)
    try:
        pyfiles = [fn for fn in os.listdir(flow_dir) if fn.startswith("test_") and fn.endswith(".py")]
    except Exception:
        pyfiles = []
    for fn in pyfiles:
        p = os.path.join(flow_dir, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        m = pat.search(content)
        if m:
            new_content = re.sub(r"\n{3,}", "\n\n", content.replace(m.group(0), ""))
            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)
            removed.append(fn)
            found = True
    if not found and not removed:
        return {"ok": False, "error": f"test case '{tc_id}' not found in flow '{flow_id}'"}
    return {"ok": True, "tc_id": tc_id, "flow": flow_id, "removed": removed}


class BrowserSession:
    """Holds the persistent page the agent drives, the ref->element map from the
    last inspect, the recorded steps/checkpoints for test authoring, and any
    breakage captured along the way."""

    def __init__(self):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.by_ref = {}        # ref -> element model (from last inspect)
        self.steps = []         # recorded steps -> generate_from_review
        self.assertions = []    # recorded checkpoints
        self.step_id = 0
        self.input_counter = 0
        self.issues = []        # breakage: console errors / JS exceptions / failed requests
        self._start_args = None  # remembered so restart() can relaunch after a crash/close

    # -- lifecycle -----------------------------------------------------------
    def start(self, start_url="", storage_state=None, headless=True, credentials=None,
              save_state_path=None, record_first_step=True):
        """Launch the browser, reuse a saved login if present, and — if we still
        land on a login form — log in ONCE from configured credentials and persist
        a fresh storage_state for next time (so the agent never has to log in by
        hand). Returns {url, title, authed} where authed is
        'storage_state' | 'login' | 'session' | 'none'."""
        # Remember how we were started so restart() can relaunch identically after a crash.
        self._start_args = dict(start_url=start_url, storage_state=storage_state,
                                headless=headless, credentials=credentials,
                                save_state_path=save_state_path)
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=headless, channel="chrome", args=["--disable-gpu", "--no-sandbox"])
        ctx_args = {"viewport": {"width": 1280, "height": 720}}
        if storage_state:
            ctx_args["storage_state"] = storage_state
        self.context = self.browser.new_context(**ctx_args)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.set_default_navigation_timeout(20000)
        self.page.on("console", lambda m: self._issue("console_error", m.text)
                     if getattr(m, "type", "") == "error" else None)
        self.page.on("pageerror", lambda e: self._issue("js_exception", e))
        self.page.on("requestfailed", lambda r: self._issue(
            "request_failed", f"{getattr(r, 'method', '')} {getattr(r, 'url', '')}"))

        authed = "none"
        if start_url:
            self._goto(start_url)
            if self._on_login_page():
                email, password = credentials or (None, None)
                if email and password:
                    log(f"[agent] not authenticated -> logging in as {email} ...")
                    if self._attempt_login(email, password):
                        authed = "login"
                        if save_state_path:
                            try:
                                os.makedirs(os.path.dirname(save_state_path), exist_ok=True)
                                self.context.storage_state(path=save_state_path)
                                log("[agent] login OK -> saved session (will be reused next time)")
                            except Exception as e:
                                log(f"[agent] login OK but could not save session: {e}")
                    else:
                        log("[agent] auto-login did not succeed (check credentials in settings, or capture a login)")
                else:
                    log("[agent] not authenticated and no credentials configured -> set them in the project/settings")
            else:
                authed = "storage_state" if storage_state else "session"
            # Record the FIRST step at the post-login landing page (never /login).
            # Skipped on restart() so a mid-recording crash recovery doesn't add a stray step.
            if record_first_step:
                self._record_navigate(self.page.url)
        return {"url": self._url(), "title": self._title(), "authed": authed}

    def close(self):
        for fn in (lambda: self.context and self.context.close(),
                   lambda: self.browser and self.browser.close(),
                   lambda: self.pw and self.pw.stop()):
            try:
                fn()
            except Exception:
                pass

    def _alive(self):
        """True if there is a live, connected page to drive."""
        try:
            return (bool(self.page) and not self.page.is_closed()
                    and bool(self.browser) and self.browser.is_connected())
        except Exception:
            return False

    def restart(self):
        """Relaunch the browser with the original start args (after the window was closed or
        the browser crashed). Returns {ok, url, title, authed} or an error dict."""
        if not self._start_args:
            return {"ok": False, "error": "browser was never started; cannot restart"}
        try:
            self.close()
        except Exception:
            pass
        self.pw = self.browser = self.context = self.page = None
        self.by_ref = {}  # refs from the old page are stale after a relaunch
        try:
            info = self.start(**self._start_args, record_first_step=False)
        except Exception as e:
            return {"ok": False, "error": f"restart failed: {str(e)[:160]}"}
        return {"ok": True, **info}

    def _ensure_alive(self):
        """Restart the browser if it has died. Returns True if a live page is available."""
        if self._alive():
            return True
        if not self._start_args:
            return False
        self.restart()
        return self._alive()

    # -- internals -----------------------------------------------------------
    def _issue(self, kind, text):
        try:
            self.issues.append({"type": kind, "text": str(text)[:280], "url": self.page.url})
        except Exception:
            pass

    def _url(self):
        try:
            return self.page.url
        except Exception:
            return ""

    def _title(self):
        try:
            return self.page.title()
        except Exception:
            return ""

    def _goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    def _record_navigate(self, url):
        # Skip a duplicate consecutive navigate to the same URL (e.g. the start_url
        # auto-step followed by the agent navigating to that same login page).
        if self.steps and self.steps[-1].get("type") == "navigate" and self.steps[-1].get("target") == url:
            return
        self.step_id += 1
        self.steps.append({"id": self.step_id, "rawLine": f'page.goto("{_q(url)}")',
                           "type": "navigate", "target": url,
                           "targetDescription": f"Navigate to {url}", "value": "", "varName": ""})

    def _settle(self):
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

    def _on_login_page(self):
        """A visible password field is the most reliable 'not logged in' signal."""
        try:
            if self.page.locator('input[type="password"]').count() > 0:
                return True
        except Exception:
            pass
        return "login" in (self._url() or "").lower()

    def _attempt_login(self, email, password):
        """One-shot heuristic login (mirrors conftest._do_platform_login). Returns
        True if we are no longer on a login page afterwards."""
        page = self.page
        email_field = page.locator(
            'input[name="username"], input[type="email"], input[placeholder*="Email"], input[placeholder*="email"]'
        ).first
        password_field = page.locator('input[type="password"]').first
        submit_btn = page.locator(
            'button[type="submit"], button:has-text("Sign In"), button:has-text("Sign in"), '
            'button:has-text("Log In"), button:has-text("Login")'
        ).first
        try:
            email_field.wait_for(state="visible", timeout=15000)
            email_field.fill(email)
            password_field.fill(password)
            submit_btn.click()
        except Exception as e:
            log(f"[agent] login form interaction failed: {str(e)[:140]}")
            return False
        self.page.wait_for_timeout(5000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        return not self._on_login_page()

    # -- tool bodies ---------------------------------------------------------
    def navigate(self, url):
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed and could not be restarted; call restart_browser"}
        before = len(self.issues)
        try:
            self._goto(url)
        except Exception as e:
            # Auto-recover once if the browser died mid-navigation.
            if _is_closed_error(e) and self._ensure_alive():
                try:
                    self._goto(url)
                except Exception as e2:
                    return {"ok": False, "error": f"navigation failed: {str(e2)[:160]}", "url": self._url()}
            else:
                return {"ok": False, "error": f"navigation failed: {str(e)[:160]}", "url": self._url()}
        self._record_navigate(url)
        return {"ok": True, "url": self._url(), "title": self._title(),
                "new_issues": self.issues[before:]}

    def inspect(self, limit=40, include_hidden=False):
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed and could not be restarted; call restart_browser"}
        try:
            raw = _comprehensive_snapshot(self.page)
        except Exception as e:
            if _is_closed_error(e) and self._ensure_alive():
                raw = _comprehensive_snapshot(self.page)
            else:
                return {"ok": False, "error": f"inspect failed: {str(e)[:160]}"}
        models, seen = [], {}
        for el in raw:
            if not isinstance(el, dict):
                continue
            if "_error" in el:
                return {"ok": False, "error": el["_error"]}
            if not _label(el):
                continue
            m = element_to_model(el)
            ref = m["ref"]
            if ref in seen:
                seen[ref] += 1
                m["ref"] = f"{ref}-{seen[ref]}"
            else:
                seen[ref] = 0
            models.append(m)
        self.by_ref = {m["ref"]: m for m in models}

        compact, hidden_total = [], 0
        for m in models:
            if not m.get("visible", True):
                hidden_total += 1
                if not include_hidden:
                    continue
            item = {"ref": m["ref"], "role": m.get("role") or m.get("tag"),
                    "name": (m.get("name") or "")[:60], "type": m.get("input_type") or ""}
            if not m.get("visible", True):
                item["hidden_reason"] = m.get("hidden_reason", "")
            if m.get("disabled"):
                item["disabled"] = True
            if m.get("broken"):
                item["broken"] = True
            compact.append(item)
            if len(compact) >= limit:
                break
        return {"ok": True, "url": self._url(), "title": self._title(),
                "element_count": len(models), "hidden_count": hidden_total,
                "elements": compact, "issues": self.issues[-8:]}

    def _record_strategy(self, el):
        """Pick the locator to RECORD so the generated test hits exactly the intended element on a
        COLD run. If the primary matches >1 element it is ambiguous (SmartLocator would silently take
        .first) — upgrade to the element's test-id when that's unique, else keep it but warn the agent.
        Returns (strategy, locator_str, warning|None). Never raises (record-time best effort)."""
        prim = el.get("primary") or {}
        def _count(strat):
            try:
                loc = to_locator(self.page, strat)
                return loc.count() if loc is not None else 0
            except Exception:
                return -1
        n = _count(prim)
        if n < 2:                                    # unique (1), none yet (0), or uncountable (-1) -> keep
            return prim, _locator_str(prim), None
        tid = (el.get("test_id") or "").strip()      # ambiguous (>=2) -> try the element's test-id
        if tid:
            ts = {"by": "test_id", "value": tid}
            if _count(ts) == 1:
                return ts, _locator_str(ts), None
        warn = (f"This locator matches {n} elements on the page, so the test will act on the FIRST "
                f"one. If that is not the element you meant, pick a more uniquely identifiable element "
                f"(distinct text/label, or one exposing a test id).")
        return prim, _locator_str(prim), warn

    def act(self, kind, ref, value=None):
        el = self.by_ref.get(ref)
        if not el:
            return {"ok": False, "error": f"ref '{ref}' is not on the current page; call inspect_page first"}
        if not self._alive():
            restarted = self._ensure_alive()
            return {"ok": False, "error": ("browser had closed; restarted -- re-inspect the page (refs are stale) and retry"
                                           if restarted else "browser is closed; call restart_browser, then re-inspect")}
        strat, loc_str, warning = self._record_strategy(el)
        try:
            live = smart_locator(self.page, strat, fallbacks=el.get("fallbacks"),
                                 fingerprint=el.get("fingerprint")).resolve()
        except SelfHealError as e:
            return {"ok": False, "error": f"could not resolve '{ref}': {str(e)[:140]}"}

        before = len(self.issues)
        name = el.get("name") or ref
        self.step_id += 1
        try:
            if kind == "fill":
                val = str(value or "")
                live.fill(val)
                self.input_counter += 1
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.fill("{_q(val)}")',
                                   "type": "fill", "target": loc_str,
                                   "targetDescription": f'Enter "{val}" in {name}',
                                   "value": val, "varName": f"input_{self.input_counter}"})
            elif kind == "select":
                val = str(value or "")
                live.select_option(val)
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.select_option("{_q(val)}")',
                                   "type": "select", "target": loc_str,
                                   "targetDescription": f'Select "{val}" in {name}',
                                   "value": val, "varName": ""})
            else:  # click
                live.click()
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.click()',
                                   "type": "click", "target": loc_str,
                                   "targetDescription": f'Click {name}', "value": "", "varName": ""})
        except Exception as e:
            self.step_id -= 1
            return {"ok": False, "error": f"{kind} failed on '{ref}': {str(e)[:160]}"}
        self._settle()
        result = {"ok": True, "url": self._url(), "title": self._title(),
                  "new_issues": self.issues[before:]}
        if warning:
            result["warning"] = warning
        return result

    def add_checkpoint(self, name, assert_type, value):
        after = self.steps[-1]["id"] if self.steps else 0
        self.assertions.append({"type": assert_type, "value": value,
                                "afterStep": after, "description": name})
        return {"ok": True, "checkpoint_count": len(self.assertions)}

    def recorded_flow(self):
        return {"steps": [{"id": s["id"], "desc": s["targetDescription"]} for s in self.steps],
                "checkpoints": [a["description"] for a in self.assertions],
                "issues_found": len(self.issues)}

    def create_test(self, ats_root, project, tc_id, flow_id, description,
                    preconditions="", expected=""):
        if len(self.steps) <= 1:
            return {"status": "error",
                    "message": "no flow recorded yet — drive the complete scenario "
                               "(navigate/click/fill) before creating the test"}
        pname = (project or {}).get("name") or (project or {}).get("id") or "the app"
        payload = {
            "tc_id": tc_id, "description": (description or tc_id)[:200], "flowId": flow_id,
            "preconditions": preconditions or f"{pname} reachable; logged in if required",
            "expectedResult": expected, "steps": self.steps,
            "assertions": self.assertions, "criteria": [],
        }
        res = generate_from_review(payload, ats_root)
        res = res if isinstance(res, dict) else {"status": "success", "result": str(res)}
        res.update({"recorded_steps": len(self.steps), "checkpoints": len(self.assertions)})
        warns = _lint_recording(self.steps, self.assertions)
        if warns:
            res["lint_warnings"] = warns   # nudge the agent to fix weak/dynamic assertions before delivering
        return res


# ── UI Map crawler (sync, runs on _BROWSER thread) ───────────────────────────

def _crawl_ui_map(session, start_url, max_pages=30, max_depth=3, on_log=None):
    """BFS crawl using the agent's existing authenticated sync Playwright page.
    Follows same-origin <a href> links only (no button-clicks that might mutate state).
    Saves elements per page, nav edges, errors. Navigates back to start_url when done."""
    page = session.page
    if not page:
        return {"ok": False, "error": "browser not running"}

    def _log(m):
        if on_log:
            on_log(str(m))

    m = re.match(r"(https?://[^/]+)", start_url)
    base_origin = m.group(1) if m else start_url

    pages_data = {}   # norm_url -> page snapshot
    nav_edges = []
    visited = set()
    errors = []
    queue = [(start_url, 0, None)]  # (url, depth, edge_dict)

    while queue and len(pages_data) < max_pages:
        url, depth, edge = queue.pop(0)
        norm = _normalize_url(url)
        if norm in visited:
            continue
        visited.add(norm)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
        except Exception as e:
            errors.append({"url": url, "error": str(e)[:200]})
            _log(f"[map] skip {url}: {str(e)[:80]}")
            continue

        if edge:
            nav_edges.append(edge)

        title = ""
        try:
            title = page.title()
        except Exception:
            pass

        raw = _comprehensive_snapshot(page)
        elements = []
        seen_refs: dict = {}
        for el in raw:
            if "_error" in el or not _label(el) or not el.get("visible", True):
                continue
            mod = element_to_model(el)
            ref = mod["ref"]
            if ref in seen_refs:
                seen_refs[ref] += 1
                mod["ref"] = f"{ref}-{seen_refs[ref]}"
            else:
                seen_refs[ref] = 0
            # Keep only what the agent needs — drop fingerprint/fallback bulk
            elements.append({k: mod[k] for k in
                              ("ref", "role", "name", "tag", "test_id",
                               "input_type", "placeholder", "primary") if k in mod})

        links_out = []
        try:
            for lnk in page.evaluate(_LINKS_JS):
                href = lnk.get("href", "")
                text = lnk.get("text", "")
                if not href or _should_skip_link(href, text):
                    continue
                norm_href = _normalize_url(href)
                if norm_href and norm_href not in visited:
                    links_out.append(norm_href)
                    if depth + 1 <= max_depth:
                        queue.append((href, depth + 1, {
                            "from": norm, "via": (text or href)[:60], "to": norm_href,
                        }))
        except Exception:
            pass

        pages_data[norm] = {
            "title": title, "url": norm, "depth": depth,
            "element_count": len(elements), "elements": elements,
            "links_out": sorted(set(links_out)),
        }
        _log(f"[map] [{len(pages_data)}/{max_pages}] {norm}  "
             f"({len(elements)} elements, depth {depth})")

    # Return browser to start page so the agent can continue where it was
    try:
        page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    return {
        "ok": True,
        "schema_version": 2,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "start_url": _normalize_url(start_url),
        "base_origin": base_origin,
        "page_count": len(pages_data),
        "element_count": sum(p["element_count"] for p in pages_data.values()),
        "pages": pages_data,
        "navigation": nav_edges,
        "errors": errors,
    }


# ── Agent dependencies ───────────────────────────────────────────────────────

@dataclass
class Deps:
    session: BrowserSession
    ats_root: str
    project: Optional[dict]
    config: dict = field(default_factory=dict)
    context_dir: str = ""     # folder of user-supplied context files (read_context_file)
    memory_path: str = ""     # the agent's durable memory file (read/update_memory)
    vision: bool = False      # model can see images -> run_test_case returns failure screenshots


# ── System prompt ──────────────────────────────────────────────────────────

_SYSTEM = (
    "You are Agrim ATS's autonomous QA engineer. You operate a REAL Chromium browser "
    "through tools to drive an app and author COMPLETE, runnable end-to-end test cases — "
    "full flows, not fragments.\n\n"
    "Operating loop:\n"
    "- You usually start ALREADY logged in (a saved session is loaded for you). Do NOT attempt to "
    "log in or type credentials unless you actually land on a login form.\n"
    "- Before acting on a page, call inspect_page to see its interactive elements (each has a "
    "stable 'ref'). Use ONLY refs from the MOST RECENT inspect_page. Re-inspect after any "
    "navigation or click that changes the page.\n"
    "- Drive the flow the user asked for, one action at a time (navigate / click / fill / select_option).\n"
    "- To open an item's detail, click its main row or title link — NOT inline per-row action "
    "buttons (invoice, ready, raise ticket, accept, ...) unless the goal explicitly needs them.\n"
    "- Add a checkpoint (add_checkpoint) at each meaningful outcome so the test verifies results.\n"
    "- When you have driven the FULL flow, call create_test_case EXACTLY ONCE (clear tc_id "
    "TC-<AREA>-NNN + flow_id). Then you MUST VERIFY it: call run_test_case and confirm it PASSES. "
    "If it fails, read the error (and read_test_file), then clear_recording, re-drive the corrected "
    "flow, create_test_case again (same id overwrites) and run_test_case again -- repeat until green "
    "(give up after ~3 tries and report what's failing). NEVER hand the user a test you have not seen "
    "pass. Use delete_test_case to clean up a stub or a bad test.\n"
    "- If the browser reports it is closed or crashed and actions keep failing, call restart_browser, "
    "then inspect_page again (your old refs are stale).\n\n"
    "Using the ATS app itself (as a user, never editing its code):\n"
    "- You CAN read and change the app's Settings: get_settings (environments, the seller/admin "
    "accounts WITH their credentials, execution options, Jira) and update_setting. So when asked to "
    "'use the seller credentials from settings', call get_settings — never ask the user for them.\n"
    "- You CAN browse the existing test suite (list_test_flows / read_test_cases) and read run "
    "reports / history (list_runs / read_run), and list projects (list_projects).\n"
    "- Note: you usually do NOT need credentials to log in — the session is already authenticated "
    "for you; get_settings is for answering questions and for flows that explicitly need an account.\n\n"
    "Context about the app (use what you're given — don't ask the user to paste things you can read):\n"
    "- At the start of a new project: (1) call map_app to discover all screens; "
    "(2) call extract_flows(context_file='<prd>') to turn the spec into a structured work order "
    "(list of flows, each with entry URL, steps, success criteria, suggested TC id); "
    "(3) call read_ui_map(url_filter=...) to see elements on a specific screen before driving it.\n"
    "- The user can ATTACH documents and images to a message. Document text is inlined for you and "
    "images are shown to you visually — read them and act on them.\n"
    "- The project may have a SCOPED CONTEXT FOLDER (a docs/code tree about the app). It is NOT "
    "imported — explore it like a repo: search_files(query) to find files by name/content, "
    "list_context_files(subdir) to browse a directory, read_context_file(name[, start_line, end_line]) "
    "to read one. Read only what's relevant; don't try to read everything. For an image, ask the user "
    "to attach it so you can view it.\n"
    "- If the user's message contains an '@path' token, that is a reference to a file in the context "
    "folder they want you to look at — read it with read_context_file.\n"
    "- You have a durable MEMORY FILE (read_memory / update_memory) that is also shown to you at the start "
    "of every turn. Whenever you learn something durable about the app — stable URLs, where a feature lives, "
    "a login quirk, a recurring gotcha — SAVE it with update_memory so future sessions start informed. Keep "
    "it concise and factual; don't store transient run state.\n\n"
    "Stay on task:\n"
    "- You have a limited action budget per turn; spend it efficiently (don't re-inspect when the "
    "page did not change). For open-ended exploration, cover the most important things first, then "
    "give a SUMMARY of what you found and offer to continue — never trail off mid-action or try to "
    "do everything in one turn.\n"
    "- Pursue ONLY the user's goal; do not wander into unrelated pages or features.\n"
    "- If you get stuck (an element is missing, a modal will not close, a page is unexpectedly "
    "complex) or you are unsure, STOP and ASK the user — do NOT keep clicking around hoping it works.\n"
    "- Report any breakage you surface (console errors, JS exceptions, failed requests, broken "
    "images) as findings.\n\n"
    "Rules:\n"
    "- NEVER log out or perform destructive actions (delete / cancel / reject / accept / pack) "
    "UNLESS the user's goal explicitly requires it.\n"
    "- Never invent refs. You have conversation memory — the user can correct you mid-flow; adapt."
)


def _load_playbook():
    """The permanent operating playbook (engine/agent_playbook.md), appended to the system
    prompt every session. Human-authored GENERAL procedure (how to work); app-specific facts
    live in the per-project memory file instead. Edit the .md to update standing behavior."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_playbook.md"),
                  "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_PLAYBOOK = _load_playbook()


def build_agent(model, max_tokens=None):
    mt = int(max_tokens if max_tokens is not None else AGENT_MAX_TOKENS)
    settings = {"temperature": 0.2}
    if mt > 0:                       # 0/unset => omit max_tokens so the model uses its own output max
        settings["max_tokens"] = mt
    agent = Agent(
        model,
        deps_type=Deps,
        system_prompt=_SYSTEM + (("\n\n" + _PLAYBOOK) if _PLAYBOOK else ""),
        retries=2,
        # Generous so a run_test_case verification (a full pytest run) is not cancelled mid-test.
        tool_timeout=240,
        model_settings=settings,
    )

    @agent.tool
    async def navigate(ctx: RunContext[Deps], url: str) -> dict:
        """Navigate the browser to a URL (absolute http(s) URL). Returns the resulting URL,
        page title, and any new breakage issues triggered by the load."""
        return await _bro(ctx.deps.session.navigate, url)

    @agent.tool
    async def restart_browser(ctx: RunContext[Deps]) -> dict:
        """Relaunch the browser after it has closed or crashed (you saw a 'page/context/browser
        has been closed' error and navigate/inspect/click keep failing). Reuses the saved login.
        Your old refs become stale — call inspect_page before acting again. Returns the
        post-restart url and title."""
        return await _bro(ctx.deps.session.restart)

    @agent.tool
    async def inspect_page(ctx: RunContext[Deps], include_hidden: bool = False) -> dict:
        """Capture the interactive elements on the CURRENT page. Each element has a stable 'ref'
        you pass to click/fill/select_option. Also returns counts of hidden elements and any
        breakage (console errors, JS exceptions, failed requests, broken images). Call this
        before acting, and again after any navigation or click that changes the page. Set
        include_hidden=true to also list elements users miss (display:none, zero-size, aria-hidden)."""
        return await _bro(ctx.deps.session.inspect, 40, include_hidden)

    @agent.tool
    async def click(ctx: RunContext[Deps], ref: str) -> dict:
        """Click the element with the given ref (from the latest inspect_page). Records the action
        as a test step. Returns ok, the new URL/title, and any breakage triggered."""
        return await _bro(ctx.deps.session.act, "click", ref, None)

    @agent.tool
    async def fill(ctx: RunContext[Deps], ref: str, value: str) -> dict:
        """Type `value` into the input/textbox with the given ref. Records a test step."""
        return await _bro(ctx.deps.session.act, "fill", ref, value)

    @agent.tool
    async def select_option(ctx: RunContext[Deps], ref: str, value: str) -> dict:
        """Select `value` in the <select>/combobox with the given ref. Records a test step."""
        return await _bro(ctx.deps.session.act, "select", ref, value)

    @agent.tool
    async def add_checkpoint(ctx: RunContext[Deps], name: str, assert_type: str, value: str) -> dict:
        """Record a verification checkpoint after the latest step. assert_type must be
        'url_contains' or 'page_contains_text'; value is the expected substring. Add these at
        meaningful results so the generated test verifies outcomes."""
        if assert_type not in ("url_contains", "page_contains_text"):
            return {"ok": False, "error": "assert_type must be 'url_contains' or 'page_contains_text'"}
        return await _bro(ctx.deps.session.add_checkpoint, name, assert_type, value)

    @agent.tool
    async def get_recorded_flow(ctx: RunContext[Deps]) -> dict:
        """Return the steps and checkpoints recorded so far, so you can review the flow before
        creating the test."""
        return await _bro(ctx.deps.session.recorded_flow)

    @agent.tool
    async def create_test_case(ctx: RunContext[Deps], tc_id: str, flow_id: str, description: str,
                               preconditions: str = "", expected_result: str = "") -> dict:
        """Turn the recorded flow (all steps + checkpoints) into a real, runnable test case in flow
        `flow_id` with id `tc_id`. Call this ONCE, after driving the COMPLETE end-to-end flow.
        Returns the created test's flow/path."""
        return await _bro(ctx.deps.session.create_test, ctx.deps.ats_root, ctx.deps.project,
                          tc_id, flow_id, description, preconditions, expected_result)

    @agent.tool
    async def run_test_case(ctx: RunContext[Deps], tc_id: str, flow_id: str):
        """Run ONE test case through the REAL pytest runner to VERIFY it passes — call this right
        after create_test_case; never deliver a test you have not seen pass. To be sure it is
        RELIABLE (not flaky) it runs the test TWICE and only reports passed when BOTH runs are green.
        On failure it returns the pytest tail AND (for vision models) the FAILURE SCREENSHOT — LOOK at
        the screenshot to see what actually went wrong (acted on the wrong element, a blocking
        modal/overlay, an empty state, an error toast), then fix the flow and re-run. Returns
        {passed, flaky?, summary, tail, error_detail?}."""
        ats_root = ctx.deps.ats_root
        flow_dir = os.path.join(ats_root, "tests", "flows", flow_id)
        if not os.path.isdir(os.path.join(ats_root, "tests")) or not os.path.isdir(flow_dir):
            return {"ok": False, "error": f"flow '{flow_id}' not found under tests/flows"}
        underscored = tc_id.replace("-", "_")
        base_env = os.environ.copy()
        base_env["ATS_ROOT"] = ats_root
        base_env["PYTHONPATH"] = ats_root
        base_env["PYTHONUNBUFFERED"] = "1"
        proj = ctx.deps.project or {}
        if proj.get("id"):
            base_env["ATS_PROJECT_ID"] = proj["id"]
        base_env.setdefault("ATS_ENV", os.environ.get("ATS_ENV") or "dev")
        verify_root = os.path.join(ats_root, "results", "_agent_verify")
        try:
            import shutil
            shutil.rmtree(verify_root, ignore_errors=True)   # keep only the latest attempt's artifacts
        except Exception:
            pass

        # Windows: prevent handle inheritance deadlocks when agent has live Chrome/Playwright.
        # stdin=DEVNULL: pytest must not inherit the agent's stdin (Electron IPC pipe) or it
        # blocks waiting for input that never arrives and the process never exits.
        # CREATE_NO_WINDOW + close_fds: sever the inheritance chain so Chrome subprocesses
        # spawned by the test's Playwright don't hold the stdout PIPE open past pytest exit.
        _spawn_kw: dict = {"stdin": asyncio.subprocess.DEVNULL}
        if sys.platform == "win32":
            import subprocess as _sp
            _spawn_kw["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0x08000000)
            _spawn_kw["close_fds"] = True  # Python 3.9+: PROC_THREAD_ATTRIBUTE_HANDLE_LIST

        async def _one(idx):
            rdir = os.path.join(verify_root, f"{underscored}_{idx}")
            env = dict(base_env)
            env["ATS_RESULTS_DIR"] = rdir   # known dir so we can find the failure screenshot
            cmd = [sys.executable, "-m", "pytest", flow_dir, "-k", underscored,
                   "--tb=short", "-q", "-p", "no:cacheprovider", "--tracing=off"]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=ats_root, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    **_spawn_kw)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return {"passed": False, "rc": -1, "text": "test run timed out after 180s (hung or far too slow)", "rdir": rdir, "no_tests": False}
            except Exception as e:
                return {"passed": False, "rc": -2, "text": f"could not start pytest: {str(e)[:160]}", "rdir": rdir, "no_tests": False}
            text = (out or b"").decode("utf-8", "replace")
            rc = proc.returncode
            no_tests = (rc == 5) or ("no tests ran" in text.lower())
            return {"passed": (rc == 0 and not no_tests), "rc": rc, "text": text, "rdir": rdir, "no_tests": no_tests}

        r1 = await _one(1)
        if r1["no_tests"]:
            return {"ok": True, "passed": False, "exit_code": r1["rc"],
                    "error": (f"no test matched '{underscored}' in flow '{flow_id}' — confirm "
                              "create_test_case succeeded and the tc_id/flow_id are right")}
        runs = [r1]
        verify_runs = int(os.environ.get("ATS_VERIFY_RUNS") or 2)
        if r1["passed"] and verify_runs >= 2:
            runs.append(await _one(2))   # flakiness gate: only "passed" if it passes CONSISTENTLY
        passed_all = all(r["passed"] for r in runs)
        flaky = (not passed_all) and any(r["passed"] for r in runs)
        result = {"ok": True, "passed": passed_all, "runs": len(runs), "exit_code": runs[-1]["rc"],
                  "summary": _pytest_summary(runs[-1]["text"]) or _pytest_summary(r1["text"])}
        if flaky:
            result["flaky"] = True
            result["note"] = ("passed on one run but FAILED on another -> FLAKY, not reliable. Add an "
                              "explicit wait for the result or a more robust assertion, then re-run.")
        if passed_all:
            return result
        failed = next((r for r in runs if not r["passed"]), runs[-1])
        result["tail"] = (failed["text"] or "")[-2500:]
        shot, err = _find_failure_artifacts(failed["rdir"])
        if err:
            result["error_detail"] = err[:800]
        # Vision: hand the model the actual failure screenshot so it can SEE the problem and fix it.
        if shot and ctx.deps.vision:
            img = _binary_part(shot, "image/png", max_bytes=8_000_000)
            if img is not None:
                head = {k: v for k, v in result.items() if k != "tail"}
                txt = ("run_test_case verdict: " + json.dumps(head, ensure_ascii=True, default=str) +
                       "\nThe image below is the FAILURE SCREENSHOT of the running test — look at it to see "
                       "what actually went wrong (wrong element, a blocking modal/overlay, an empty state, an "
                       "error toast), then fix the flow and re-run.\n\npytest tail:\n" + result["tail"])
                return ToolReturn(return_value=result, content=[txt, img])
        return result

    @agent.tool
    async def read_test_file(ctx: RunContext[Deps], flow_id: str, tc_id: str) -> dict:
        """Read the GENERATED test code (the test_<flow>.py function) for a test case so you can
        diagnose why run_test_case failed. Read-only. To fix: clear_recording, re-drive the
        corrected flow, and create_test_case again (same tc_id overwrites)."""
        path = os.path.join(ctx.deps.ats_root, "tests", "flows", flow_id, f"test_{flow_id}.py")
        if not os.path.isfile(path):
            return {"ok": False, "error": f"test file for flow '{flow_id}' not found"}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
        func = "test_" + tc_id.replace("-", "_")
        m = re.search(r'(\n@pytest\.mark\.tc\("' + re.escape(tc_id) + r'"\)\ndef '
                      + re.escape(func) + r'\(.*?)(?=\n@pytest\.mark|\Z)', content, re.DOTALL)
        if m:
            return {"ok": True, "flow": flow_id, "tc_id": tc_id, "code": m.group(1).strip()[:6000]}
        return {"ok": True, "flow": flow_id, "tc_id": tc_id, "code": content[:6000],
                "note": "exact function not found; returning the file head"}

    @agent.tool
    async def clear_recording(ctx: RunContext[Deps]) -> dict:
        """Discard the steps/checkpoints recorded so far (KEEPS our conversation). Call this
        before re-driving a flow you are fixing, so the new recording doesn't append onto the old
        one; then create_test_case again with the SAME tc_id to overwrite the failing test."""
        s = ctx.deps.session
        s.steps = []
        s.assertions = []
        s.step_id = 0
        s.input_counter = 0
        return {"ok": True, "message": "recorded steps and checkpoints cleared"}

    @agent.tool
    async def delete_test_case(ctx: RunContext[Deps], tc_id: str, flow_id: str) -> dict:
        """Delete a test case from a flow — removes it from test_cases.json, test_data.json AND the
        test_<flow>.py function. Use it to clean up a stub or a test you replaced. Confirm with the
        user before deleting anything you did not just create yourself."""
        return _delete_test_case(ctx.deps.ats_root, flow_id, tc_id)

    # ── ATS app features: use the tool itself AS A USER (read/change settings,
    # browse the suite, read reports). File-backed — no browser involved. ──

    @agent.tool
    async def get_settings(ctx: RunContext[Deps]) -> dict:
        """Read the ATS app Settings (config.json): environments, platform accounts
        (seller/admin) WITH their login credentials, execution defaults, LLM providers,
        pass criteria, and Jira project. Use this to look up credentials, URLs, or any
        configured option a user would see in Settings (the Jira API token is redacted)."""
        cfg = _read_json_file(os.path.join(ctx.deps.ats_root, "config.json")) or {}
        cfg = json.loads(json.dumps(cfg, default=str))
        try:
            if (cfg.get("jira") or {}).get("apiToken"):
                cfg["jira"]["apiToken"] = "[set]"
        except Exception:
            pass
        return cfg

    @agent.tool
    async def update_setting(ctx: RunContext[Deps], dotted_path: str, value: str) -> dict:
        """Change ONE setting in config.json (as a user would in the Settings UI).
        dotted_path e.g. 'execution.default_mode' or 'default_environment'; value is
        parsed as JSON when possible (true / 5 / "x"), else kept as a string."""
        path = os.path.join(ctx.deps.ats_root, "config.json")
        cfg = _read_json_file(path)
        if cfg is None:
            return {"ok": False, "error": "config.json not found"}
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = value
        keys = [k for k in dotted_path.split(".") if k]
        if not keys:
            return {"ok": False, "error": "empty dotted_path"}
        node = cfg
        for k in keys[:-1]:
            if not isinstance(node.get(k), dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = parsed
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            return {"ok": False, "error": f"could not write config.json: {e}"}
        return {"ok": True, "path": dotted_path, "value": parsed}

    @agent.tool
    async def list_test_flows(ctx: RunContext[Deps]) -> dict:
        """List the test flows and their test case IDs (tests/flows/*/test_cases.json) —
        the suite shown in the app's left panel."""
        flows_dir = os.path.join(ctx.deps.ats_root, "tests", "flows")
        out = []
        try:
            names = sorted(os.listdir(flows_dir))
        except Exception:
            names = []
        for name in names:
            tcf = os.path.join(flows_dir, name, "test_cases.json")
            if os.path.isfile(tcf):
                tcs = _read_json_file(tcf) or []
                if isinstance(tcs, list):
                    out.append({"flow": name, "count": len(tcs),
                                "tc_ids": [t.get("tc_id") for t in tcs if isinstance(t, dict)][:60]})
        return {"flows": out}

    @agent.tool
    async def read_test_cases(ctx: RunContext[Deps], flow_id: str) -> dict:
        """Read the test cases in a flow (id, description, checkpoints, expected_result)."""
        tcf = os.path.join(ctx.deps.ats_root, "tests", "flows", flow_id, "test_cases.json")
        tcs = _read_json_file(tcf)
        if tcs is None:
            return {"ok": False, "error": f"flow '{flow_id}' not found"}
        return {"flow": flow_id, "test_cases": [
            {"tc_id": t.get("tc_id"), "description": t.get("description"),
             "checkpoints": t.get("checkpoints"), "expected_result": t.get("expected_result")}
            for t in tcs if isinstance(t, dict)][:60]}

    @agent.tool
    async def list_runs(ctx: RunContext[Deps], limit: int = 10) -> dict:
        """List recent test runs with pass/fail summaries (results/<ts>/run_metadata.json) —
        the History panel."""
        rd = os.path.join(ctx.deps.ats_root, "results")
        runs = []
        try:
            stamps = sorted([d for d in os.listdir(rd) if os.path.isdir(os.path.join(rd, d))], reverse=True)
        except Exception:
            stamps = []
        for ts in stamps:
            m = _read_json_file(os.path.join(rd, ts, "run_metadata.json"))
            if m:
                runs.append({"run": ts, "status": m.get("status"), "passed": m.get("passed"),
                             "failed": m.get("failed"), "skipped": m.get("skipped"), "total": m.get("total")})
            if len(runs) >= max(1, limit):
                break
        return {"runs": runs}

    @agent.tool
    async def read_run(ctx: RunContext[Deps], run_id: str) -> dict:
        """Read one run's per-test results (verdict, duration, error) from run_metadata.json."""
        m = _read_json_file(os.path.join(ctx.deps.ats_root, "results", run_id, "run_metadata.json"))
        if m is None:
            return {"ok": False, "error": f"run '{run_id}' not found"}
        res = m.get("results", []) if isinstance(m.get("results"), list) else []
        return {"run": run_id, "status": m.get("status"),
                "summary": {k: m.get(k) for k in ("passed", "failed", "skipped", "flaky", "warnings", "total")},
                "results": [{"tc": r.get("tc_id") or r.get("name"), "verdict": r.get("status") or r.get("verdict"),
                             "duration": r.get("duration"), "error": (r.get("error") or "")[:200]}
                            for r in res if isinstance(r, dict)][:80]}

    @agent.tool
    async def list_projects(ctx: RunContext[Deps]) -> dict:
        """List the projects this tool targets (apps under test) and which is active."""
        root = ctx.deps.ats_root
        return {"active": project_store.get_active_project_id(root),
                "projects": [{"id": p.get("id"), "name": p.get("name"), "url": project_store.resolve_base_url(p)}
                             for p in project_store.list_projects(root)]}

    # ── App context: a folder of docs/screenshots + a durable memory file ──
    # These give the agent reusable knowledge of the app under test. The memory
    # file is also injected into the prompt each turn (the @agent.instructions
    # block below) so the agent always starts a turn aware of what it has learned.

    @agent.tool
    async def list_context_files(ctx: RunContext[Deps], subdir: str = ".") -> dict:
        """List ONE directory level of the project's SCOPED context FOLDER (a folder you explore
        like a repo — it is NOT imported, nothing is auto-loaded). Pass `subdir` to descend, e.g.
        'docs' or 'src/auth'. Junk (.git/node_modules/caches/dot-dirs) is hidden. Returns `dirs`
        and `files` (name/ext/bytes). Use search_files to find by name/content, read_context_file
        to read one."""
        base = ctx.deps.context_dir or ""
        start = _scoped_path(base, "" if subdir in ("", ".") else subdir)
        if not start or not os.path.isdir(start):
            return {"ok": False, "error": f"no such folder: {subdir}", "dir": base}
        dirs, files = [], []
        try:
            for entry in sorted(os.listdir(start)):
                if _is_junk_name(entry):
                    continue
                full = os.path.join(start, entry)
                rel = os.path.relpath(full, base).replace("\\", "/")
                if os.path.isdir(full):
                    dirs.append({"name": rel})
                else:
                    try:
                        size = os.path.getsize(full)
                    except Exception:
                        size = 0
                    files.append({"name": rel, "ext": os.path.splitext(entry)[1].lower(), "bytes": size})
                if len(dirs) + len(files) >= 300:
                    break
        except Exception as e:
            return {"ok": False, "error": str(e)[:160], "dir": base}
        return {"ok": True, "dir": base, "subdir": subdir, "dirs": dirs, "files": files,
                "count": len(dirs) + len(files)}

    @agent.tool
    async def search_files(ctx: RunContext[Deps], query: str, content: bool = True) -> dict:
        """Search the SCOPED context FOLDER for `query`: matches file NAMES always, plus file
        CONTENT (text files) when content=true. Junk and big/binary files are skipped. Returns
        `name_matches` (paths) and `content_matches` (file/line/text snippets). This is how you
        find the right file in a large folder — then read it with read_context_file."""
        base = ctx.deps.context_dir or ""
        if not base or not os.path.isdir(base):
            return {"ok": False, "error": "no context folder set for this project"}
        if not (query or "").strip():
            return {"ok": False, "error": "empty query"}
        try:
            return _search_context(base, query, content)
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}

    @agent.tool
    async def read_context_file(ctx: RunContext[Deps], name: str,
                                start_line: int = 0, end_line: int = 0) -> dict:
        """Read a file from the SCOPED context FOLDER by its path (as listed by list_context_files
        / search_files). text/markdown/csv/json/code/pdf/docx/xlsx come back as text. For a big
        file, pass start_line/end_line (1-based) to read just a slice. Images can't be read here —
        ask the user to attach the image to a message so you can view it."""
        base = ctx.deps.context_dir or ""
        safe = _scoped_path(base, name)
        if not safe:
            return {"ok": False, "error": "name escapes the context folder"}
        if not os.path.isfile(safe):
            return {"ok": False, "error": f"no such context file: {name}"}
        ext = os.path.splitext(safe)[1].lower()
        if ext in IMAGE_MIME:
            return {"ok": False, "error": f"'{name}' is an image — ask the user to attach it "
                                          "to a message so you can view it."}
        text, truncated = extract_file_text(safe)
        if text is None:
            return {"ok": False, "error": f"could not extract text from '{name}' "
                                          "(unsupported type or missing extractor)"}
        if start_line or end_line:
            lines = text.splitlines()
            s = max(1, int(start_line or 1))
            e = int(end_line) if end_line else len(lines)
            return {"ok": True, "name": name, "start_line": s, "end_line": min(e, len(lines)),
                    "total_lines": len(lines), "text": "\n".join(lines[s - 1:e])}
        return {"ok": True, "name": name, "truncated": truncated, "text": text}

    @agent.tool
    async def read_memory(ctx: RunContext[Deps]) -> dict:
        """Read your full durable MEMORY FILE for this project (facts you have saved about
        the app). It is also injected into your context each turn; use this to re-read it."""
        p = ctx.deps.memory_path
        try:
            if p and os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return {"ok": True, "path": p, "content": f.read()}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
        return {"ok": True, "path": p, "content": "", "note": "memory is empty"}

    @agent.tool
    async def extract_flows(ctx: RunContext[Deps], context_file: str = "",
                            focus: str = "") -> dict:
        """Parse a PRD, spec, or requirements file from the context folder into a structured
        list of testable end-to-end flows — each with role, entry URL, steps, and success
        criteria. This is the work order for autonomous test authoring.
        context_file: filename in the project context folder (list_context_files to browse).
        focus: optional keyword to filter flows, e.g. 'catalog' or 'checkout'.
        After extracting, navigate to each flow's entry URL, drive the flow, create_test_case,
        and run_test_case — repeat for each flow in the list."""
        base = ctx.deps.context_dir or ""

        # If no file given, list what's available
        if not context_file:
            if not base or not os.path.isdir(base):
                return {"ok": False, "error": "no context folder set — attach a file or set the context folder in project settings"}
            try:
                files = [f for f in sorted(os.listdir(base))
                         if not _is_junk_name(f) and os.path.isfile(os.path.join(base, f))]
            except Exception:
                files = []
            return {"ok": False, "error": "context_file is required",
                    "available_files": files,
                    "hint": "Call extract_flows(context_file='<name>') with one of the files above."}

        safe = _scoped_path(base, context_file) if base else None
        if not safe or not os.path.isfile(safe):
            return {"ok": False, "error": f"file not found in context folder: {context_file!r}"}

        text, truncated = extract_file_text(safe)
        if text is None:
            return {"ok": False, "error": f"could not read '{context_file}' (unsupported format or binary)"}
        if len(text) > 40_000:
            text = text[:40_000]
            truncated = True

        # Inject known pages from the UI map so the LLM can use real entry URLs
        ats_root = ctx.deps.ats_root
        proj_id = (ctx.deps.project or {}).get("id", "")
        map_path = (project_store.ui_map_path(ats_root, proj_id)
                    if proj_id else os.path.join(ats_root, "ui_map.json"))
        pages_hint = ""
        if os.path.isfile(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    ui_map = json.load(f)
                page_lines = [f"  {p['url']}  ({p['element_count']} elements, title: {p['title']!r})"
                              for p in list(ui_map.get("pages", {}).values())[:20]]
                if page_lines:
                    pages_hint = ("\n\nKnown app pages (from UI map — use these for entry URLs):\n"
                                  + "\n".join(page_lines))
            except Exception:
                pass

        focus_clause = f"\nFocus only on flows related to: {focus}\n" if focus else ""

        _SYSTEM_EXTRACT = (
            "You are a senior QA engineer specialising in end-to-end test planning.\n"
            "Extract every distinct testable end-to-end flow from the specification.\n"
            "A flow is ONE complete user journey: start → actions → verifiable outcome.\n\n"
            "Output a JSON ARRAY (no wrapper object). Each item:\n"
            "  id                – 'flow-NNN' (sequential, 3-digit)\n"
            "  name              – concise verb+object ('Create product', 'Accept purchase order')\n"
            "  role              – user type ('Seller', 'Admin', 'Buyer', 'Guest')\n"
            "  entry             – URL path or page name where the flow starts\n"
            "  preconditions     – one line: what must be true before starting\n"
            "  steps             – array of 3-8 high-level user actions (what they DO)\n"
            "  success_criteria  – what must be true at the END (what to assert)\n"
            "  suggested_tc_id   – 'TC-<AREA>-NNN' (AREA = CATALOG, ORDERS, AUTH, SETTINGS, ADMIN …)\n"
            "  suggested_flow_id – lowercase folder name ('catalog', 'orders', 'auth')\n"
            "  priority          – 'high' | 'medium' | 'low'\n\n"
            "Rules:\n"
            "- Happy path for every major feature. Include important negative/error paths too.\n"
            "- Do NOT include infrastructure or data-setup steps — only real user journeys.\n"
            "- Steps: 3-8 high-level actions. 'Fill form and submit' beats listing every field.\n"
            "- Group related flows by suggested_flow_id.\n"
            "- If you cannot determine an entry URL, use the closest known page path."
        )

        user_prompt = (
            f"Specification file: {context_file}\n"
            f"{focus_clause}"
            f"{pages_hint}\n\n"
            f"--- SPECIFICATION ---\n{text}\n--- END ---"
        )

        try:
            provider = _llm.make_provider(ctx.deps.config)
        except _llm.LLMError as e:
            return {"ok": False, "error": f"LLM provider not configured: {e}"}

        loop = asyncio.get_running_loop()
        try:
            flows = await loop.run_in_executor(
                None,
                lambda: _llm.complete_json(provider, _SYSTEM_EXTRACT, user_prompt, max_tokens=4096)
            )
        except _llm.LLMNotConfigured as e:
            return {"ok": False, "error": str(e)}
        except _llm.LLMError as e:
            return {"ok": False, "error": f"LLM extraction failed: {str(e)[:300]}"}
        except Exception as e:
            return {"ok": False, "error": f"unexpected error: {str(e)[:200]}"}

        if not isinstance(flows, list):
            # LLM may return {"flows": [...]} — unwrap
            if isinstance(flows, dict):
                for key in ("flows", "test_flows", "items", "results"):
                    if isinstance(flows.get(key), list):
                        flows = flows[key]
                        break
        if not isinstance(flows, list):
            return {"ok": False, "error": "LLM did not return a JSON array of flows",
                    "raw": str(flows)[:400]}

        if focus:
            flows = [f for f in flows
                     if focus.lower() in json.dumps(f).lower()]

        return {
            "ok": True,
            "flow_count": len(flows),
            "source_file": context_file,
            "truncated": truncated,
            "flows": flows,
            "note": (
                "Work order ready. For each flow: navigate to flow['entry'], drive the steps, "
                "add_checkpoint at each success_criteria, create_test_case (use suggested_tc_id "
                "and suggested_flow_id), then run_test_case. Repeat until all flows are green."
            ),
        }

    @agent.tool
    async def update_memory(ctx: RunContext[Deps], content: str, mode: str = "append") -> dict:
        """Save durable knowledge about the app to your MEMORY FILE so future sessions start
        informed (stable URLs, where features live, login quirks, recurring gotchas).
        mode='append' (default) adds a timestamped bullet; mode='replace' overwrites the whole
        file. Keep it concise and factual — do not store transient run state."""
        p = ctx.deps.memory_path
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "empty content"}
        if mode not in ("append", "replace"):
            return {"ok": False, "error": "mode must be 'append' or 'replace'"}
        try:
            if os.path.dirname(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
            if mode == "replace":
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content.rstrip() + "\n")
            else:
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(p, "a", encoding="utf-8") as f:
                    f.write(f"\n- ({stamp}) {content}\n")
            return {"ok": True, "path": p, "mode": mode, "bytes": os.path.getsize(p)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}

    @agent.tool
    async def map_app(ctx: RunContext[Deps], start_url: str = "",
                      max_pages: int = 30, max_depth: int = 3) -> dict:
        """BFS-crawl the web app to discover all screens, their interactive elements, and
        navigation paths, then save a ui_map.json to the project folder.
        Call this at the start of a new project — the map tells you what pages exist and what
        elements are on them BEFORE you author any test, so you're not exploring blind.
        start_url: leave empty to start from the current page.
        max_pages: stop after N pages (default 30). max_depth: BFS link depth (default 3).
        Returns a page inventory; use read_ui_map to query elements for a specific screen."""
        session = ctx.deps.session
        if not session.page:
            return {"ok": False, "error": "browser not running — call navigate first"}
        actual_start = start_url
        if not actual_start:
            try:
                actual_start = await _bro(lambda: session.page.url)
            except Exception:
                return {"ok": False, "error": "could not read current URL"}
        if not actual_start or not actual_start.startswith("http"):
            return {"ok": False, "error": f"invalid start URL: {actual_start!r}"}

        def _run():
            return _crawl_ui_map(session, actual_start, max_pages, max_depth, on_log=log)

        ui_map = await _bro(_run)
        if not ui_map.get("ok"):
            return ui_map

        ats_root = ctx.deps.ats_root
        proj_id = (ctx.deps.project or {}).get("id", "")
        out_path = (project_store.ui_map_path(ats_root, proj_id)
                    if proj_id else os.path.join(ats_root, "ui_map.json"))
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(ui_map, f, indent=2)
        except Exception as e:
            ui_map["save_error"] = str(e)[:200]

        page_list = [
            {"url": p["url"], "title": p["title"],
             "depth": p["depth"], "elements": p["element_count"]}
            for p in ui_map["pages"].values()
        ]
        return {
            "ok": True,
            "page_count": ui_map["page_count"],
            "element_count": ui_map["element_count"],
            "errors": len(ui_map["errors"]),
            "saved_to": out_path,
            "pages": page_list,
            "note": "Call read_ui_map(url_filter='/some/path') to see elements for a specific screen.",
        }

    @agent.tool
    async def read_ui_map(ctx: RunContext[Deps], url_filter: str = "") -> dict:
        """Query the saved UI map (built by map_app).
        url_filter: if given, returns full element list for pages whose URL contains this string
        (e.g. '/catalog', '/orders'). Without a filter, returns the page inventory and navigation
        graph only (no per-page elements) to stay concise. Run map_app first if the map is missing."""
        ats_root = ctx.deps.ats_root
        proj_id = (ctx.deps.project or {}).get("id", "")
        path = (project_store.ui_map_path(ats_root, proj_id)
                if proj_id else os.path.join(ats_root, "ui_map.json"))
        if not os.path.isfile(path):
            return {"ok": False, "error": "no UI map found — call map_app first"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                ui_map = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"could not read ui_map.json: {str(e)[:160]}"}

        pages = ui_map.get("pages", {})
        if url_filter:
            matched = {u: p for u, p in pages.items()
                       if url_filter.lower() in u.lower()}
            if not matched:
                return {"ok": True, "found": 0,
                        "note": f"no pages match '{url_filter}'",
                        "all_urls": [p["url"] for p in pages.values()]}
            return {
                "ok": True, "found": len(matched),
                "pages": {
                    u: {"title": p["title"], "depth": p["depth"],
                        "elements": p.get("elements", []),
                        "links_out": p.get("links_out", [])}
                    for u, p in matched.items()
                },
            }
        # Inventory + deduplicated nav only
        inventory = [
            {"url": p["url"], "title": p["title"],
             "depth": p["depth"], "element_count": p["element_count"],
             "links_out": len(p.get("links_out", []))}
            for p in pages.values()
        ]
        seen_edges: set = set()
        nav = []
        for e in ui_map.get("navigation", []):
            key = f"{e.get('from')}|{e.get('to')}"
            if key not in seen_edges:
                seen_edges.add(key)
                nav.append(e)
        return {
            "ok": True,
            "generated_at": ui_map.get("generated_at"),
            "page_count": ui_map.get("page_count", len(inventory)),
            "element_count": ui_map.get("element_count", 0),
            "pages": inventory,
            "navigation": nav[:60],
        }

    @agent.instructions
    async def _project_memory_and_context(ctx: RunContext[Deps]) -> str:
        """Injected fresh each turn (instructions are regenerated per run and not stored in
        history): the memory file's contents + a short listing of available context files."""
        blocks = []
        p = ctx.deps.memory_path
        try:
            if p and os.path.isfile(p):
                mem = open(p, "r", encoding="utf-8", errors="replace").read().strip()
                if mem:
                    blocks.append("PROJECT MEMORY (durable notes you saved about this app — "
                                  "trust and extend these):\n" + mem[:8000])
        except Exception:
            pass
        d = ctx.deps.context_dir
        try:
            if d and os.path.isdir(d):
                top = [e for e in sorted(os.listdir(d)) if not _is_junk_name(e)]
                if top:
                    preview = ", ".join(top[:20]) + (" ..." if len(top) > 20 else "")
                    blocks.append(
                        "SCOPED CONTEXT FOLDER: " + d + "\nTop level: " + preview + "\n"
                        "It is NOT loaded for you — use search_files / list_context_files / "
                        "read_context_file to explore and read only what's relevant.")
        except Exception:
            pass
        return "\n\n".join(blocks)

    return agent


# ── Model construction (reuses config.json "llm"; OpenAI-compatible providers) ─

def build_model(config, provider_name=None):
    llm_cfg = (config or {}).get("llm") or {}
    name = provider_name or os.environ.get("ATS_LLM_PROVIDER") or llm_cfg.get("default_provider", "mimo")
    cfg = (llm_cfg.get("providers") or {}).get(name, {})

    if name == "ollama":
        base = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        prov = OpenAIProvider(base_url=base, api_key="ollama")
        return OpenAIChatModel(cfg.get("model", "llama3.1"), provider=prov), name, cfg.get("model", "llama3.1")

    # mimo / openai / any OpenAI-compatible provider (has base_url + api_key_env)
    key_env = cfg.get("api_key_env", "")
    api_key = os.environ.get(key_env, "") if key_env else ""
    if not api_key:
        raise RuntimeError(
            f"{name} API key not set (expected env {key_env or '?'}). "
            f"Set it (Settings / env) or pick another provider.")
    base_url = cfg.get("base_url") or "https://api.openai.com/v1"
    model_name = cfg.get("model", "gpt-4o")
    prov = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIChatModel(model_name, provider=prov), name, model_name


def _resolve_credentials(project, config, platform_name="seller", index=0):
    """Where the agent's login comes from, in priority order:
      1. the project's own settings (project.auth.credentials), then
      2. config.json platforms.<platform>.users[index]  (the legacy 'system settings').
    Returns (email, password) or (None, None). The agent never types these — the
    session logs in once with them and reuses the saved storage_state thereafter."""
    cred = ((project or {}).get("auth") or {}).get("credentials") or {}
    if cred.get("email") and cred.get("password"):
        return cred["email"], cred["password"]
    plats = (config or {}).get("platforms") or {}
    users = ((plats.get(platform_name) or {}).get("users")) or []
    if users:
        u = users[index] if 0 <= index < len(users) else users[0]
        if u.get("email"):
            return u.get("email"), u.get("password", "")
    return None, None


# ── Streaming: map Pydantic AI events -> IPC events ──────────────────────────

def _short(obj, n=300):
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=True, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= n else s[:n] + "..."


async def _stream_handler(ctx, stream):
    async for ev in stream:
        kind = type(ev).__name__
        try:
            if kind == "FunctionToolCallEvent":
                part = ev.part
                args = part.args
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                emit({"event": "tool_call", "tool": part.tool_name, "args": args})
            elif kind == "FunctionToolResultEvent":
                part = ev.part
                emit({"event": "tool_result", "tool": getattr(part, "tool_name", ""),
                      "summary": _short(getattr(part, "content", ""))})
            elif kind == "PartStartEvent":
                part = ev.part
                if getattr(part, "part_kind", "") == "text" and getattr(part, "content", ""):
                    emit({"event": "text", "delta": part.content})
            elif kind == "PartDeltaEvent":
                delta = ev.delta
                dk = getattr(delta, "part_delta_kind", "")
                content = getattr(delta, "content_delta", "")
                if not content:
                    continue
                if dk == "thinking":
                    emit({"event": "thinking", "delta": content})
                else:
                    emit({"event": "text", "delta": content})
        except Exception as e:
            log(f"[stream] {kind}: {e}")


# ── Session runtime ──────────────────────────────────────────────────────────

class AgentRuntime:
    def __init__(self):
        self.session = None
        self.agent = None
        self.model = None
        self.deps = None
        self.history = []        # list[ModelMessage] — conversation memory
        self.session_id = None   # persisted session this runtime is attached to
        self.project_id = None
        self.max_tokens = AGENT_MAX_TOKENS   # effective per-turn output cap (set in init)
        self.session_tokens = {"input": 0, "output": 0, "total": 0}  # cumulative token usage
        self.ready_info = None   # the 'ready' payload, re-emitted on reattach
        self.ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config = {}
        try:
            with open(os.path.join(self.ats_root, "config.json"), encoding="utf-8") as f:
                self.config = json.load(f)
        except Exception:
            pass

    async def init(self, cmd):
        provider = cmd.get("provider")
        try:
            model, pname, model_name = build_model(self.config, provider)
        except Exception as e:
            emit({"event": "error", "message": str(e)})
            return
        self.model = model

        project_id = cmd.get("project_id") or project_store.get_active_project_id(self.ats_root)
        project = project_store.get_project(self.ats_root, project_id) if project_id else None
        self.project_id = project_id

        # Resolve the persisted session: resume an existing one (we will load its model
        # history below) or create a fresh record. The live browser is always relaunched —
        # it cannot be serialized — so a resumed session reopens the app from scratch.
        session_id = cmd.get("session_id")
        resumed = bool(session_id and agent_sessions.read_session(self.ats_root, project_id, session_id))
        if resumed:
            self.session_id = session_id
        else:
            meta = agent_sessions.create_session(
                self.ats_root, project_id, title=cmd.get("title") or "",
                provider=pname, model=model_name, env=(cmd.get("env") or ""),
                headed=bool(cmd.get("headed")), session_id=session_id)
            self.session_id = meta["id"]

        env = cmd.get("env")
        base_url = project_store.resolve_base_url(project, env) if project else ""
        start_path = cmd.get("start_path") or ""
        start_url = (base_url.rstrip("/") + "/" + start_path.lstrip("/")) if (base_url and start_path) else base_url

        ss = project_store.storage_state_path(self.ats_root, project_id) if project_id else None
        load_state = ss if (ss and os.path.exists(ss)) else None

        # Credentials for one-time auto-login (saved as storage_state, then reused).
        idx = int(cmd.get("seller_index") or os.environ.get("ATS_SELLER_USER_INDEX") or 0)
        creds = _resolve_credentials(project, self.config, cmd.get("platform") or "seller", idx)

        headless = not cmd.get("headed", False)
        self.session = BrowserSession()
        try:
            info = await _bro(self.session.start, start_url, load_state, headless, creds, ss)
        except Exception as e:
            emit({"event": "error", "message": f"browser failed to start: {e}"})
            return

        prov_cfg = ((self.config.get("llm", {}) or {}).get("providers", {}) or {}).get(pname, {}) or {}
        self.max_tokens = int(prov_cfg.get("max_tokens") or AGENT_MAX_TOKENS)
        self.agent = build_agent(model, self.max_tokens)
        # Vision-capable? -> run_test_case can hand failure SCREENSHOTS back to the model.
        _vis = (os.environ.get("ATS_AGENT_VISION") or "").lower()
        vision = (_vis == "on") if _vis in ("on", "off") else (pname in ("mimo", "openai"))
        ctx_dir = project_store.resolve_context_dir(self.ats_root, project_id, project)
        mem_path = project_store.resolve_memory_path(self.ats_root, project_id)
        self.deps = Deps(session=self.session, ats_root=self.ats_root, project=project,
                         config=self.config, context_dir=ctx_dir, memory_path=mem_path, vision=vision)
        # Restore the model's conversation memory on resume (this is what lets it "continue
        # from a point"); a fresh session starts empty. Then mark the session running and load
        # the saved transcript so the window can redraw the visible chat.
        self.history = agent_sessions.load_messages(self.ats_root, project_id, self.session_id) if resumed else []
        if resumed:
            _t = (agent_sessions.read_session(self.ats_root, project_id, self.session_id) or {}).get("tokens") or {}
            self.session_tokens = {"input": int(_t.get("input", 0) or 0),
                                   "output": int(_t.get("output", 0) or 0),
                                   "total": int(_t.get("total", 0) or 0)}
        else:
            self.session_tokens = {"input": 0, "output": 0, "total": 0}
        try:
            agent_sessions.update_session_meta(self.ats_root, project_id, self.session_id,
                {"status": "running", "provider": pname, "model": model_name, "env": (cmd.get("env") or "")})
        except Exception:
            pass
        transcript = agent_sessions.load_transcript(self.ats_root, project_id, self.session_id) if resumed else []
        authed = info.get("authed", "none")
        self.ready_info = {"url": info.get("url", ""), "title": info.get("title", ""),
                           "provider": pname, "model": model_name,
                           "auth": authed != "none", "auth_via": authed,
                           "project": (project or {}).get("name") or project_id or None,
                           "project_id": project_id, "session_id": self.session_id}
        emit({"event": "ready", **self.ready_info, "resumed": resumed,
              "transcript": transcript, "tokens": self.session_tokens})

    async def chat(self, cmd):
        if not self.agent:
            emit({"event": "error", "message": "agent not initialized — send {action:'init'} first"})
            return
        message = (cmd.get("message") or "").strip()
        attachments = cmd.get("attachments") or []
        if not message and not attachments:
            emit({"event": "error", "message": "empty message"})
            return
        attachment_names = [name for _p, name in _attachment_paths(attachments)]
        # Inline document text + attach images as multimodal parts. Falls back to a
        # plain string when there are no binary parts (so text-only models are unaffected).
        prompt = _build_user_prompt(
            message or "Use the attached file(s) as context for the app under test.",
            attachments)
        hist_before = len(self.history)
        with capture_run_messages() as messages:
            try:
                result = await self.agent.run(
                    prompt, deps=self.deps, message_history=self.history,
                    event_stream_handler=_stream_handler,
                    usage_limits=UsageLimits(tool_calls_limit=TOOL_BUDGET))
                self.history = result.all_messages()
                self._add_usage(result)
                self._persist_turn(message, attachment_names, result.new_messages())
                emit({"event": "turn_complete", "text": result.output})
            except UsageLimitExceeded:
                # Hit the per-turn action budget. Keep the partial history so "continue"
                # has context, but TRIM the trailing un-executed tool call (else
                # pydantic-ai rejects the next prompt), then summarize findings.
                if messages:
                    self.history = _trim_dangling_tool_calls(messages)
                summary = await self._summarize_after_budget()
                extra = [{"role": "assistant", "text": summary}] if summary else None
                self._persist_turn(message, attachment_names, self.history[hist_before:], extra_bubbles=extra)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                low = str(e).lower()
                if "token limit" in low or "max_tokens" in low:
                    if self.max_tokens and self.max_tokens > 0:
                        msg += (f"  (Hit the per-turn output cap of {self.max_tokens}. Raise it via "
                                f"ATS_AGENT_MAX_TOKENS / a higher 'max_tokens' for this provider in config.json, "
                                f"or set it to 0 to uncap, then Stop + restart the session.)")
                    else:
                        msg += ("  (Hit the model's OWN output-token limit on a single turn — separate from its "
                                "much larger context window. Break the request into smaller turns, or use a "
                                "provider/model that allows more output per response.)")
                emit({"event": "error", "message": msg})
                log(traceback.format_exc())

    async def reattach(self):
        """Re-emit `ready` (+ current transcript + token total) so a reopened window/dock reconnects
        to this STILL-RUNNING session without restarting its browser or losing memory."""
        if not self.session_id or not self.ready_info:
            emit({"event": "error", "message": "no active session to reattach"})
            return
        transcript = agent_sessions.load_transcript(self.ats_root, self.project_id, self.session_id)
        emit({"event": "ready", **self.ready_info, "resumed": True,
              "transcript": transcript, "tokens": self.session_tokens})

    def _add_usage(self, result):
        """Accumulate this turn's token usage into the session total and emit a live counter
        ({event:'usage', input, output, total, estimated} — cumulative for the session). If the
        provider reports no usage (some OpenAI-compatible endpoints omit it), fall back to a rough
        ~4-chars/token estimate so the counter still moves instead of being stuck at 0."""
        inp = out = tot = 0
        try:
            u = result.usage()
            inp = int(getattr(u, "input_tokens", 0) or 0)
            out = int(getattr(u, "output_tokens", 0) or 0)
            tot = int(getattr(u, "total_tokens", 0) or 0)
        except Exception:
            pass
        estimated = bool(self.session_tokens.get("estimated"))
        if tot <= 0:
            try:
                chars = sum(len(str(getattr(pt, "content", "") or ""))
                            for m in result.new_messages() for pt in getattr(m, "parts", []))
            except Exception:
                chars = len(str(getattr(result, "output", "") or ""))
            out = max(out, max(1, chars // 4))
            tot = inp + out
            estimated = True
        self.session_tokens["input"] += inp
        self.session_tokens["output"] += out
        self.session_tokens["total"] += tot
        self.session_tokens["estimated"] = estimated
        emit({"event": "usage", **self.session_tokens})

    def _persist_turn(self, message, attachment_names, new_msgs, extra_bubbles=None):
        """Persist one completed turn: append its bubbles to the transcript, save the model
        history (for resume), and bump session meta (count, title-on-first-turn). Never raises
        — a disk hiccup must not fail a live turn."""
        if not self.session_id:
            return
        try:
            bubbles = [agent_sessions.user_bubble(message, attachment_names)]
            bubbles += agent_sessions.bubbles_from_messages(new_msgs, include_user=False)
            if extra_bubbles:
                bubbles += extra_bubbles
            agent_sessions.append_bubbles(self.ats_root, self.project_id, self.session_id, bubbles)
            agent_sessions.save_messages(self.ats_root, self.project_id, self.session_id, self.history)
            patch = {"message_count": len(self.history), "status": "idle", "tokens": dict(self.session_tokens)}
            meta = agent_sessions.read_session(self.ats_root, self.project_id, self.session_id) or {}
            title = (meta.get("title") or "").strip()
            if message and (not title or title == "New session"):
                patch["title"] = message.strip()[:80]
            agent_sessions.update_session_meta(self.ats_root, self.project_id, self.session_id, patch)
        except Exception as e:
            log(f"[agent] persist failed: {e}")

    async def _summarize_after_budget(self):
        """After the tool budget is hit, produce a findings summary (a tool-less run
        over the preserved history) so the exploration isn't lost, then invite the
        user to continue. Falls back to a plain pause note if summarizing fails."""
        summary = ""
        try:
            s_agent = Agent(self.model, system_prompt=(
                "You are wrapping up a browser-exploration session. You have NO tools and CANNOT take "
                "any further action. From the tool results already in the conversation, reply with ONLY "
                "a concise plain-text summary of what was found (pages, key elements/actions, any "
                "breakage) plus concrete suggestions for what to explore or test next. NEVER output "
                "<tool_call>, <function>, or any function-call syntax."))
            sr = await s_agent.run("Summarize what you found so far and suggest next steps.",
                                   message_history=list(self.history),
                                   usage_limits=UsageLimits(request_limit=3))
            summary = (sr.output or "").strip()
            # MiMo sometimes leaks a raw <tool_call> text token (it 'wants' to act); strip it.
            summary = re.sub(r"<tool_call>.*?</tool_call>", "", summary, flags=re.DOTALL)
            summary = re.sub(r"</?function[^>]*>", "", summary).strip()
            if len(summary) < 40:
                summary = ""
        except Exception as e:
            log(f"[agent] post-budget summary failed: {e}")
        note = (f"(Paused after {TOOL_BUDGET} exploration steps — my per-turn budget. I've kept "
                f"everything I found, so just say \"continue\" to keep going, or tell me what to focus on.)")
        emit({"event": "turn_complete", "text": (summary + "\n\n" + note) if summary else note})
        return summary

    def reset(self):
        self.history = []
        if self.session:
            self.session.steps = []
            self.session.assertions = []
            self.session.step_id = 0
            self.session.input_counter = 0
        if self.session_id:
            try:
                agent_sessions.clear_session(self.ats_root, self.project_id, self.session_id)
            except Exception:
                pass
        emit({"event": "reset_ok"})

    async def shutdown(self):
        if self.session_id:
            try:
                agent_sessions.update_session_meta(
                    self.ats_root, self.project_id, self.session_id, {"status": "idle"})
            except Exception:
                pass
        if self.session:
            await _bro(self.session.close)
        emit({"event": "bye"})


# ── stdin reader -> asyncio queue ────────────────────────────────────────────

def _spawn_stdin_reader(loop, queue):
    def reader():
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)  # EOF sentinel
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return t


async def main_loop():
    rt = AgentRuntime()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _spawn_stdin_reader(loop, queue)
    emit({"event": "started"})

    while True:
        line = await queue.get()
        if line is None:           # stdin closed
            break
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except Exception:
            emit({"event": "error", "message": f"bad JSON: {line[:120]}"})
            continue

        action = cmd.get("action")
        if action == "init":
            await rt.init(cmd)
        elif action == "chat":
            await rt.chat(cmd)
        elif action == "reattach":
            await rt.reattach()
        elif action == "reset":
            rt.reset()
        elif action in ("shutdown", "quit", "exit"):
            await rt.shutdown()
            break
        elif action == "ping":
            emit({"event": "pong"})
        else:
            emit({"event": "error", "message": f"unknown action: {action!r}"})

    try:
        await rt.shutdown()
    except Exception:
        pass


# ── Offline self-test (no browser, no network): proves tool wiring ───────────

def _selftest():
    import tempfile
    from pydantic_ai.models.test import TestModel
    agent = build_agent(TestModel())
    sess = BrowserSession()
    # Isolate file writes: TestModel calls EVERY tool with dummy args, incl.
    # update_setting and update_memory — point ats_root (and the context/memory
    # paths) at a throwaway dir so the real config.json / memory are never touched.
    root = tempfile.mkdtemp(prefix="ats_selftest_")
    deps = Deps(session=sess, ats_root=root, project=None, config={},
                context_dir=project_store.resolve_context_dir(root, None),
                memory_path=project_store.resolve_memory_path(root, None))
    tools = sorted(agent._function_toolset.tools.keys()) if hasattr(agent, "_function_toolset") else []
    print("agent built OK; tools:", tools)

    async def go():
        seen = []

        async def handler(ctx, stream):
            async for ev in stream:
                seen.append(type(ev).__name__)
        # TestModel calls every tool with dummy args; our tool bodies marshal to
        # the browser thread where session.page is None -> they return error dicts
        # (not exceptions), which is exactly the graceful path we want to verify.
        r = await agent.run("hello", deps=deps, event_stream_handler=handler)
        print("run output:", _short(r.output, 200))
        print("events:", seen[:12])
        print("messages:", len(r.all_messages()))
    asyncio.run(go())
    print("SELFTEST OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        try:
            asyncio.run(main_loop())
        except KeyboardInterrupt:
            pass
