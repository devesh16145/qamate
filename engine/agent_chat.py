"""
QAmate — Conversational Agent (Pydantic AI)
==============================================

The autonomous explorer/test-author turned into a real **agent you can chat
with**: it keeps conversation memory, calls tools, drives a live browser, and
iterates with you — the Claude-Code loop, embedded in the product.

This replaces the fire-and-forget loop in `agent_recorder.py` (one LLM call =
one action, no memory, no chat) with a persistent Pydantic AI `Agent` whose
tools wrap the engine functions we already have:

    navigate / observe / click / fill / select_option        -> drive the app
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
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
import agent_sessions
import input_registry as _ireg
from app_explorer import _comprehensive_snapshot, element_to_model, _label, _LINKS_JS, _should_skip_link, _slug
from normalizer import disambiguate, infer_hints, match_option, compact_elements
from provenance import ProvenanceTracker, settings_values, registry_values
from dom_inspector import DESTRUCTIVE_KEYWORDS, _normalize_url
import llm as _llm
from smart_locator import smart_locator, SelfHealError, to_locator
from recorder_parser import generate_from_review
from agent_recorder import _locator_str, _q  # reuse the proven codegen helpers
from goal_controller import GoalAction, execute_bounded
from decision import ChoiceDecider
from action_guard import ActionGuard, GoalHandoffGuard
from recording_history import RecordingHistory, clear_recording as clear_saved_recording
from interaction_context import attach_interaction_context, decision_page

from pydantic_ai import Agent, RunContext, capture_run_messages, BinaryContent, ToolReturn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

# Code root (engine/ + tests/conftest.py). ATS_ROOT is the data root; they differ only
# in a packaged macOS app, where the code is read-only (see bootstrap.js).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Default max tool calls per chat turn. 0 = unlimited (no pause).
# Overridden per-session via the init command's tool_budget field or ATS_AGENT_TOOL_BUDGET env var.
TOOL_BUDGET_DEFAULT = int(os.environ.get("ATS_AGENT_TOOL_BUDGET") or 30)

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


# ── Context compaction ────────────────────────────────────────────────────────
# Long sessions replay the whole history every turn. Browser percepts (observe
# payloads, aria trees, run logs) dominate the tokens but go STALE immediately —
# the agent re-observes rather than re-reading them, so old copies are dead
# weight that slows turns and degrades the model's decisions (4M+ token turns
# were observed on autonomous runs).

_COMPACT_KEEP_RECENT = 12     # most recent messages stay verbatim (current task context)
_COMPACT_MAX_CHARS = 1200     # what an OLD tool result may keep
_PERCEPT_TOOLS = {"observe", "execute_goal", "multi_app_observe"}


def _is_percept(part):
    return isinstance(part, ToolReturnPart) and (part.tool_name in _PERCEPT_TOOLS or
        (part.tool_name in {"multi_app_execute_goal", "multi_app_act", "multi_app_check"} and isinstance(part.content, dict)
         and isinstance(part.content.get("observation"), dict)))


def _multi_app_prompt(messages):
    import dataclasses
    from planner_policy import HYBRID_SYSTEM, MULTI_APP_SYSTEM
    return [dataclasses.replace(m, parts=[dataclasses.replace(p, content=MULTI_APP_SYSTEM)
            if getattr(p, "part_kind", "") == "system-prompt" and p.content == HYBRID_SYSTEM else p
            for p in m.parts]) for m in messages]


def _plan_snapshot(part):
    return (isinstance(part, ToolReturnPart) and part.tool_name in {"set_plan", "update_plan"}
            and isinstance(part.content, dict) and part.content.get("ok") is True
            and (isinstance(part.content.get("plan"), list)
                 or (part.tool_name == "set_plan" and isinstance(part.content.get("steps"), int))))


def _plan_failure(part):
    # Keep past blocked/skipped milestones even when a newer plan no longer lists them.
    return (isinstance(part, ToolReturnPart) and part.tool_name in {"set_plan", "update_plan"}
            and (not isinstance(part.content, dict) or part.content.get("ok") is not True
                 or any(tag in str(part.content.get("plan", [])) for tag in ("[failed]", "[skipped]"))))


def _compact_plans(messages):
    """Superseded successful checklist snapshots only; preserve calls and originals."""
    import dataclasses
    latest = next((p for m in reversed(messages) for p in reversed(m.parts) if _plan_snapshot(p)), None)
    out = []
    for message in messages:
        parts = [dataclasses.replace(p, content={"ok": True, "superseded_plan": True})
                 if _plan_snapshot(p) and p is not latest and not _plan_failure(p) else p for p in message.parts]
        out.append(dataclasses.replace(message, parts=parts))
    return out


def _compact_history(messages, keep_recent=_COMPACT_KEEP_RECENT,
                     max_chars=_COMPACT_MAX_CHARS, preserve_last_percept=False):
    """Shrink stale tool results in the model history. Structure is never touched
    (tool_call_id pairing stays intact) — only the CONTENT of old ToolReturnParts
    is truncated, with a hint to re-call the tool for fresh data. Idempotent."""
    import dataclasses
    msgs = list(messages)
    last_percept = next((p for m in reversed(msgs) for p in reversed(getattr(m, "parts", []))
                         if _is_percept(p)), None) if preserve_last_percept else None
    last_plan = next((p for m in reversed(msgs) for p in reversed(m.parts) if _plan_snapshot(p)), None) if preserve_last_percept else None
    if len(msgs) <= keep_recent:
        return msgs
    cutoff = len(msgs) - keep_recent
    out = []
    for i, m in enumerate(msgs):
        if i >= cutoff or not isinstance(m, ModelRequest):
            out.append(m)
            continue
        parts, changed = [], False
        for p in getattr(m, "parts", []):
            if (isinstance(p, ToolReturnPart) and p is not last_percept and p is not last_plan
                    and not (preserve_last_percept and (_plan_failure(p) or
                        isinstance(p.content, dict) and p.content.get("stale_observation")))):
                c = p.content
                if isinstance(c, str):
                    s = c
                else:
                    try:
                        s = json.dumps(c, ensure_ascii=True, default=str)
                    except Exception:
                        s = str(c)
                if len(s) > max_chars:
                    stub = (s[:max_chars] +
                            f" ...[compacted {len(s)} chars of stale data - call the "
                            f"tool again if you need it fresh]")
                    parts.append(dataclasses.replace(p, content=stub))
                    changed = True
                    continue
            parts.append(p)
        out.append(dataclasses.replace(m, parts=parts) if changed else m)
    return out


def _compact_percepts(messages):
    """Keep the latest observation intact; obsolete refs must not dominate context."""
    import dataclasses
    latest = None
    for i, m in enumerate(messages):
        for j, p in enumerate(getattr(m, "parts", [])):
            if _is_percept(p):
                latest = (i, j)
    out = []
    for i, m in enumerate(messages):
        parts = []
        changed = False
        for j, p in enumerate(getattr(m, "parts", [])):
            if _is_percept(p) and (i, j) != latest:
                content = p.content
                summary = {"stale_observation": True, "note": "Use the latest observation for refs"}
                if isinstance(content, dict):
                    summary.update({k: content[k] for k in ("ok", "status", "url", "app", "actor", "checkpoints", "checks", "trace", "error", "detail", "note", "tainted", "verified", "execution_state", "attempted_actions", "completed_actions", "capture", "op", "recorded_steps") if k in content})
                p = dataclasses.replace(p, content=summary)
                changed = True
            parts.append(p)
        out.append(dataclasses.replace(m, parts=parts) if changed else m)
    return out


def planner_request_metrics(response):
    """Token and reasoning figures for one planner response (None when unreported)."""
    from pydantic_ai.messages import ThinkingPart
    usage = getattr(response, "usage", None)
    details = dict(getattr(usage, "details", None) or {})
    reasoning = details.get("reasoning_tokens")
    return {
        "input": getattr(usage, "input_tokens", None),
        "output": getattr(usage, "output_tokens", None),
        "reasoning_tokens": reasoning,
        "thinking_chars": sum(len(p.content or "") for p in response.parts if isinstance(p, ThinkingPart)),
        "tool_calls": sum(1 for p in response.parts if isinstance(p, ToolCallPart)),
    }


class TurnCompactingModel(WrapperModel):
    """Caps WITHIN-TURN context growth. Every model request re-sends the whole
    turn transcript, so long authoring turns grow quadratically — run-5
    BENCH-005 hit 9.0M tokens at 142 calls (~64k/request) and the bloat also
    slows every round (run-4/5 BENCH-007 wall-clock timeouts). prepare_messages
    shrinks STALE tool returns in the OUTGOING copy only (same _compact_history
    used between turns: structure intact, recent messages verbatim); the
    runner's own history is never mutated."""

    # Rough constant for what rides on EVERY request outside the message list:
    # system prompt incl. playbook (~5.4k) + tool schemas/docstrings (~4.2k) +
    # AGENT_MEMORY (~2.8k) + framing — measured 2026-06-11.
    _SYS_OVERHEAD_EST = 15000

    def __init__(self, wrapped, *, compact=False, multi_app_active=None):
        super().__init__(wrapped)
        self.compact = compact
        self.multi_app_active = multi_app_active

    def _report_context(self, messages, parameters):
        from prompt_metrics import context_metrics
        emit({"event": "planner_context", **context_metrics(messages, parameters)})

    @staticmethod
    def _report_request(started, response):
        """Per-request planner timing (same event the decision model emits), so a run
        shows how much wall time is model thinking vs browser/tool work."""
        try:
            emit({"event": "model_usage", "role": "planner", **planner_request_metrics(response),
                  "duration_ms": round((time.monotonic() - started) * 1000)})
        except Exception:
            pass

    async def request(self, messages, model_settings, model_request_parameters):
        self._report_context(messages, model_request_parameters)
        started = time.monotonic()
        response = await super().request(messages, model_settings, model_request_parameters)
        self._report_request(started, response)
        return response

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        self._report_context(messages, model_request_parameters)
        started = time.monotonic()
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
            yield stream
        self._report_request(started, stream.get())

    def prepare_messages(self, messages):
        if self.compact and self.multi_app_active is not None and self.multi_app_active():
            messages = _multi_app_prompt(messages)
        out = _compact_plans(_compact_percepts(messages)) if self.compact else messages
        out = _compact_history(out, keep_recent=4 if self.compact else 12, max_chars=600,
                               preserve_last_percept=self.compact)
        # Cost telemetry that SURVIVES killed turns: provider usage only lands when
        # a turn completes, so 40-minute timeout tasks reported ZERO tokens (nine
        # such tasks hid an estimated 100M+ real spend). Estimate the outgoing
        # context per request (chars/4 + fixed overhead, INPUT side only) and emit
        # it; consumers use the estimate when real usage never arrives.
        try:
            est = self._SYS_OVERHEAD_EST + sum(
                len(str(getattr(p, "content", "") or ""))
                for m in out for p in getattr(m, "parts", [])) // 4
            self._req_count = getattr(self, "_req_count", 0) + 1
            self._input_est = getattr(self, "_input_est", 0) + est
            emit({"event": "usage_est", "requests": self._req_count,
                  "input_est_total": self._input_est, "last_request_est": est})
        except Exception:
            pass
        return super().prepare_messages(out)


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


def _build_user_prompt(message, attachments, vision=False):
    """Assemble the prompt for agent.run(). Returns a plain str when there are no
    binary (image/PDF) parts, else a list [text, BinaryContent, ...] — the shape
    pydantic-ai accepts for multimodal input. Docs are inlined as labeled text blocks;
    images/scanned-PDFs are appended as binary parts only when vision=True — otherwise
    they are noted as skipped so text-only/non-vision endpoints never receive image data."""
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
            if not vision:
                notes.append(f"[image '{name}' not sent — model does not support image input]")
                continue
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
    def execute():
        owner = getattr(fn, "__self__", None)
        if getattr(owner, "_multi_app_recording", None) is not None and getattr(fn, "__name__", "") != "close":
            return {"ok": False, "status": "multi_app_active", "note": "Use the isolated multi_app_* recording tools"}
        return fn(*args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_BROWSER, execute)


_VISION_PROMPT = (
    "You are the eyes of a browser automation agent. Describe ONLY what is visually "
    "present on screen right now. Focus on: any open dropdown lists and ALL their visible "
    "options (quote exact text), modal dialogs and their content, form fields and their "
    "current values, buttons and their labels, any error or success messages. "
    "Be concise and literal — no inference, no guessing. "
    "Start with: 'Current screen shows:'"
)


async def _call_vision_oracle(config: dict, screenshot_bytes: bytes,
                               question: str = "") -> str:
    """Async wrapper: dispatches a one-shot vision call to the configured vision model
    on the default executor (NOT the browser thread). Returns a text description.
    Falls back to a clear error string so callers can always include it in results."""
    from model_profiles import vision_unavailable_reason
    unavailable = vision_unavailable_reason(config)
    if unavailable:
        return f"(vision unavailable: {unavailable})"
    oracle_cfg = (config.get("vision_oracle") or {})
    vision_profile = ((config.get("llm") or {}).get("roles") or {}).get("vision")
    if vision_profile:
        from model_profiles import resolve_profile, require_capability
        try:
            _, oracle_cfg = resolve_profile(config, vision_profile, "vision")
            require_capability(oracle_cfg, "vision")
        except ValueError as exc:
            return f"(vision profile unavailable: {exc})"
    if not oracle_cfg or not oracle_cfg.get("model"):
        return ("(vision oracle not configured — add 'vision_oracle' block with "
                "'model','base_url','api_key_env' to config.json)")
    q = question or _VISION_PROMPT
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: _llm.call_vision_oracle(oracle_cfg, screenshot_bytes, q))
    except _llm.LLMNotConfigured as e:
        return f"(vision oracle key missing: {e})"
    except Exception as e:
        if getattr(e, "status_code", None) in {400, 401, 402, 403, 404, 422}:
            config["_vision_runtime_unavailable"] = True
        return f"(vision oracle error: {type(e).__name__}: {str(e)[:200]})"


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
    """Locate failure artifacts from a verify run's screenshots/ dir.
    Returns (screenshot_path|None, err_text|None, page_text|None).
    conftest writes: *_FAILED.png, *_ERRORS.txt (filtered errors), *_FAILED_TEXT.txt (full body text)."""
    import glob
    shot, err, page_text = None, None, None
    sdir = os.path.join(results_dir or "", "screenshots")
    try:
        pngs = sorted(glob.glob(os.path.join(sdir, "*_FAILED.png")), key=os.path.getmtime, reverse=True)
        if pngs:
            shot = pngs[0]
        txts = sorted(glob.glob(os.path.join(sdir, "*_ERRORS.txt")), key=os.path.getmtime, reverse=True)
        if txts:
            with open(txts[0], "r", encoding="utf-8", errors="replace") as f:
                err = f.read()
        # Full page text saved by conftest for non-vision models (replaces OCR)
        ptxts = sorted(glob.glob(os.path.join(sdir, "*_FAILED_TEXT.txt")), key=os.path.getmtime, reverse=True)
        if ptxts:
            with open(ptxts[0], "r", encoding="utf-8", errors="replace") as f:
                page_text = f.read()[:4000]  # cap to keep tokens reasonable
    except Exception:
        pass
    return shot, err, page_text


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


def _tests_root_for(ats_root, project):
    """The tests root for the current target: a project's OWN suite when it has one
    (projects/<id>/tests exists), else the legacy shared <ats_root>/tests suite.
    Pre-suite projects (the original 'test' agent project) keep authoring into the
    legacy suite — behavior unchanged until a project is explicitly given a suite."""
    pid = (project or {}).get("id") if isinstance(project, dict) else None
    try:
        return project_store.resolve_tests_root(ats_root, pid)
    except Exception:
        return os.path.join(ats_root, "tests")


def _delete_test_case(ats_root, flow_id, tc_id, tests_dir=None):
    """Remove a test case from a flow: its test_cases.json entry, its test_data.json key, and the
    @pytest.mark.tc/def block in test_<flow>.py. Mirrors main.js's delete-test handler."""
    flow_dir = os.path.join(tests_dir or os.path.join(ats_root, "tests"), "flows", flow_id)
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


# ARIA roles we treat as interactive during inspect hybrid augmentation.
_ARIA_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio",
    "spinbutton", "slider", "searchbox", "switch", "listbox", "option",
    "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "treeitem",
}


def _parse_aria_snapshot(snap: str) -> list:
    """Parse Playwright aria_snapshot() YAML output into flat {role, name} dicts.
    Only returns elements whose role is in _ARIA_INTERACTIVE_ROLES."""
    elements = []
    for line in snap.splitlines():
        s = line.strip()
        m = re.match(r'^-\s+(\w[\w-]*)\s+"([^"]*)"', s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and role in _ARIA_INTERACTIVE_ROLES:
                elements.append({"role": role, "name": name})
    return elements


def _aria_el_to_model(role: str, name: str, idx: int) -> dict:
    """Build a minimal element model from an aria snapshot element.
    Primary locator is get_by_role(role, name=name) — stable across DOM reshuffles."""
    ref = _slug(name, role) if name else f"{role}-{idx}"
    return {
        "ref": ref,
        "tag": "",
        "role": role,
        "name": name,
        "placeholder": "",
        "input_type": "",
        "required": False,
        "disabled": False,
        "visible": True,
        "hidden_reason": "",
        "broken": False,
        "primary": {"by": "role", "role": role, "name": name},
        "fallbacks": [{"by": "text", "value": name}],
        "fingerprint": {"tag": "", "role": role, "name": name, "text": name},
        "test_id": "",
        "source": "aria",
    }


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
        self._action_guard = ActionGuard()
        self.steps = []         # recorded steps -> generate_from_review
        self.assertions = []    # recorded checkpoints
        self.step_id = 0
        self.input_counter = 0
        self.assumptions = []   # auto-mode values with no provenance (review queue)
        self.skips = []         # explicit skip_step events (auditable)
        self.recording_history = RecordingHistory()
        self._multi_app_recording = None
        self.issues = []        # breakage: console errors / JS exceptions / failed requests
        self._auth_failures = []  # 401/403 responses since last navigate (reset each navigate)
        self._api_errors = []   # 400-499 non-auth responses since last navigate (reset each navigate)
        self._start_args = None  # remembered so restart() can relaunch after a crash/close

    # -- lifecycle -----------------------------------------------------------
    def start(self, start_url="", storage_state=None, headless=True, credentials=None,
              save_state_path=None, record_first_step=True, extra_init_states=None,
              browser_config=None):
        """Launch the browser, reuse a saved login if present, and — if we still
        land on a login form — log in ONCE from configured credentials and persist
        a fresh storage_state for next time (so the agent never has to log in by
        hand). Returns {url, title, authed} where authed is
        'storage_state' | 'login' | 'session' | 'none'.

        extra_init_states: list of storage-state dicts (origins/localStorage) to
        inject via add_init_script so e.g. the admin panel JWT is pre-loaded without
        a separate login step."""
        # Remember how we were started so restart() can relaunch identically after a crash.
        self._start_args = dict(start_url=start_url, storage_state=storage_state,
                                headless=headless, credentials=credentials,
                                save_state_path=save_state_path,
                                extra_init_states=extra_init_states,
                                browser_config=browser_config)
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=headless, channel="chrome", args=["--disable-gpu", "--no-sandbox"])
        # Match the user's real Chrome so fixed/floating elements render correctly.
        # Headed: no_viewport=True lets Playwright use the actual OS window size (same as conftest.py).
        # Headless: use explicit viewport+DPR from config.json "browser" section (no real window exists).
        _bcfg = browser_config or {}
        _dpr = float(_bcfg.get("device_scale_factor", 1.0))
        ctx_args = {}
        if not headless:
            # no_viewport lets Playwright use the actual OS window size (same as conftest.py).
            # device_scale_factor is incompatible with no_viewport — the real window handles DPR.
            ctx_args["no_viewport"] = True
        else:
            _vp = _bcfg.get("viewport", {})
            ctx_args["viewport"] = {
                "width": int(_vp.get("width", 1280)),
                "height": int(_vp.get("height", 720)),
            }
            if _dpr != 1.0:
                ctx_args["device_scale_factor"] = _dpr
        if storage_state:
            ctx_args["storage_state"] = storage_state
        # Record the agent's OWN navigation for post-hoc analysis (what did it
        # actually do for 40 minutes?). Opt-in: ATS_AGENT_VIDEO_DIR points at the
        # destination (the bench sets it per task; the UI can set it per session).
        # Videos finalize on context close; hash-named .webm files land in the dir.
        self._video_dir = (os.environ.get("ATS_AGENT_VIDEO_DIR") or "").strip() or None
        if self._video_dir:
            try:
                os.makedirs(self._video_dir, exist_ok=True)
                ctx_args["record_video_dir"] = self._video_dir
                if headless:
                    ctx_args["record_video_size"] = dict(ctx_args.get("viewport") or
                                                         {"width": 1280, "height": 720})
                log(f"[agent] navigation video -> {self._video_dir}")
            except Exception as e:
                log(f"[agent] video recording disabled: {e}")
                self._video_dir = None
        self.context = self.browser.new_context(**ctx_args)
        # Pre-inject localStorage for additional origins (e.g. admin panel JWT auth).
        # add_init_script runs before the page's own scripts on every navigation,
        # so the token is already set when the SPA boots — no manual login needed.
        for state in (extra_init_states or []):
            for origin_data in (state.get("origins") or []):
                ls_items = origin_data.get("localStorage") or []
                if not ls_items:
                    continue
                origin = origin_data.get("origin", "")
                # Build a hostname check so we only inject on the right domain
                try:
                    from urllib.parse import urlparse
                    hostname = urlparse(origin).hostname or ""
                except Exception:
                    hostname = ""
                if not hostname:
                    continue
                ls_json = json.dumps(ls_items, ensure_ascii=True)
                script = (
                    f"if (window.location.origin === {json.dumps(origin)}) {{"
                    f"  try {{ const _i={ls_json};"
                    f"  _i.forEach(function(x){{localStorage.setItem(x.name,x.value);}});"
                    f"  }} catch(e) {{}} }}"
                )
                try:
                    self.context.add_init_script(script)
                    log(f"[agent] pre-loaded {len(ls_items)} localStorage items for {hostname}")
                except Exception as e:
                    log(f"[agent] could not add init script for {hostname}: {e}")
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.set_default_navigation_timeout(20000)
        self.page.on("console", lambda m: self._issue("console_error", m.text)
                     if getattr(m, "type", "") == "error" else None)
        self.page.on("pageerror", lambda e: self._issue("js_exception", e))
        self.page.on("requestfailed", lambda r: self._issue(
            "request_failed", f"{getattr(r, 'method', '')} {getattr(r, 'url', '')}"))
        # Track 4xx responses. 401/403 -> auth signal; 400/4xx -> api_errors surfaced in action results.
        def _on_response(resp):
            try:
                s = resp.status
                u = getattr(resp, "url", "")
                if u.endswith((".ico", ".png", ".gif", ".woff", ".woff2", ".css", ".js")):
                    return
                if s in (401, 403):
                    self._auth_failures.append(f"{s} {u[:120]}")
                elif 400 <= s < 500:
                    self._api_errors.append({"status": s, "url": u[:120]})
            except Exception:
                pass
        self.page.on("response", _on_response)

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
        if self._multi_app_recording is not None:
            self._multi_app_recording.close()
            self._multi_app_recording = None
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

    def show_browser(self):
        """Make the testing browser window visible. If currently headless, restarts in headed
        mode (same URL and session) so the user can watch what the agent is doing."""
        if not self._start_args:
            return {"ok": False, "error": "browser not started yet"}
        if not self._start_args.get("headless", True):
            return {"ok": True, "message": "Browser is already visible (headed mode).", "url": self._url()}
        # Switch from headless to headed by restarting with headless=False
        headed_args = {**self._start_args, "headless": False}
        try:
            self.close()
        except Exception:
            pass
        self.pw = self.browser = self.context = self.page = None
        self.by_ref = {}
        try:
            info = self.start(**headed_args, record_first_step=False)
        except Exception as e:
            return {"ok": False, "error": f"could not switch to headed mode: {str(e)[:160]}"}
        self._start_args["headless"] = False   # stay headed for future restarts this session
        return {"ok": True, "message": "Browser is now visible.", "url": info.get("url", "")}

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

    # Overlay detection + screenshot ─────────────────────────────────────────

    _OVERLAY_JS = """() => {
        const sel = [
            '[role="listbox"]', '[role="dialog"]', '[role="menu"]',
            '[role="option"]',  '[aria-haspopup][aria-expanded="true"]',
            '[data-radix-popper-content-wrapper]', '.select__menu',
            '[class*="dropdown"][class*="open"]', '[class*="popover"]'
        ].join(',');
        const el = document.querySelector(sel);
        return el ? true : false;
    }"""

    def overlay_opened(self) -> bool:
        """Fast post-click check: did a dropdown / dialog / menu appear?"""
        try:
            return bool(self.page.evaluate(self._OVERLAY_JS))
        except Exception:
            return False

    _OPTIONS_JS = """() => {
        const texts = [];

        // Tier 1: standard ARIA roles (MUI, Radix, Headless UI, Ant Design)
        for (const el of document.querySelectorAll('[role="option"]')) {
            const t = (el.textContent || '').trim();
            if (t) texts.push(t);
        }
        if (texts.length) return texts.slice(0, 30);

        // Tier 2: li inside a role=listbox/menu container
        for (const el of document.querySelectorAll(
                '[role="listbox"] li, [role="menu"] li, ul[class*="option"] li, ' +
                'ul[class*="menu"] li, ul[class*="dropdown"] li')) {
            const t = (el.textContent || '').trim();
            if (t) texts.push(t);
        }
        if (texts.length) return texts.slice(0, 30);

        // Tier 3: custom dropdown — scan absolutely/fixed positioned containers
        // that are currently visible and appeared near an input field.
        // Collect direct children that look like option rows (short, leaf-ish text).
        const active = document.activeElement;
        const inputRect = active ? active.getBoundingClientRect() : null;
        for (const el of document.querySelectorAll('div, ul')) {
            const style = window.getComputedStyle(el);
            const pos = style.position;
            if (pos !== 'absolute' && pos !== 'fixed') continue;
            if (el.offsetWidth < 50 || el.offsetHeight < 20) continue;
            // Must actually be rendered on top (some apps keep permanent
            // 'Please wait...' spinner divs positioned but covered/inert —
            // they are NOT open dropdowns).
            const cr = el.getBoundingClientRect();
            const topEl = document.elementFromPoint(
                Math.min(cr.x + cr.width / 2, window.innerWidth - 2),
                Math.min(cr.y + Math.min(cr.height / 2, 20), window.innerHeight - 2));
            if (!topEl || (!el.contains(topEl) && !topEl.contains(el))) continue;
            // Must be roughly below/near the active input
            if (inputRect) {
                const r = el.getBoundingClientRect();
                if (r.top < inputRect.top - 20) continue;  // above input — skip
            }
            // Unwrap wrapper chains first: with container > scroll-wrapper >
            // options, the wrapper's textContent is every option concatenated
            // into one blob (recorded as a single bogus "option" in run-5
            // BENCH-008 step 6). Only VISIBLE text children count — containers
            // often carry an extra hidden ghost/measure node that would
            // otherwise stop the descent at the wrapper level. Descend while
            // exactly one visible child carries text, then collect THAT level.
            const textKids = (node) => Array.from(node.children).filter(k => {
                if (!(k.textContent || '').trim()) return false;
                const kr = k.getBoundingClientRect();
                return kr.width > 0 && kr.height > 0;
            });
            let level = el;
            for (let d = 0; d < 10; d++) {   // wrapper chains run deep (portal>dialog>scroll>viewport>list)
                const kids = textKids(level);
                if (kids.length === 1 && kids[0].children.length) { level = kids[0]; continue; }
                break;
            }
            // Gather visible child text (skip pure-header rows that have no siblings)
            for (const child of textKids(level)) {
                const t = (child.textContent || '').trim();
                if (t && t.length > 0 && t.length < 120) texts.push(t);
            }
            if (texts.length) break;  // stop at first matching container
        }
        return [...new Set(texts)].slice(0, 30);
    }"""

    def _wait_for_options(self, timeout_ms: int = 3000) -> bool:
        """Wait up to timeout_ms for dropdown options to appear (ARIA or custom).
        Returns True if options appeared, False on timeout."""
        try:
            self.page.wait_for_selector(
                '[role="option"], [role="listbox"] li, [role="listbox"]',
                state="attached", timeout=timeout_ms)
            return True
        except Exception:
            pass
        # Also wait a fixed 1s for custom (non-ARIA) dropdowns — they may have
        # no sentinel element we can wait_for_selector on.
        try:
            self.page.wait_for_timeout(1000)
        except Exception:
            pass
        return False

    def _get_visible_options(self) -> list:
        """Synchronously read all visible option texts from any open dropdown/listbox."""
        try:
            return self.page.evaluate(self._OPTIONS_JS) or []
        except Exception:
            return []

    def get_options(self, filter_text: str = "") -> dict:
        """Read option texts from any open dropdown.
        Primary strategy: aria_snapshot (browser accessibility engine — finds custom
        components the DOM scraper misses). Falls back to JS DOM scan."""
        self._wait_for_options(3000)
        # Primary: aria_snapshot — the browser resolves roles even for custom divs
        try:
            snap = self.page.locator("body").aria_snapshot()
            opts = []
            for line in snap.splitlines():
                s = line.strip()
                # aria_snapshot uses YAML-like format: '- option "Name"'
                if s.startswith('- option "') and s.endswith('"'):
                    opts.append(s[len('- option "'):-1])
            if opts:
                if filter_text:
                    low = filter_text.lower()
                    opts = [o for o in opts if low in o.lower()]
                return {"ok": True, "count": len(opts), "options": opts, "source": "aria"}
        except Exception:
            pass
        # Fallback: JS DOM scan (Tier 3 sweep of positioned containers)
        opts = self._get_visible_options()
        if filter_text:
            low = filter_text.lower()
            opts = [o for o in opts if low in o.lower()]
        return {"ok": True, "count": len(opts), "options": opts, "source": "dom"}

    def _record_option_click(self, text: str, strategy: str):
        """Append the test step for a dropdown-option click so the generated test
        replays the selection. rawLines are emitted verbatim by generate_from_review."""
        if strategy == "role-option":
            raw = f'page.get_by_role("option").filter(has_text="{_q(text)}").first.click()'
        elif strategy == "listbox-child":
            raw = f"page.locator(\"[role='listbox'] *:has-text('{text}'), [role='menu'] *:has-text('{text}')\").first.click()"
        else:  # get_by_text / js-click / vision — replay by visible text
            raw = f'page.get_by_text("{_q(text)}").first.click()'
        self.step_id += 1
        self.steps.append({"id": self.step_id, "rawLine": raw,
                           "type": "click", "target": raw.rsplit(".click", 1)[0],
                           "targetDescription": f'Select option "{text}"',
                           "value": "", "varName": ""})

    def click_option_by_text(self, text: str, exact: bool = False, record: bool = False) -> dict:
        """Click a visible dropdown option by its text content.
        Searches the entire document including portal elements.
        record=True also appends a test step (used by the select_from_dropdown ladder)."""
        try:
            # Tier 1: role=option (MUI, Radix, standard)
            # Use filter(has_text=) first to handle decorated option text like
            # "VENDOR NAME(phone)FULL LEGAL NAME" when searching "VENDOR NAME"
            loc = self.page.get_by_role("option").filter(has_text=text)
            if loc.count() == 0 and exact:
                loc = self.page.get_by_role("option", name=text, exact=True)
            if loc.count() > 0:
                loc.first.click()
                self._settle()
                if record:
                    self._record_option_click(text, "role-option")
                return {"ok": True, "clicked": text, "url": self._url()}
            # Tier 2: any element inside a role=listbox/menu container
            loc2 = self.page.locator(
                f'[role="listbox"] *:has-text("{text}"), [role="menu"] *:has-text("{text}")'
            )
            if loc2.count() > 0:
                loc2.first.click()
                self._settle()
                if record:
                    self._record_option_click(text, "listbox-child")
                return {"ok": True, "clicked": text, "url": self._url(), "strategy": "listbox-child"}
            # Tier 3: get_by_text — finds any element by visible text, including
            # custom non-ARIA dropdown items (plain divs, custom components).
            # Prefer smaller elements (option rows) over large containers.
            loc3 = self.page.get_by_text(text, exact=exact)
            count3 = loc3.count()
            if count3 > 0:
                for i in range(min(count3, 8)):
                    item = loc3.nth(i)
                    try:
                        bb = item.bounding_box()
                        if bb and 10 < bb["height"] < 80:
                            item.click()
                            self._settle()
                            if record:
                                self._record_option_click(text, "get_by_text")
                            return {"ok": True, "clicked": text, "url": self._url(), "strategy": "get_by_text"}
                    except Exception:
                        continue
                # If all were too large (containers), click the first one anyway
                loc3.first.click()
                self._settle()
                if record:
                    self._record_option_click(text, "get_by_text")
                return {"ok": True, "clicked": text, "url": self._url(), "strategy": "get_by_text-first"}
            return {"ok": False,
                    "error": f"No element matching '{text}' found in any open dropdown."}
        except Exception as e:
            return {"ok": False, "error": f"click_option failed: {str(e)[:200]}"}

    def click_by_text_direct(self, text: str) -> dict:
        """Last-resort click: finds any visible element containing `text` and clicks it.
        Uses three strategies in order: get_by_text → text= selector → JS click.
        Works on completely custom components with no ARIA roles."""
        try:
            # Strategy 1: Playwright get_by_text, prefer option-row-sized elements
            loc = self.page.get_by_text(text, exact=False)
            count = loc.count()
            if count > 0:
                for i in range(min(count, 10)):
                    item = loc.nth(i)
                    try:
                        bb = item.bounding_box()
                        if bb and 10 < bb["height"] < 80:
                            item.click()
                            self._settle()
                            return {"ok": True, "clicked": text, "url": self._url(), "strategy": "get_by_text"}
                    except Exception:
                        continue

            # Strategy 2: Playwright text= selector
            loc2 = self.page.locator(f"text={text}")
            if loc2.count() > 0:
                loc2.first.click()
                self._settle()
                return {"ok": True, "clicked": text, "url": self._url(), "strategy": "text-selector"}

            # Strategy 3: JavaScript click — completely bypasses Playwright's
            # interactability checks. Finds the smallest visible element containing the text.
            search = text.lower()
            clicked = self.page.evaluate(f"""() => {{
                const needle = {json.dumps(search)};
                const all = Array.from(document.querySelectorAll('*'));
                const candidates = all.filter(el => {{
                    const t = (el.textContent || '').trim().toLowerCase();
                    return t.includes(needle) && el.offsetWidth > 0 && el.offsetHeight > 0
                           && el.offsetHeight < 100;
                }});
                // prefer the element whose text is closest in length to the needle
                candidates.sort((a, b) =>
                    Math.abs(a.textContent.trim().length - needle.length) -
                    Math.abs(b.textContent.trim().length - needle.length));
                if (candidates.length) {{ candidates[0].click(); return candidates[0].textContent.trim(); }}
                return null;
            }}""")
            if clicked:
                self._settle()
                return {"ok": True, "clicked": clicked, "url": self._url(), "strategy": "js-click"}

            return {"ok": False, "error": f"No visible element containing '{text}' found on page."}
        except Exception as e:
            return {"ok": False, "error": f"click_by_text failed: {str(e)[:200]}"}

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="png")

    def aria_snapshot_page(self, selector: str = "body") -> dict:
        """Return the ARIA accessibility tree for the page or a subtree.
        Uses Playwright's built-in accessibility engine — more complete than any
        DOM scraper, finds custom components and portals."""
        try:
            loc = self.page.locator(selector)
            if loc.count() == 0:
                return {"ok": False, "error": f"selector '{selector}' matched 0 elements"}
            tree = loc.first.aria_snapshot()
            return {"ok": True, "selector": selector, "tree": tree}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def mouse_click_coords(self, x: float, y: float) -> dict:
        """Click at pixel coordinates. Snaps to nearest interactive DOM element for precision.
        Vision gives approximate coords (±15px); elementFromPoint snaps to the real element."""
        before_api = len(self._api_errors)
        try:
            snapped = False
            try:
                handle = self.page.evaluate_handle(
                    """([x, y]) => {
                        const el = document.elementFromPoint(x, y);
                        if (!el) return null;
                        return el.closest('button, [role="button"], a, [data-testid], input, select, [tabindex]') || el;
                    }""",
                    [x, y]
                )
                el_handle = handle.as_element()
                if el_handle:
                    el_handle.click()
                    snapped = True
            except Exception:
                pass
            if not snapped:
                self.page.mouse.click(x, y)
            self._settle()
            result = {"ok": True, "x": x, "y": y, "url": self._url(), "dom_snapped": snapped}
            if self.overlay_opened():
                result["overlay_opened"] = True
            new_api = self._api_errors[before_api:]
            if new_api:
                result["api_errors"] = new_api
                dom_errors = self._scan_dom_errors()
                if dom_errors:
                    result["page_validation_errors"] = dom_errors
                else:
                    result["hint"] = "API error(s) detected — call scan_page_errors() to read any validation messages the page is showing."
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def _scan_dom_errors(self) -> list:
        """Scan visible validation errors from the DOM: aria-invalid fields, MUI helper text,
        role=alert elements. Returns a list of {type, text} dicts."""
        try:
            return self.page.evaluate("""() => {
                const seen = new Set(), errors = [];
                const add = (type, text) => {
                    const t = (text || '').trim();
                    if (t && t.length < 400 && !seen.has(t)) { seen.add(t); errors.push({type, text: t}); }
                };
                document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
                    const did = el.getAttribute('aria-describedby');
                    const msg = did ? ((document.getElementById(did) || {}).textContent || '') : '';
                    const label = el.getAttribute('placeholder') || el.getAttribute('name') || el.getAttribute('id') || 'field';
                    add('field_error', label + (msg ? ': ' + msg : ': invalid'));
                });
                document.querySelectorAll('.MuiFormHelperText-root.Mui-error').forEach(el => {
                    if (el.offsetParent) add('field_error', el.textContent);
                });
                document.querySelectorAll('[role="alert"]').forEach(el => {
                    if (el.offsetParent) add('alert', el.textContent);
                });
                document.querySelectorAll('.error-message, [class*="errorText"], [class*="error-text"], [class*="ErrorMessage"]').forEach(el => {
                    if (el.offsetParent) add('ui_error', el.textContent);
                });
                return errors;
            }""") or []
        except Exception:
            return []

    def scan_page_errors_dom(self) -> dict:
        """Public tool entry: scan the page for visible validation/API error messages."""
        errors = self._scan_dom_errors()
        return {"ok": True, "count": len(errors), "errors": errors,
                "hint": ("No visible validation errors found in the DOM. "
                         "The error may be in a toast that has already dismissed, or only in the network response body."
                         if not errors else None)}

    @staticmethod
    def _is_hidden_input_error(err: str) -> bool:
        """True when a click failed because the element is hidden or pointer-intercepted
        — the signal that we should retry with set_checked / force=True."""
        e = err.lower()
        return any(k in e for k in (
            "element is not visible", "is not visible", "intercepts pointer",
            "hidden", "not actionable", "covered by another element",
        ))

    def _smart_click(self, locator, loc_str: str, input_type: str = "", prefer_force: bool = False) -> str:
        """Click with automatic fallback for Tailwind hidden radio/checkbox inputs.
        Returns the rawLine string actually used (click vs set_checked vs force).
        prefer_force=True (from a 'force-click' hint) skips the doomed plain click.
        Raises on final failure so callers can surface the error."""
        if not prefer_force:
            try:
                locator.click()
                return f"{loc_str}.click()"
            except Exception as e:
                if not self._is_hidden_input_error(str(e)):
                    raise
        # Element is hidden or pointer-intercepted — use the right Playwright API.
        # set_checked is Playwright's dedicated radio/checkbox method; it's more
        # reliable than force=True and handles label-interception correctly.
        itype = input_type.lower()
        if itype in ("radio", "checkbox") or "radio" in loc_str or "checkbox" in loc_str:
            locator.set_checked(True, force=True)
            return f"{loc_str}.set_checked(True, force=True)"
        locator.click(force=True)
        return f"{loc_str}.click(force=True)"

    def force_click_hidden(self, selector: str, nth: int = 0) -> dict:
        """Click a hidden input (Tailwind radio/checkbox) with force=True, bypassing
        Playwright's visibility check. The actual <input> is hidden; the styled wrapper
        is what's visible. React's onChange only fires on the real input."""
        try:
            loc = self.page.locator(selector)
            n = loc.count()
            if n == 0:
                return {"ok": False, "error": f"selector '{selector}' matched 0 elements"}
            target = loc.nth(nth) if nth < n else loc.first
            before = len(self.issues)
            before_api = len(self._api_errors)
            # Use set_checked for radio/checkbox (Playwright's dedicated API),
            # force click for everything else.
            itype = selector.lower()
            if "radio" in itype or "checkbox" in itype:
                target.set_checked(True, force=True)
            else:
                target.click(force=True)
            self._settle()
            result = {"ok": True, "selector": selector, "nth": nth, "url": self._url()}
            new_api = self._api_errors[before_api:]
            if new_api:
                result["api_errors"] = new_api
                dom_errors = self._scan_dom_errors()
                if dom_errors:
                    result["page_validation_errors"] = dom_errors
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def press_key(self, key: str, times: int = 1) -> dict:
        """Press a keyboard key N times."""
        try:
            pre_url = self._url()
            for _ in range(max(1, times)):
                self.page.keyboard.press(key)
                # Recorded so replay presses it too (Enter to submit, Escape to close).
                self.step_id += 1
                self.steps.append({"id": self.step_id, "rawLine": f'page.keyboard.press("{_q(key)}")',
                                   "type": "press", "target": "page.keyboard", "url": pre_url,
                                   "targetDescription": f"Press {key}", "value": key, "varName": ""})
            self._settle()
            result = {"ok": True, "key": key, "times": times, "url": self._url()}
            if self.overlay_opened():
                result["overlay_opened"] = True
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def wait_for_text_on_page(self, text: str, timeout_ms: int = 5000) -> dict:
        """Wait for specific text to appear on the page."""
        try:
            self.page.wait_for_selector(f"text={text}", state="visible", timeout=timeout_ms)
            return {"ok": True, "found": text}
        except Exception:
            return {"ok": False, "error": f"'{text}' did not appear within {timeout_ms}ms"}

    def viewport_size(self) -> dict:
        vs = self.page.viewport_size or {"width": 1280, "height": 720}
        return dict(vs)

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
        self._auth_failures = []   # reset per-navigate counter
        self._api_errors = []
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
        on_login = self._on_login_page()
        auth_failures = list(self._auth_failures)
        result = {"ok": True, "url": self._url(), "title": self._title(),
                  "new_issues": self.issues[before:]}
        if on_login:
            result["⚠_AUTH_REQUIRED"] = (
                "LOGIN FORM DETECTED. The session was NOT accepted — you are on the login page. "
                "Step 1: call get_settings() and check platforms.[seller|admin].users for credentials. "
                "Step 2: if credentials found, fill the form and log in. "
                "Step 3: if login still fails OR no credentials found, call ask_user() immediately. "
                "DO NOT navigate to any other URL. DO NOT continue the task sequence. "
                "SEQUENCE IS SUSPENDED HERE until authentication is resolved."
            )
        elif auth_failures:
            result["⚠_AUTH_REQUIRED"] = (
                f"SESSION EXPIRED — {len(auth_failures)} request(s) returned 401/403: "
                f"{auth_failures[0]}{'...' if len(auth_failures)>1 else ''}. "
                "The frontend may still show pages (stale localStorage token) but the backend rejects requests. "
                "Step 1: call clear_auth_storage() to wipe the stale token. "
                "Step 2: navigate to the login URL. "
                "Step 3: call get_settings() for credentials and fill the login form. "
                "Step 4: if login still fails, call ask_user() for new credentials. "
                "DO NOT continue the task sequence until authentication is resolved."
            )
        return result

    def inspect(self, limit=40, include_hidden=False):
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed and could not be restarted; call restart_browser"}

        # --- Native snapshot (rich locators: test_id, fingerprint, hidden elements) ---
        raw = []
        native_err = None
        try:
            raw = _comprehensive_snapshot(self.page)
        except Exception as e:
            if _is_closed_error(e) and self._ensure_alive():
                try:
                    raw = _comprehensive_snapshot(self.page)
                except Exception as e2:
                    native_err = str(e2)[:160]
            else:
                native_err = str(e)[:160]

        models, seen = [], {}
        for el in (raw or []):
            if not isinstance(el, dict):
                continue
            if "_error" in el:
                native_err = el["_error"]
                break
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

        # --- Aria snapshot augmentation (adds fixed-footer buttons, hidden inputs, etc. native misses) ---
        snap = None
        try:
            snap = self.page.locator("body").aria_snapshot()
            # Dedupe against name AND placeholder: a native element may carry an
            # empty accessible name while aria names it from the placeholder.
            native_names_lower = set()
            for m in models:
                for v in (m.get("name"), m.get("placeholder")):
                    if v:
                        native_names_lower.add(v.lower())
            for i, ae in enumerate(_parse_aria_snapshot(snap)):
                if ae["name"].lower() not in native_names_lower:
                    am = _aria_el_to_model(ae["role"], ae["name"], i)
                    ref = am["ref"]
                    if ref in seen:
                        seen[ref] += 1
                        am = dict(am, ref=f"{ref}-{seen[ref]}")
                    else:
                        seen[ref] = 0
                    models.append(am)
                    native_names_lower.add(ae["name"].lower())
        except Exception:
            pass  # aria augmentation optional — native-only result is still valid

        if not models and native_err:
            self.by_ref = {}
            return {"ok": False, "error": native_err}
        # A static confirmation/detail page is still an observable page. Zero
        # interactive controls must not discard its URL and rendered evidence.

        # --- Normalizer detectors: fix name collisions (exact=True), compute hints ---
        disambiguate(models)
        for m in models:
            m["hints"] = infer_hints(m)

        self.by_ref = {m["ref"]: m for m in models}

        # Token-light list: identical repeats grouped (repeats=N), region context
        # (ctx) + link hrefs exposed — see normalizer.compact_elements.
        compact, hidden_total = compact_elements(models, limit, include_hidden)
        result = {"ok": True, "url": self._url(), "title": self._title(),
                  "element_count": len(models), "hidden_count": hidden_total,
                  "elements": compact, "issues": self.issues[-8:]}

        # --- Unified percept extras: open dropdown options + visible validation errors ---
        try:
            if self.overlay_opened():
                opts = []
                if snap:
                    for line in snap.splitlines():
                        s = line.strip()
                        if s.startswith('- option "') and s.endswith('"'):
                            opts.append(s[len('- option "'):-1])
                opts = opts or self._get_visible_options()
                if opts:
                    result["open_dropdown_options"] = opts[:30]
        except Exception:
            pass
        try:
            errs = self._scan_dom_errors()
            if errs:
                result["validation_errors"] = errs[:8]
        except Exception:
            pass
        return attach_interaction_context(self.page, result, self.by_ref)

    def _record_strategy(self, el):
        """Pick the locator to RECORD so the generated test hits exactly the intended element on a
        COLD run. Ambiguity handling (the primary matches >1 element):
          1. the element's own test-id, when unique;
          2. the element's DOM-order ordinal among its identical-locator group
             (match_index from normalizer.disambiguate) -> .nth(k). This targets the
             element the agent's ref actually points at — a bare ambiguous locator
             resolves the FIRST match live (clicked the sidebar tab instead of the
             card button) and fails Playwright strict mode at replay;
          3. otherwise keep the primary and warn.
        Returns (strategy, locator_str, warning|None, nth|None). Never raises."""
        prim = el.get("primary") or {}
        def _count(strat):
            try:
                loc = to_locator(self.page, strat)
                count = loc.count() if loc is not None else 0
                if count == 1 and el.get("node_id"):
                    identity = loc.evaluate("el => { const s=window.__qamateNodes; return s && s.document + ':' + s.ids.get(el); }")
                    # A unique text fallback may locate the LABEL instead of its
                    # input. Uniqueness alone is not grounding or replay safety.
                    if identity != el["node_id"]:
                        return 0
                return count
            except Exception:
                return -1
        k = el.get("match_index")
        n = _count(prim)
        if n == 1 and el.get("tag") == "a" and el.get("node_id"):
            # Entity links can gain duplicate renderings after asynchronous
            # activity arrives. Preserve the observed occurrence at replay too.
            return prim, _locator_str(prim), None, 0
        if n == 0:
            # The primary matches NOTHING live (e.g. Radix/shadcn selects: the
            # visible placeholder is aria-hidden, so the accessible name is empty
            # and role+name can never match). The live action would quietly heal
            # through fallbacks — so RECORD the fallback that actually matches,
            # or the generated test replays a locator that finds nothing
            # (run-4 BENCH-008 step 3 timed out exactly here).
            for fb in (el.get("fallbacks") or []):
                if _count(fb) == 1:
                    return fb, _locator_str(fb), None, None
        if n < 2 and k is None:                      # unique (1), none yet (0), or uncountable (-1) -> keep
            return prim, _locator_str(prim), None, None
        tid = (el.get("test_id") or "").strip()      # ambiguous -> try the element's test-id
        if tid:
            ts = {"by": "test_id", "value": tid}
            if el.get("test_id_attribute") in {"data-test", "data-test-id", "data-cy"}:
                escaped = tid.replace("\\", "\\\\").replace('"', '\\"')
                ts = {"by": "css", "value": f'[{el["test_id_attribute"]}="{escaped}"]'}
            if _count(ts) == 1:
                return ts, _locator_str(ts), None, None
        if k is not None:
            # nth suffix is appended by act() AFTER refining k against the live
            # locator (snapshot order is only a guess — see _ordinal_by_rect).
            return prim, _locator_str(prim), None, k
        if n > 1 and el.get("node_id"):
            try:
                identities = to_locator(self.page, prim).evaluate_all(
                    "els => els.map(el => { const s=window.__qamateNodes; return s && s.document + ':' + s.ids.get(el); })")
                if identities.count(el["node_id"]) == 1:
                    return prim, _locator_str(prim), None, identities.index(el["node_id"])
            except Exception:
                pass
        warn = (f"This locator matches {n} elements on the page, so the test will act on the FIRST "
                f"one. If that is not the element you meant, pick a more uniquely identifiable element "
                f"(distinct text/label, or one exposing a test id).")
        return prim, _locator_str(prim), warn, None

    def _ordinal_by_rect(self, loc, n, rect, tol=4.0):
        """True DOM-order ordinal of the group member whose geometry matches the
        snapshot rect. The snapshot's ordinals are computed from ITS iteration
        order, which need not equal the live locator's match order (and broke the
        dropdown ladder when trusted blindly) — geometry is the ground truth.
        Returns the ordinal, or None when nothing matches (page moved/scrolled)."""
        if not rect or rect.get("x") is None:
            return None
        try:
            for i in range(min(n, 15)):
                bb = loc.nth(i).bounding_box()
                if bb and abs(bb["x"] - rect.get("x", 0)) <= tol and abs(bb["y"] - rect.get("y", 0)) <= tol:
                    return i
        except Exception:
            return None
        return None

    def _auth_signal(self, before_auth):
        """Return the ⚠_AUTH_REQUIRED message when new 401/403s appeared since
        before_auth, else None. Without this, a stale token mid-form is invisible:
        the SPA swallows the 401 and shows an innocent empty state ('No customers
        found') while every search silently fails."""
        new_auth = self._auth_failures[before_auth:]
        if not new_auth:
            return None
        return (
            f"SESSION EXPIRED — {len(new_auth)} request(s) returned 401/403 during this "
            f"action: {new_auth[0]}{'...' if len(new_auth) > 1 else ''}. "
            "Any empty results / 'not found' messages you just saw are NOT real data — the "
            "backend rejected the request. "
            "Step 1: call clear_auth_storage(). Step 2: navigate to the login URL. "
            "Step 3: call get_settings() for credentials and log in. "
            "Step 4: if login still fails, call ask_user(). "
            "DO NOT continue the task sequence until authentication is resolved."
        )

    @staticmethod
    def _is_direct_selector(ref):
        """True when ref looks like a CSS/XPath/Playwright selector rather than an inspect_page ref name.
        Used as a fallback path for elements whose refs are broken (e.g. MUI :r3: colon IDs)."""
        if not ref:
            return False
        return (
            ref.startswith("input[") or ref.startswith("button[") or
            ref.startswith("select[") or ref.startswith("textarea[") or
            ref.startswith("[role=") or ref.startswith("[type=") or
            ref.startswith("[aria-") or ref.startswith("[placeholder") or
            ref.startswith("[name=") or ref.startswith("[id=") or
            ref.startswith("//") or            # XPath
            ref.startswith("text=") or         # Playwright text selector
            ref.startswith("role=") or         # Playwright role selector
            (ref.startswith("[") and "=" in ref and "]" in ref)
        )

    def act(self, kind, ref, value=None):
        return self._guard_action(kind, ref, value, lambda: self._act_once(kind, ref, value))

    def _action_state(self):
        try:
            import hashlib
            state = self.page.evaluate("""() => JSON.stringify([
                location.href, document.body?.innerText.slice(0, 20000),
                Array.from(document.querySelectorAll('input,select,textarea,[aria-expanded],[role=dialog]')).slice(0,200)
                    .map(el => [el.tagName,el.value,el.checked,el.disabled,el.getAttribute('aria-expanded'),el.getAttribute('aria-hidden')])
            ])""")
            return hashlib.sha256(state.encode()).hexdigest()
        except Exception:
            return self._url()

    def _guard_action(self, kind, ref, value, run):
        before = self._action_state()
        target = (self.by_ref.get(ref) or {}).get("node_id") or ref
        key = self._action_guard.key(kind, target, value, before)
        if self._action_guard.blocked(key):
            return {"ok": False, "blocked": True, "retry_exhausted": True,
                    "error": "Equivalent action already failed or made no progress twice. Replan or ask the user; do not repeat it."}
        result = run()
        progressed = bool(result.get("ok")) and self._action_state() != before
        self._action_guard.record(key, progressed)
        if result.get("ok") and not progressed:
            result["no_progress"] = True
        return result

    def _act_once(self, kind, ref, value=None):
        el = self.by_ref.get(ref)

        # Direct-selector fallback: if ref isn't a known inspect_page ref but looks like a
        # CSS/XPath selector (e.g. input[type="password"]), try it directly on the page.
        # This handles MUI forms where inspect_page returns colon-based IDs (:r3:, :r5:)
        # that break Playwright's selector engine — the agent can fall back to type-based selectors.
        if not el and self._is_direct_selector(ref):
            return self._act_direct(kind, ref, value)

        if not el:
            return {"ok": False, "error": f"ref '{ref}' is not on the current page; call observe first"}
        if not self._alive():
            restarted = self._ensure_alive()
            return {"ok": False, "error": ("browser had closed; restarted -- re-inspect the page (refs are stale) and retry"
                                           if restarted else "browser is closed; call restart_browser, then re-inspect")}
        strat, loc_str, warning, nth = self._record_strategy(el)
        try:
            if nth is not None:
                # Grouped identical locators: act on THIS member, not the first match.
                # The true ordinal comes from the live element's geometry; the snapshot
                # ordinal is only the fallback guess. Healing fallbacks would
                # re-introduce the first-match bug, so resolve direct.
                loc = to_locator(self.page, strat)
                n_live = loc.count()
                if n_live <= 1:
                    if el.get("tag") == "a" and n_live == 1:
                        loc_str = f"{loc_str}.nth(0)"
                    else:
                        nth = None                  # unique live — plain locator is right
                    live = loc
                else:
                    k_live = self._ordinal_by_rect(loc, n_live, el.get("rect") or {})
                    nth = k_live if k_live is not None else min(nth, n_live - 1)
                    live = loc.nth(nth)
                    loc_str = f"{loc_str}.nth({nth})"
            else:
                live = smart_locator(self.page, strat, fallbacks=el.get("fallbacks"),
                                     fingerprint=el.get("fingerprint")).resolve()
        except SelfHealError as e:
            return {"ok": False, "error": f"could not resolve '{ref}': {str(e)[:140]}"}
        except Exception as e:
            return {"ok": False, "error": f"could not resolve '{ref}': {str(e)[:140]}"}

        before = len(self.issues)
        before_api = len(self._api_errors)
        before_auth = len(self._auth_failures)
        name = el.get("name") or ref
        hints = el.get("hints") or []
        pre_url = self._url()   # page this step belongs to (a click may navigate away)
        self.step_id += 1
        try:
            if el.get("node_id"):
                identity = live.evaluate("el => { const s=window.__qamateNodes; return s && s.document + ':' + s.ids.get(el); }")
                if identity != el["node_id"]:
                    self.step_id -= 1
                    return {"ok": False, "error": "Target changed or healed to a different DOM node; observe again", "stale_ref": True, "dispatched": False}
            if kind == "fill":
                val = str(value or "")
                if "sequential-fill" in hints:
                    # MUI number inputs reject fill() (React onChange never fires).
                    # Type for real; the rawLine stays .fill() — codegen rewrites it
                    # to press_sequentially + Tab via _is_mui_numeric_input_fill.
                    live.click()
                    live.press("ControlOrMeta+a")
                    live.press_sequentially(val, delay=40)
                    live.press("Tab")
                else:
                    live.fill(val)
                self.input_counter += 1
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.fill("{_q(val)}")',
                                   "type": "fill", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Enter "{val}" in {name}',
                                   "value": val, "varName": f"input_{self.input_counter}"})
            elif kind == "select":
                val = str(value or "")
                live.select_option(val)
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.select_option("{_q(val)}")',
                                   "type": "select", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Select "{val}" in {name}',
                                   "value": val, "varName": ""})
            else:  # click — auto-fallback for Tailwind hidden radio/checkbox inputs
                used_raw = self._smart_click(live, loc_str, el.get("input_type", ""),
                                             prefer_force="force-click" in hints)
                self.steps.append({"id": self.step_id, "rawLine": used_raw,
                                   "type": "click", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Click {name}', "value": "", "varName": ""})
        except Exception as e:
            self.step_id -= 1
            return {"ok": False, "error": f"{kind} failed on '{ref}': {str(e)[:160]}"}
        self._settle()
        result = {"ok": True, "url": self._url(), "title": self._title(),
                  "new_issues": self.issues[before:]}
        auth_msg = self._auth_signal(before_auth)
        if auth_msg:
            result["⚠_AUTH_REQUIRED"] = auth_msg
        new_api = self._api_errors[before_api:]
        if new_api:
            result["api_errors"] = new_api
            dom_errors = self._scan_dom_errors()
            if dom_errors:
                result["page_validation_errors"] = dom_errors
            else:
                result["hint"] = "API error(s) detected — call scan_page_errors() to read any validation messages the page is showing."
        if warning:
            result["warning"] = warning
        if kind == "click" and self.overlay_opened():
            result["overlay_opened"] = True
        elif kind == "fill" and (el.get("role") == "combobox" or el.get("autocomplete") in {"list", "both"}):
            # After typing into a field, wait for autocomplete options (API response may be async).
            # If any appear, include them directly so the agent doesn't need another round-trip.
            opts = self.get_options()["options"]
            if not opts:
                # SPA autocomplete may fire an API call that returns after networkidle — retry once
                try:
                    self.page.wait_for_timeout(1500)
                except Exception:
                    pass
                opts = self.get_options()["options"]
            if opts:
                result["autocomplete_options"] = opts
                result["hint"] = ("Autocomplete options appeared. To pick one, call "
                                  "select_option(ref, value) with this field's ref — it selects "
                                  "the matching option and records the step.")
        return result

    def _act_direct(self, kind, ref, value=None):
        """Act on an element via a direct CSS/XPath/Playwright selector, bypassing by_ref lookup.
        Used when inspect_page refs are broken (MUI dynamic IDs, colon selectors, etc.)."""
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed; call restart_browser"}
        before = len(self.issues)
        before_api = len(self._api_errors)
        before_auth = len(self._auth_failures)
        pre_url = self._url()
        self.step_id += 1
        try:
            loc = self.page.locator(ref)
            n = loc.count()
            if n == 0:
                self.step_id -= 1
                return {"ok": False, "error": f"direct selector '{ref}' matched 0 elements on current page"}
            target = loc.first if n > 1 else loc
            loc_str = f'page.locator("{ref}")'
            if kind == "fill":
                val = str(value or "")
                target.fill(val)
                self.input_counter += 1
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.fill("{_q(val)}")',
                                   "type": "fill", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Enter "{val}" in {ref}',
                                   "value": val, "varName": f"input_{self.input_counter}"})
            elif kind == "select":
                val = str(value or "")
                target.select_option(val)
                self.steps.append({"id": self.step_id, "rawLine": f'{loc_str}.select_option("{_q(val)}")',
                                   "type": "select", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Select "{val}" in {ref}',
                                   "value": val, "varName": ""})
            else:  # click — auto-fallback for Tailwind hidden radio/checkbox inputs
                used_raw = self._smart_click(target, loc_str, ref)
                self.steps.append({"id": self.step_id, "rawLine": used_raw,
                                   "type": "click", "target": loc_str, "url": pre_url,
                                   "targetDescription": f'Click {ref}', "value": "", "varName": ""})
        except Exception as e:
            self.step_id -= 1
            return {"ok": False, "error": f"{kind} with selector '{ref}' failed: {str(e)[:160]}"}
        self._settle()
        result = {"ok": True, "url": self._url(), "title": self._title(),
                  "new_issues": self.issues[before:]}
        auth_msg = self._auth_signal(before_auth)
        if auth_msg:
            result["⚠_AUTH_REQUIRED"] = auth_msg
        new_api = self._api_errors[before_api:]
        if new_api:
            result["api_errors"] = new_api
            dom_errors = self._scan_dom_errors()
            if dom_errors:
                result["page_validation_errors"] = dom_errors
            else:
                result["hint"] = "API error(s) detected — call scan_page_errors() to read any validation messages the page is showing."
        if n > 1:
            result["warning"] = f"Selector '{ref}' matched {n} elements — acted on the first one."
        if kind == "click" and self.overlay_opened():
            result["overlay_opened"] = True
        elif kind == "fill":
            opts = self.get_options()["options"]
            if opts:
                result["autocomplete_options"] = opts
                result["hint"] = ("Autocomplete options appeared. To pick one, call "
                                  "select_option(ref, value) with this field's ref — it selects "
                                  "the matching option and records the step.")
        return result

    # Dropdown texts that are messages, not options ("No customers found for X",
    # "Start typing to search", "Loading..."). Selecting these is the classic
    # false-positive — the keyboard tier must never fire on them.
    _EMPTY_STATE_RE = re.compile(
        r"^no .{0,50}(found|results?|match)|^nothing found|^no options"
        r"|^start typing|^type to search|^loading|^searching|^please wait|^fetching", re.I)
    # Subset of empty-state: transient loading texts — wait them out, don't give up.
    _LOADING_RE = re.compile(r"^(loading|searching|fetching|please wait)", re.I)
    # Options the ladder must NEVER auto-click unless the agent literally asked for
    # them. Menus (profile, row actions) can be misread as dropdowns; clicking
    # "Logout" because it was the first option is catastrophic.
    _DESTRUCTIVE_OPT_RE = re.compile(
        r"^(log\s?-?out|sign\s?-?out|delete|remove|cancel|reject|deactivate)\b", re.I)

    def _real_options(self, opts):
        return [o for o in (opts or []) if not self._EMPTY_STATE_RE.search((o or "").strip())]

    def select_from_dropdown(self, ref, value):
        return self._guard_action("select-dropdown", ref, value,
                                  lambda: self._select_from_dropdown_once(ref, value))

    def _select_from_dropdown_once(self, ref, value):
        """Intent-level dropdown selection: ONE call runs the whole escalation ladder
        (native select -> type-to-search (full term, then first word) -> wait for
        async options -> option click -> keyboard) and records test steps for
        whichever tier worked. The agent never chooses between tiers. Returns
        blocked:true when every tier failed so the async tool wrapper can try the
        vision tier."""
        el = self.by_ref.get(ref)
        direct = el is None and self._is_direct_selector(ref)
        if el is None and not direct:
            return {"ok": False, "error": f"ref '{ref}' is not on the current page; call observe first"}

        # Tier 0: native <select> — Playwright handles it outright.
        if el is not None and el.get("tag") == "select":
            options = el.get("options") or []
            matches = [o for o in options if not o.get("disabled") and (o.get("value") == value or o.get("label") == value)]
            if options and len(matches) != 1:
                return {"ok": False, "error": "Native option must match one enabled label or value exactly", "options": options}
            r = self.act("select", ref, matches[0]["value"] if matches else value)
            if r.get("ok"):
                r.update({"selected": value, "via": "native-select"})
            return r

        tried = []
        opts, raw_opts = [], []
        role = (el.get("role") or "") if el else ""
        tag = (el.get("tag") or "") if el else ""
        typeable = direct or tag in ("input", "textarea") or role in (
            "textbox", "searchbox", "combobox", "spinbutton")

        # Tier 1: type the value — full term first, then first word (search backends
        # often miss the full legal name but hit the distinctive first token).
        terms = [value]
        first_word = (value.split() or [""])[0]
        if first_word and first_word.lower() != value.lower():
            terms.append(first_word)
        if typeable:
            for ti, term in enumerate(terms):
                if ti > 0 and self.steps and self.steps[-1].get("type") == "fill":
                    self.steps.pop()        # drop the no-result search from the recording
                    self.step_id -= 1
                r = self.act("fill", ref, term) if not direct else self._act_direct("fill", ref, term)
                if r.get("⚠_AUTH_REQUIRED"):
                    return {"ok": False, "blocked": True,
                            "⚠_AUTH_REQUIRED": r["⚠_AUTH_REQUIRED"],
                            "error": "authentication required — the search API returned 401/403"}
                if not r.get("ok"):
                    tried.append(f"fill('{term[:30]}') failed: {(r.get('error') or '')[:60]}")
                    break
                raw_opts = r.get("autocomplete_options") or self.get_options().get("options") or []
                opts = self._real_options(raw_opts)
                # 'Please wait...' / 'Loading' = the API is still working — be patient
                # (up to ~8s) instead of concluding there are no options.
                waited = 0
                while (not opts and waited < 8
                       and any(self._LOADING_RE.search((o or "").strip()) for o in raw_opts)):
                    try:
                        self.page.wait_for_timeout(1000)
                    except Exception:
                        break
                    waited += 1
                    raw_opts = self.get_options().get("options") or []
                    opts = self._real_options(raw_opts)
                tried.append(f"fill('{term[:30]}') -> {len(opts)} options"
                             + (f" (waited {waited}s for loading)" if waited else ""))
                if opts:
                    break
        # Tier 1b: non-typeable trigger (or fill failed) — click to open, then read options.
        if not opts:
            if not typeable or (tried and "failed" in tried[-1]):
                rc = self.act("click", ref) if not direct else self._act_direct("click", ref)
                tried.append("click-to-open" if rc.get("ok")
                             else f"click-to-open failed: {(rc.get('error') or '')[:80]}")
            raw_opts = self.get_options().get("options") or raw_opts
            opts = self._real_options(raw_opts)

        # Tier 2: targeted wait for the value text (async API results), then re-read.
        if not match_option(opts, value):
            if self.wait_for_text_on_page(value, 3000).get("ok"):
                opts = self._real_options(self.get_options().get("options")) or opts

        target = match_option(opts, value)
        # Destructive guard: never auto-click logout/delete/etc. via fuzzy match —
        # only when the agent asked for that option verbatim.
        if target and self._DESTRUCTIVE_OPT_RE.search(target.strip()) \
                and target.strip().casefold() != str(value).strip().casefold():
            tried.append(f"matched '{target[:40]}' but it is destructive — refused to auto-click")
            target = None
        if target:
            res = self.click_option_by_text(target, record=True)
            if res.get("ok"):
                return {"ok": True, "selected": target, "via": "option-click",
                        "url": self._url(), "options_seen": opts[:10]}
            tried.append(f"option-click failed: {(res.get('error') or '')[:80]}")

        # Tier 3: keyboard protocol — only when the MATCHED option is the first in
        # the list (ArrowDown+Enter selects the first; firing it blind would pick
        # an arbitrary — possibly destructive — entry).
        if target and opts and opts[0] == target:
            try:
                self.page.keyboard.press("ArrowDown")
                self.page.keyboard.press("Enter")
                self._settle()
                if not self.overlay_opened():           # dropdown closed -> something got selected
                    for key in ("ArrowDown", "Enter"):
                        self.step_id += 1
                        self.steps.append({"id": self.step_id,
                                           "rawLine": f'page.keyboard.press("{key}")',
                                           "type": "press", "target": "page.keyboard",
                                           "targetDescription": f"Press {key} (select dropdown option)",
                                           "value": key, "varName": ""})
                    return {"ok": True, "selected": target, "via": "keyboard", "url": self._url(),
                            "options_seen": opts[:10],
                            "verify": "selection made via keyboard — confirm the field now shows the intended value"}
                tried.append("keyboard: dropdown still open")
            except Exception as e:
                tried.append(f"keyboard failed: {str(e)[:80]}")

        res = {"ok": False, "blocked": True, "tried": tried, "options_seen": opts[:15],
               "error": f"could not select '{value}' — no tier matched an option",
               "hint": ("If options_seen contains the right item under a different label, call "
                        "select_option again with that EXACT text. Otherwise the dropdown may "
                        "need vision or user input.")}
        empty_msgs = [o for o in (raw_opts or []) if self._EMPTY_STATE_RE.search((o or "").strip())]
        if empty_msgs:
            res["dropdown_message"] = empty_msgs[0][:120]
            res["hint"] = ("The dropdown reported: '" + empty_msgs[0][:80] + "'. The search "
                           "term likely has no match in this environment's data — ask_user "
                           "for the correct value instead of retrying more variations.")
        return res

    _IDENTIFY_JS = """([x, y]) => {
        let el = document.elementFromPoint(x, y);
        if (!el) return null;
        el = el.closest('button,[role],a[href],input,select,textarea,[tabindex],[onclick],[data-testid]') || el;
        const r = el.getBoundingClientRect();
        return {tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                text: (el.innerText || el.textContent || '').trim().slice(0, 150),
                aria_label: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '', type: el.getAttribute('type') || '',
                id: el.id || '',
                test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
                visible: true, hidden_reason: '',
                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}};
    }"""

    def identify_at(self, x, y):
        """Vision -> ref grounding: resolve pixel coordinates to the DOM element at
        that point, build an element model for it, and register it in by_ref so the
        agent can act on it with a normal ref (no coordinate clicking)."""
        try:
            info = self.page.evaluate(self._IDENTIFY_JS, [x, y])
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
        if not info:
            return {"ok": False, "error": f"no DOM element at ({x},{y}) — possibly canvas content"}
        m = element_to_model(info)
        m["hints"] = infer_hints(m)
        ref = m["ref"]
        if ref in self.by_ref and self.by_ref[ref].get("fingerprint") != m.get("fingerprint"):
            i = 2
            while f"{ref}-{i}" in self.by_ref:
                i += 1
            ref = f"{ref}-{i}"
            m["ref"] = ref
        self.by_ref[ref] = m
        return {"ok": True, "ref": ref, "role": m.get("role") or m.get("tag"),
                "name": (m.get("name") or "")[:80]}

    def scroll_to_text(self, text, max_scrolls=20):
        """Deterministic scroll-until-found: step the page down until an element
        containing `text` is visible, then register it as a ref (identify_at
        grounding). ONE call replaces the agent's scroll->observe percept loop
        (BENCH-007 burned its whole 2400s wall scroll-hunting lazy-loaded cards).
        Records a single replay-safe scroll_into_view step regardless of how far
        it scrolled."""
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed; call restart_browser"}
        t = str(text or "").strip()
        if not t:
            return {"ok": False, "error": "text is required"}
        # SPAs usually scroll an inner pane, not the window — find the largest
        # scrollable container and step IT; window scrolling is the fallback.
        # When a dialog/overlay is OPEN, only ITS scrollables count: run-6
        # BENCH-007 scrolled the catalog BEHIND the Create Product modal and
        # reported "end of scrollable area" while Submit sat below the modal fold.
        _SCROLL_JS = """() => {
            const ok = (el) => el.scrollHeight > el.clientHeight + 10 &&
                ['auto','scroll','overlay'].includes(getComputedStyle(el).overflowY);
            const dlg = Array.from(document.querySelectorAll('[role=dialog], dialog'))
                .filter(d => { const r = d.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .pop();   // topmost rendered dialog
            const scope = dlg || document;
            let best = null, bestArea = 0;
            const cands = [...(dlg && ok(dlg) ? [dlg] : []), ...scope.querySelectorAll('div,main,section,ul')];
            for (const el of cands) {
                if (!ok(el)) continue;
                const r = el.getBoundingClientRect();
                if (r.width * r.height > bestArea) { best = el; bestArea = r.width * r.height; }
            }
            const step = Math.round(window.innerHeight * 0.8);
            if (best) { const b = best.scrollTop; best.scrollTop += step; return best.scrollTop !== b; }
            if (dlg) return false;   // dialog open but nothing scrollable in it — never scroll the page behind it
            const y = window.scrollY; window.scrollBy(0, step); return window.scrollY !== y;
        }"""
        found, scrolls, loc, stuck = False, 0, None, 0
        for i in range(max(1, int(max_scrolls))):
            try:
                loc = self.page.get_by_text(t).first
                if loc.count() > 0:
                    try:
                        loc.scroll_into_view_if_needed(timeout=3000)
                        if loc.is_visible():
                            found = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                moved = self.page.evaluate(_SCROLL_JS)
                self.page.wait_for_timeout(400)   # let lazy lists render the next batch
            except Exception as e:
                return {"ok": False, "error": f"scroll failed: {str(e)[:120]}"}
            scrolls = i + 1
            stuck = 0 if moved else stuck + 1
            if stuck >= 2:
                break   # nothing scrolls anymore — end of the list
        if not found:
            return {"ok": False, "found": False, "scrolls": scrolls,
                    "error": (f"no visible element containing {t[:50]!r} after {scrolls} scrolls"
                              + (" (reached the end of the scrollable area)" if stuck >= 2 else ""))}
        ref_info = {}
        try:
            bb = loc.bounding_box()
            if bb:
                ref_info = self.identify_at(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        except Exception:
            pass
        pre_url = self._url()
        self.step_id += 1
        self.steps.append({"id": self.step_id,
                           "rawLine": f'page.get_by_text("{_q(t)}").first.scroll_into_view_if_needed()',
                           "type": "scroll", "target": f'page.get_by_text("{_q(t)}").first',
                           "url": pre_url,
                           "targetDescription": f"Scroll until '{t[:40]}' is visible",
                           "value": "", "varName": ""})
        out = {"ok": True, "found": True, "scrolls": scrolls}
        if ref_info.get("ok"):
            out.update({"ref": ref_info["ref"], "role": ref_info.get("role"),
                        "name": ref_info.get("name")})
            out["hint"] = "act on this ref with click(ref); the scroll step is already recorded"
        return out

    def upload_file(self, ref, file_path=""):
        """Attach a file to an upload control and RECORD a replay-safe step.
        Defaults to the suite's standard fixture (tests/fixtures/test_upload.png);
        the generated test replays with TEST_UPLOAD_IMAGE regardless of the live
        path (recorder_parser rewrites set_input_files args). Handles real
        <input type=file> elements (hidden ones included — Playwright allows it)
        and buttons that open a native file chooser."""
        if not self._ensure_alive():
            return {"ok": False, "error": "browser is closed; call restart_browser"}
        path = (file_path or "").strip()
        if not path:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tests", "fixtures", "test_upload.png")
        if not os.path.isfile(path):
            return {"ok": False, "error": f"file not found: {path[:120]}"}
        el = self.by_ref.get(ref)
        if el is None and not self._is_direct_selector(ref):
            return {"ok": False, "error": f"ref '{ref}' is not on the current page; call observe first"}
        pre_url = self._url()

        def _record(raw, desc):
            self.step_id += 1
            self.steps.append({"id": self.step_id, "rawLine": raw, "type": "upload",
                               "target": "", "url": pre_url,
                               "targetDescription": desc, "value": "", "varName": ""})

        fname = os.path.basename(path)
        try:
            # Case 1: the ref IS a file input (or a direct selector for one).
            if el is not None and (el.get("input_type") or "").lower() == "file":
                strat, loc_str, _w, _n = self._record_strategy(el)
                live = smart_locator(self.page, strat, fallbacks=el.get("fallbacks"),
                                     fingerprint=el.get("fingerprint")).resolve()
                live.set_input_files(path)
                _record(f'{loc_str}.set_input_files("{_q(path)}")', f"Upload {fname}")
                self._settle()
                return {"ok": True, "via": "file-input", "file": fname}
            # Case 2: exactly one file input on the page — upload buttons usually
            # proxy a hidden <input type=file>; set directly (works while hidden).
            finput = self.page.locator('input[type="file"]')
            if finput.count() == 1:
                finput.set_input_files(path)
                _record(f'page.locator(\'input[type="file"]\').set_input_files("{_q(path)}")',
                        f"Upload {fname}")
                self._settle()
                return {"ok": True, "via": "hidden-input", "file": fname}
            # Case 3: click the control and feed the native file chooser. Record the
            # click + set_input_files pair — recorder_parser regenerates it as a
            # with page.expect_file_chooser() block.
            if el is None:
                live, loc_str = self.page.locator(ref), f'page.locator("{_q(ref)}")'
            else:
                strat, loc_str, _w, _n = self._record_strategy(el)
                live = smart_locator(self.page, strat, fallbacks=el.get("fallbacks"),
                                     fingerprint=el.get("fingerprint")).resolve()
            with self.page.expect_file_chooser(timeout=8000) as fc:
                live.click()
            fc.value.set_files(path)
            _record(f"{loc_str}.click()", f"Click {el.get('name') if el else ref} (opens file chooser)")
            _record(f'page.set_input_files("{_q(path)}")', f"Upload {fname}")
            self._settle()
            return {"ok": True, "via": "file-chooser", "file": fname}
        except Exception as e:
            return {"ok": False, "error": f"upload failed on '{ref}': {str(e)[:160]}"}

    def mark_step_manual(self, prompt):
        """Convert the LAST recorded fill step into a manual-input gate: the generated
        test pauses there and prompts the human for a fresh value (an OTP that arrived
        on a phone during authoring cannot be replayed from test data)."""
        for s in reversed(self.steps):
            if s.get("type") == "fill":
                p = str(prompt or "Enter the value").strip()[:160]
                s["value"] = "__MANUAL__:" + p
                s["varName"] = s.get("varName") or ""
                s["targetDescription"] = f"PAUSE for manual input: {p}"
                return {"ok": True, "step_id": s.get("id"),
                        "note": "Recorded. The generated test will pause at this step and "
                                "show the prompt; the human types the fresh value (e.g. OTP) "
                                "and the test resumes."}
        return {"ok": False,
                "error": "no fill step recorded yet — fill the OTP/code field first, "
                         "then call mark_step_manual"}

    def add_checkpoint(self, name, assert_type, value, ref=""):
        after = self.steps[-1]["id"] if self.steps else 0
        v = str(value or "")
        if assert_type not in {"url_equals", "url_contains", "page_contains_text", "page_not_contains_text", "element_has_value"}:
            return {"ok": False, "error": "Unsupported checkpoint type"}
        if (not v.strip() and assert_type != "element_has_value") or not self._alive():
            return {"ok": False, "error": "Checkpoint requires a nonblank value and a live page"}
        if assert_type == "element_has_value":
            el = self.by_ref.get(ref)
            if not el or not el.get("node_id"):
                return {"ok": False, "error": "A current DOM-grounded input/select ref is required"}
            try:
                strategy, _, _, ordinal = self._record_strategy(el)
                locator = to_locator(self.page, strategy)
                if locator is not None and ordinal is not None:
                    locator = locator.nth(ordinal)
                if locator is None or locator.count() != 1:
                    return {"ok": False, "error": "Value checkpoint needs a unique locator"}
                node = locator.evaluate("el => { const s=window.__qamateNodes; return s && s.document + ':' + s.ids.get(el); }")
                button_combo = el.get("tag") == "button" and el.get("role") == "combobox"
                actual_value = locator.inner_text().strip() if button_combo else locator.input_value()
                if node != el["node_id"] or actual_value != v or button_combo and locator.get_attribute("aria-expanded") == "true":
                    return {"ok": False, "error": "Value checkpoint is not true on the current grounded element"}
                self.assertions.append({"type": "locator_has_text" if button_combo else "locator_has_value", "selector": locator._impl_obj._selector, "value": v,
                                        "afterStep": after, "description": name})
                return {"ok": True, "checkpoint_count": len(self.assertions)}
            except Exception:
                return {"ok": False, "error": "Could not verify the value checkpoint"}
        # Record-time truth gate: a checkpoint asserts the CURRENT state (it
        # replays right after the latest step), so if it isn't true on the
        # SETTLED page now it can never pass at replay. Catching it here costs
        # milliseconds; catching it in run_test_case costs a 2-minute browser
        # cycle (run-5 BENCH-009: asserted the vendor name that was only on
        # screen inside the open dropdown).
        try:
            if assert_type in {"page_contains_text", "page_not_contains_text"}:
                self._settle()   # let transient dropdowns/toasts finish
                body = self.page.inner_text("body") or ""
                if assert_type == "page_not_contains_text" and v in body:
                    return {"ok": False, "error": "checkpoint NOT TRUE right now: text is still present"}
                if assert_type == "page_contains_text" and v not in body:
                    return {"ok": False,
                            "error": (f"checkpoint NOT TRUE right now: the settled page does not "
                                      f"show {v[:60]!r}. If you saw it inside a dropdown/toast it "
                                      "was transient and will NOT be there at replay - assert a "
                                      "durable outcome instead (heading, field label, URL, count).")}
            elif assert_type == "url_equals":
                if self._url() != v:
                    return {"ok": False, "error": "Current URL does not equal the required URL"}
            elif assert_type == "url_contains" and v:
                cur = self._url()
                if not cur or v not in cur:
                    return {"ok": False,
                            "error": (f"checkpoint NOT TRUE right now: the current URL "
                                      f"{cur[:80]!r} does not contain {v[:60]!r}.")}
        except Exception:
            return {"ok": False, "error": "Checkpoint probe failed; no assertion was recorded"}
        self.assertions.append({"type": assert_type, "value": v,
                                "afterStep": after, "description": name})
        return {"ok": True, "checkpoint_count": len(self.assertions)}

    def recorded_flow(self):
        return {"steps": [{"id": s["id"], "desc": s["targetDescription"]} for s in self.steps],
                "checkpoints": [a["description"] for a in self.assertions],
                "issues_found": len(self.issues), "snapshots": self.recording_history.summaries(),
                "next_step": "If required outcomes are covered, create and replay the test. "
                             "Do not re-record merely to remove recovery steps or expected validation errors."}

    @staticmethod
    def _collect_check(ats_root, tests_root, flow_id, tc_id, timeout=90):
        """pytest --collect-only on the just-generated test (fast, NO browser). A
        syntax/import error in a generated file aborts collection for the WHOLE
        suite, so catch it BEFORE the agent burns a live verify cycle on it
        (observed: bench smoke attempt 1 was an uncollectable file = a wasted
        ~2-minute run_test_case). Returns '' when clean, else the error detail."""
        import subprocess as _sp
        flow_dir = os.path.join(tests_root, "flows", flow_id)
        underscored = tc_id.replace("-", "_")
        cmd = [sys.executable, "-m", "pytest", flow_dir, "-k", underscored,
               "--collect-only", "-q", "-p", "no:cacheprovider"]
        env = dict(os.environ)
        env["ATS_ROOT"] = ats_root
        env["ATS_APP_ROOT"] = _APP_ROOT
        env["PYTHONPATH"] = _APP_ROOT
        kw = {"stdin": _sp.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0x08000000)
            kw["close_fds"] = True
        try:
            p = _sp.run(cmd, cwd=ats_root, env=env, capture_output=True, text=True,
                        timeout=timeout, **kw)
        except Exception:
            return ""   # the checker itself failing must never block delivery
        if p.returncode == 0:
            return ""
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 5:
            return (f"collection succeeded but NO test matched '{underscored}' - the generated "
                    "function name does not match the tc_id")
        return out[-1200:]

    def create_test(self, ats_root, project, tc_id, flow_id, description,
                    preconditions="", expected=""):
        if len(self.steps) <= 1:
            return {"status": "error",
                    "message": "only a page navigation is recorded — the test needs at least one "
                               "interaction (click, fill, or select_option) after navigating. "
                               "Drive the flow further, then call create_test_case again."}
        pname = (project or {}).get("name") or (project or {}).get("id") or "the app"
        steps = _ensure_leading_navigation(list(self.steps))
        payload = {
            "tc_id": tc_id, "description": (description or tc_id)[:200], "flowId": flow_id,
            "preconditions": preconditions or f"{pname} reachable; logged in if required",
            "expectedResult": expected, "steps": steps,
            "assertions": self.assertions, "criteria": [],
        }
        res = generate_from_review(payload, ats_root, tests_dir=_tests_root_for(ats_root, project))
        res = res if isinstance(res, dict) else {"status": "success", "result": str(res)}
        res.update({"recorded_steps": len(self.steps), "checkpoints": len(self.assertions)})
        warns = _lint_recording(self.steps, self.assertions)
        if warns:
            res["lint_warnings"] = warns   # nudge the agent to fix weak/dynamic assertions before delivering
        if self.assumptions:
            # Review queue: values the agent synthesized without user provenance.
            res["assumed_values"] = list(self.assumptions)
            res["assumed_values_note"] = ("These values were NOT provided by the user — "
                                          "surface them for review when delivering the test.")
        if self.skips:
            res["skipped_steps"] = list(self.skips)
        if str(res.get("status")) == "success":
            cerr = self._collect_check(ats_root, _tests_root_for(ats_root, project), flow_id, tc_id)
            if cerr:
                res["status"] = "error"
                res["collect_error"] = cerr
                res["message"] = ("the generated test fails pytest COLLECTION - it cannot run and "
                                  "would abort the whole suite. Common causes: a newline or quote "
                                  "inside an element name leaking into a locator string. Fix the "
                                  "flow (re-record the bad step) and call create_test_case again "
                                  "(same tc_id overwrites).")
        return res


def _ensure_leading_navigation(steps):
    """A recording that doesn't START with a navigation generates a test that
    begins mid-flow: the fixture opens the app root while step 1 expects the page
    the agent had already explored its way onto (run-4 BENCH-009: the goto landed
    LAST, so the test filled a search box on a page it never opened). When the
    first step isn't a navigate, synthesize one from the URL captured when that
    step was recorded. Pure — unit-tested in engine/tests."""
    if not steps or steps[0].get("type") == "navigate":
        return steps
    url = (steps[0].get("url") or "").strip()
    if not url:
        return steps
    nav = {"id": 0, "rawLine": f'page.goto("{_q(url)}")', "type": "navigate",
           "target": url, "targetDescription": f"Navigate to {url}",
           "value": "", "varName": ""}
    return [nav] + steps


# ── UI Map crawler (sync, runs on _BROWSER thread) ───────────────────────────

def _map_norm_url(url):
    """URL identity for the UI-map crawl. Unlike dom_inspector._normalize_url this
    KEEPS hash-route fragments (#/oms/cart) — on hash-routed SPAs (the admin panel)
    every screen would otherwise collapse into one page. Plain in-page anchors
    (#section) and query strings are still dropped."""
    if not url:
        return ""
    base, _, frag = url.partition("#")
    base = base.split("?", 1)[0].rstrip("/")
    if frag.startswith("/"):                      # SPA route, not an anchor
        return base + "#" + frag.split("?", 1)[0].rstrip("/")
    return base


def _crawl_ui_map(session, start_url, max_pages=30, max_depth=3, on_log=None):
    """BFS crawl using the agent's existing authenticated sync Playwright page.
    Follows same-origin <a href> links only (no button-clicks that might mutate
    state) — including hash routes on SPAs. Every element's primary locator is
    VALIDATED live (verified/ambiguous/broken) so the map never lies about how
    to reach an element. JS-navigation candidates (menuitems/tabs without href)
    are listed per page so the agent knows screens exist beyond the crawl.
    Saves elements per page, nav edges, errors. Navigates back to start_url."""
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
        norm = _map_norm_url(url)
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
        mods, seen_refs = [], {}
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
            mods.append(mod)
        disambiguate(mods)   # Paid/Unpaid exact=True fixes apply AT MAP TIME too

        elements, js_nav, verified_n = [], [], 0
        for mod in mods:
            # Validate the primary locator against the LIVE page — a map that
            # records a wrong role (run3's combobox-vs-button) burns agent turns.
            status = "broken"
            try:
                loc = to_locator(page, mod.get("primary") or {})
                n = loc.count() if loc is not None else 0
                status = "verified" if n == 1 else ("ambiguous" if n > 1 else "broken")
            except Exception:
                pass
            if status == "verified":
                verified_n += 1
            entry = {k: mod[k] for k in
                     ("ref", "role", "name", "tag", "test_id",
                      "input_type", "placeholder", "primary") if k in mod}
            entry["locator_status"] = status
            elements.append(entry)
            # Navigation the crawler can't safely follow (no href — JS routing).
            if mod.get("role") in ("menuitem", "tab") and mod.get("name"):
                js_nav.append(mod["name"][:60])

        links_out = []
        try:
            for lnk in page.evaluate(_LINKS_JS):
                href = lnk.get("href", "")
                text = lnk.get("text", "")
                if not href or _should_skip_link(href, text):
                    continue
                norm_href = _map_norm_url(href)
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
            "verified_locators": verified_n,
            "links_out": sorted(set(links_out)),
        }
        if js_nav:
            pages_data[norm]["js_nav_candidates"] = sorted(set(js_nav))[:20]
        _log(f"[map] [{len(pages_data)}/{max_pages}] {norm}  "
             f"({len(elements)} elements, {verified_n} verified, depth {depth})")

    # Return browser to start page so the agent can continue where it was
    try:
        page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    return {
        "ok": True,
        "schema_version": 3,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "start_url": _map_norm_url(start_url),
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
    user_input_q: Optional[asyncio.Queue] = None  # ask_user tool blocks on this
    _ask_state: dict = field(default_factory=dict)  # {"waiting": True} while ask_user is blocked
    mode: str = "auto"        # "auto" (free) | "guided" (tools pause on unproven values/skips)
    provenance: Optional["ProvenanceTracker"] = None  # legitimate value sources this session
    plan: list = field(default_factory=list)  # live checklist [{step, status, note?}] -> 'plan' events
    tool_group: str = ""
    recovery_actions: int = 0
    controller_active: bool = False
    goal_handoffs: GoalHandoffGuard = field(default_factory=GoalHandoffGuard)


# ── System prompt ──────────────────────────────────────────────────────────

_SYSTEM = (
    "You are QAmate's autonomous QA engineer. You operate a REAL Chromium browser "
    "through tools to drive an app and author COMPLETE, runnable end-to-end test cases — "
    "full flows, not fragments.\n\n"
    "Operating loop:\n"
    "- You usually start ALREADY logged in (a saved session is loaded for you). Do NOT attempt to "
    "log in or type credentials unless you actually land on a login form.\n"
    "- Before acting on a page, call observe — your unified percept. It returns every "
    "interactive element with a stable 'ref', plus any open dropdown options and visible "
    "validation errors. Use ONLY refs from the MOST RECENT observe. Re-observe after any "
    "navigation or click that changes the page.\n"
    "- For any multi-step task (3+ actions), call set_plan FIRST with the step list, then "
    "update_plan as you work (active -> done/failed/skipped). The user watches this checklist "
    "live — keep it honest and current.\n"
    "- Drive the flow the user asked for, one action at a time (navigate / click / fill / select_option). "
    "These tools handle quirks internally (hidden inputs, number fields, portal dropdowns, "
    "keyboard fallbacks) — express WHAT you want, not HOW to click it. For ANY dropdown or "
    "autocomplete selection use select_option(ref, value); never improvise with raw key presses.\n"
    "- If observe is missing an element you can SEE (per look()), use find_on_screen(description) "
    "— it returns a ref you can act on.\n"
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
    "then observe again (your old refs are stale).\n\n"
    "Using the ATS app itself (as a user, never editing its code):\n"
    "- You CAN read and change the app's Settings: get_settings (environments, the seller/admin "
    "accounts WITH their credentials, execution options, Jira) and update_setting. So when asked to "
    "'use the seller credentials from settings', call get_settings — never ask the user for them.\n"
    "- You CAN browse the existing test suite (list_test_flows / read_test_cases) and read run "
    "reports / history (list_runs / read_run), and list projects (list_projects).\n"
    "- Note: you usually do NOT need credentials to log in — the session is already authenticated "
    "for you; get_settings is for answering questions and for flows that explicitly need an account.\n\n"
    "Context about the app (use what you're given — don't ask the user to paste things you can read):\n"
    "- Before filling any form: call get_input_registry(url_filter='<route>') to retrieve "
    "known-good values from past sessions. Use those as your first attempt. (fill and "
    "select_option auto-record successful values; record_input is only for values you "
    "confirmed some other way.)\n"
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
    "complex, or a login form will not accept your credentials) STOP and call ask_user — do NOT "
    "keep clicking around and do NOT skip the blocked step to continue with the next app or section. "
    "Skipping a blocked step is NEVER acceptable. A blocked step suspends the sequence until you "
    "get an answer from the user and resolve it.\n"
    "- Login is a hard blocker: if you land on a login form and the saved session was not accepted, "
    "first check get_settings() for credentials. If login still fails or creds are absent, call "
    "ask_user immediately — do not navigate away, do not start a different app.\n"
    "- Report any breakage you surface (console errors, JS exceptions, failed requests, broken "
    "images) as findings.\n\n"
    "Rules:\n"
    "- NEVER log out or perform destructive actions (delete / cancel / reject / accept / pack) "
    "UNLESS the user's goal explicitly requires it.\n"
    "- Never invent refs. You have conversation memory — the user can correct you mid-flow; adapt."
)


_APPROVAL_WORDS = ("ok", "okay", "yes", "y", "approve", "approved", "go ahead", "use it", "proceed")


async def _value_gate(ctx, ref, value, action):
    """Provenance gate for fill/select_option — returns the FINAL value to use.

    GUIDED mode + unknown value: pause the session (same channel as ask_user) and
    let the user supply or approve the value. AUTO mode + unknown value: proceed,
    but record the assumption on the session (review queue surfaced by
    create_test_case). This is tool-level enforcement: it runs INSIDE the tool
    before the action, so the model cannot rationalise around it."""
    deps = ctx.deps
    tracker = deps.provenance
    if tracker is None or not str(value or "").strip():
        return value
    if tracker.check(value):
        return value
    el = deps.session.by_ref.get(ref, {}) if deps.session else {}
    field_name = str(el.get("name") or el.get("placeholder") or ref)
    if deps.mode == "guided" and deps.user_input_q is not None:
        question = (f"GUIDED MODE — value check: I'm about to {action} \"{field_name}\" with "
                    f"\"{value}\", but that value did not come from you (not in your messages, "
                    f"attached files, project memory, or the input registry). "
                    f"Reply with the value to use, or 'ok' to approve this one.")
        emit({"event": "input_required", "question": question, "kind": "guided_value",
              "field": field_name, "proposed": str(value)})
        deps._ask_state["waiting"] = True
        try:
            answer = str(await deps.user_input_q.get()).strip()
        finally:
            deps._ask_state["waiting"] = False
        tracker.add_text(answer, "user-reply")
        if answer.casefold() in _APPROVAL_WORDS:
            tracker.add_value(value, "user-approved")
            return value
        return answer
    # AUTO mode: proceed, but flag for review (once per distinct value).
    deps.session.assumptions.append(
        {"field": field_name, "value": str(value), "action": action})
    tracker.add_value(value, "assumed")
    return value


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


def build_agent(model, max_tokens=None, profile=None, *, hybrid=False, decision_loop=False, decision_workflow=False):
    from planner_policy import HYBRID_SYSTEM, DECISION_LOOP_SYSTEM, DECISION_WORKFLOW_SYSTEM, GROUPS, GoalCheckpoints, goal_plan_status, prepare_planner_tools, direct_action_allowed
    mt = int(max_tokens if max_tokens is not None else AGENT_MAX_TOKENS)
    profile = profile or {}
    settings = dict(profile.get("model_settings") or {})
    if profile.get("supports_temperature", True):
        settings.setdefault("temperature", 0.2)
    if mt > 0:                       # 0/unset => omit max_tokens so the model uses its own output max
        settings["max_tokens"] = mt
    agent = Agent(
        model,
        deps_type=Deps,
        system_prompt=(DECISION_WORKFLOW_SYSTEM if decision_workflow else DECISION_LOOP_SYSTEM if decision_loop else
                      HYBRID_SYSTEM if hybrid else _SYSTEM + (("\n\n" + _PLAYBOOK) if _PLAYBOOK else "")),
        prepare_tools=prepare_planner_tools,
        retries=2,
        # Generous so a run_test_case verification (a full pytest run) is not cancelled mid-test.
        tool_timeout=240,
        model_settings=settings,
    )
    from multi_app_tools import register_multi_app_tools
    register_multi_app_tools(agent, Deps, _bro, _value_gate, emit)
    from decision_workflow_tools import register_decision_workflow
    register_decision_workflow(agent, Deps, _bro, _value_gate, emit)
    from decision_browser_tools import register_decision_browser
    register_decision_browser(agent, Deps, _bro, _value_gate, emit)

    async def _prepare_goal(ctx, definition):
        return definition if (ctx.deps.config.get("agent_execution") or {}).get("hybrid_enabled", False) else None

    @agent.tool(prepare=_prepare_goal, sequential=True)
    async def execute_goal(ctx: RunContext[Deps], goal: str, actions: list[GoalAction],
                           checkpoints: GoalCheckpoints | None = None, plan_step: int | None = None) -> dict:
        """Opt-in inner loop for up to eight already authorized actions on current refs.
        Concrete input values must be supplied. Stops on navigation, stale targets,
        no progress or uncertainty. checkpoints is an array of typed outcome objects.
        Optional plan_step updates that checklist step without extra planning tool calls.
        Use actions=[] with nonempty checkpoints for a deterministic read-only batch;
        this does not invoke the decision model or unlock mutation recovery.
        Still requires run_test_case for independent test verification.
        """
        if (ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled"):
            return {"ok": False, "status": "decision_loop_required"}
        if ctx.deps.session._multi_app_recording is not None:
            return {"ok": False, "status": "multi_app_active", "note": "Use multi_app_execute_goal for the isolated recorder"}
        if not (ctx.deps.config.get("agent_execution") or {}).get("hybrid_enabled", False):
            return {"ok": False, "status": "disabled", "note": "Use regular action tools"}
        if len(checkpoints or []) > 10:
            return {"ok": False, "status": "invalid_goal", "error": "At most ten outcome checkpoints"}
        if plan_step is not None and not 1 <= plan_step <= len(ctx.deps.plan):
            return {"ok": False, "status": "invalid_goal", "error": "plan_step must identify an existing checklist step"}
        def goal_progress(status):
            if plan_step is not None:
                ctx.deps.plan[plan_step - 1]["status"] = status
                emit({"event": "plan", "steps": [dict(p) for p in ctx.deps.plan]})
        async def record_checks():
            checks = []
            for cp in checkpoints or []:
                check = await _bro(ctx.deps.session.add_checkpoint, cp.name, cp.assert_type, cp.value, cp.ref)
                checks.append({"name": cp.name, **check})
                if not check.get("ok"):
                    break
            return checks
        if not actions:
            if not goal.strip() or not checkpoints:
                return {"ok": False, "status": "invalid_goal", "note": "Supply actions or at least one outcome checkpoint"}
            checks = await record_checks()
            passed = len(checks) == len(checkpoints) and all(c.get("ok") is True for c in checks)
            result = {"ok": passed, "status": "checks_completed" if passed else "checkpoint_failed",
                      "checkpoints": checks, "trace": [], "verified": False,
                      "attempted_actions": 0, "completed_actions": 0, "execution_state": "not_attempted",
                      "note": "Read-only assertions only; no mutation or decision call. Independent replay is still required."}
            goal_progress(goal_plan_status(result))
            emit({"event": "goal_execution", "result": result})
            return {**result, "observation": await _bro(ctx.deps.session.inspect, 40)}
        def authorized_targets():
            return {action.ref: ({"node": el["node_id"], "primary": dict(el.get("primary") or {})} if el.get("node_id") else None)
                    for action in actions for el in [ctx.deps.session.by_ref.get(action.ref, {})]}
        expected_targets = await _bro(authorized_targets)
        def snapshot():
            session = ctx.deps.session
            page = session.inspect(80)
            targets = {ref: {"node": el.get("node_id"), "primary": el.get("primary")}
                       for ref, el in session.by_ref.items()
                       if el.get("node_id") and el.get("visible", True) and not el.get("disabled")}
            return {"url": page.get("url"), "document": session.page.evaluate("() => window.__qamateNodes?.document"),
                    "state_hash": session._action_state(),
                    "targets": targets, "page": decision_page(page),
                    "values": {ref: (el.get("value"), el.get("checked")) for ref, el in session.by_ref.items()
                               if el.get("tag") in {"input", "select", "textarea"}}}
        async def observe_state():
            return await _bro(snapshot)
        async def perform(action):
            if action.kind == "fill":
                return await fill(ctx, action.ref, action.value)
            if action.kind == "select":
                return await select_option(ctx, action.ref, action.value)
            result = await click(ctx, action.ref)
            return result.return_value if isinstance(result, ToolReturn) else result
        before_goal = await _bro(ctx.deps.session._action_state)
        goal_key = ctx.deps.goal_handoffs.key(actions)
        if ctx.deps.goal_handoffs.blocked(before_goal, goal_key):
            ctx.deps.config.setdefault("agent_execution", {})["hybrid_enabled"] = False
            ctx.deps.recovery_actions = 0
            goal_progress("failed")
            emit({"event": "controller_fallback", "reason": "repeated_handoff"})
            return {"ok": False, "status": "controller_retry_exhausted",
                    "note": "Hybrid disabled for this session after repeated unchanged-state handoffs. "
                            "Use regular tools and preserve required assertions. Report fallback explicitly; do not retry execute_goal."}
        try:
            decision_name = ((ctx.deps.config.get("llm") or {}).get("roles") or {}).get("decision")
            if not decision_name:
                ctx.deps.config.setdefault("agent_execution", {})["hybrid_enabled"] = False
                emit({"event": "controller_fallback", "reason": "missing_decision_profile"})
                goal_progress("failed")
                return {"ok": False, "status": "not_configured", "terminal": True,
                        "note": "Hybrid disabled for this session. Bind llm.roles.decision before restarting; regular tools remain available."}
            goal_progress("active")
            ctx.deps.controller_active = True
            try:
                result = await execute_bounded(goal, actions, observe_state, perform,
                                               ChoiceDecider(ctx.deps.config, decision_name, usage_sink=emit), expected_targets=expected_targets)
            finally:
                ctx.deps.controller_active = False
            after_goal = await _bro(ctx.deps.session._action_state)
            ctx.deps.goal_handoffs.record(before_goal, goal_key, after_goal != before_goal)
            if result.get("ok"):
                result["checkpoints"] = await record_checks()
                if any(not c.get("ok") for c in result["checkpoints"]):
                    result["ok"] = False
                    result["status"] = "checkpoint_failed"
            ctx.deps.recovery_actions = 0 if result.get("ok") else 2
            goal_progress(goal_plan_status(result))
            emit({"event": "goal_execution", "result": result})
            result = dict(result)
            result["trace"] = [{k: row[k] for k in ("kind", "ref", "ok")} for row in result.get("trace", [])]
            result["observation"] = await _bro(ctx.deps.session.inspect, 40)
            if result.get("terminal"):
                ctx.deps.config.setdefault("agent_execution", {})["hybrid_enabled"] = False
                emit({"event": "controller_fallback", "reason": "terminal_decision_error"})
                result["note"] = "Decision provider rejected the request. Hybrid disabled for this session; fix its configuration before restarting. Regular tools remain available."
            return result
        except Exception as exc:
            ctx.deps.controller_active = False
            ctx.deps.recovery_actions = 2
            ctx.deps.goal_handoffs.record(before_goal, goal_key, False)
            goal_progress("failed")
            return {"ok": False, "status": "controller_error", "error": type(exc).__name__}

    @agent.tool
    async def request_tool_group(ctx: RunContext[Deps], group: str) -> dict:
        """Load extra tools: context, settings, diagnostics, specialized; browse resets the group.
        Does not unlock direct click/fill/select recovery or change permissions."""
        if group != "browse" and group not in GROUPS:
            return {"ok": False, "groups": ["browse", *GROUPS]}
        ctx.deps.tool_group = "" if group == "browse" else group
        from model_profiles import vision_unavailable_reason
        unavailable = vision_unavailable_reason(ctx.deps.config)
        tools = GROUPS.get(group, set()) - ({"look", "find_on_screen"} if unavailable else set())
        return {"ok": True, "group": group, "tools": sorted(tools),
                "vision_unavailable": unavailable,
                "note": "Use grounded DOM tools or report the unsupported control; switching groups does not enable vision." if unavailable else ""}

    @agent.tool
    async def navigate(ctx: RunContext[Deps], url: str) -> dict:
        """Navigate the browser to a URL (absolute http(s) URL). Returns the resulting URL,
        page title, and any new breakage issues triggered by the load.

        CRITICAL — if the result contains the key '⚠_AUTH_REQUIRED', read its value.
        It means either (a) you landed on a login form, or (b) API calls returned 401/403
        (expired session). In BOTH cases you MUST follow the steps in that message before
        doing anything else. DO NOT navigate to another URL. DO NOT continue the sequence.
        The sequence is SUSPENDED at this point until authentication is resolved."""
        if (ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled"):
            return {"ok": False, "status": "decision_loop_required"}
        return await _bro(ctx.deps.session.navigate, url)

    @agent.tool
    async def restart_browser(ctx: RunContext[Deps]) -> dict:
        """Relaunch the browser after it has closed or crashed (you saw a 'page/context/browser
        has been closed' error and navigate/inspect/click keep failing). Reuses the saved login.
        Your old refs become stale — call observe before acting again. Returns the
        post-restart url and title."""
        return await _bro(ctx.deps.session.restart)

    @agent.tool
    async def observe(ctx: RunContext[Deps], include_hidden: bool = False) -> dict:
        """Your ONE unified percept of the CURRENT page. Returns every interactive element
        (DOM + accessibility tree merged, including role-less clickable divs) with a stable
        'ref' you pass to click/fill/select_option. Ambiguous names are pre-disambiguated and
        interaction quirks (hidden inputs, number fields) are handled automatically when you
        act. Reading the list: 'repeats: N' = the SAME control appears on N cards/rows (act
        on the listed ref for the first one); 'ctx' = the page region (sidebar/nav/left-rail/
        dialog/...) — when two elements share a name, ctx tells the sidebar TAB from the
        in-content BUTTON, pick by region; 'href' on links = the navigation target (use it
        to reach detail pages on SPAs). Also includes: 'open_dropdown_options' if a dropdown
        is currently open, 'validation_errors' if the page shows form errors, and breakage
        issues (console errors, failed requests). Call this before acting, and again after
        any navigation or click that changes the page. include_hidden=true lists hidden
        elements too."""
        return await _bro(ctx.deps.session.inspect, 40, include_hidden)

    @agent.tool
    async def click(ctx: RunContext[Deps], ref: str) -> dict:
        """Click the element with the given ref (from the latest observe). Records the action
        as a test step. Hidden styled inputs (Tailwind radio/checkbox) are force-clicked
        automatically — you do not need a special tool. Returns ok, the new URL/title, and
        any breakage triggered. If the click opened a dropdown / dialog / menu, the result
        includes 'overlay_opened: true' AND a 'visual_state' description of what appeared.
        FALLBACK: if a ref is broken (e.g. MUI colon IDs like :r3:), pass a direct CSS selector
        instead — e.g. 'button[type="submit"]' or '[role="button"]'. Direct selectors are
        detected automatically and bypass the ref lookup."""
        if not direct_action_allowed(ctx.deps):
            return {"ok": False, "status": "controller_required", "note": "Use execute_goal with grounded refs"}
        result = await _bro(ctx.deps.session.act, "click", ref, None)
        if result.get("overlay_opened"):
            shot = await _bro(ctx.deps.session.screenshot)
            result["visual_state"] = await _call_vision_oracle(ctx.deps.config, shot)
        return result

    @agent.tool
    async def look(ctx: RunContext[Deps],
                   question: str = "Describe all interactive elements on screen, especially any "
                                   "open dropdowns with their options, dialogs, overlays, and "
                                   "any currently selected or highlighted items.") -> dict:
        """Take a screenshot and get a visual description of the current page state from the
        vision model. Use this when:
        - A dropdown, modal, or overlay is open and you need to see its options
        - observe() did not return an element you expected to see
        - You are about to interact with dynamic content (date-pickers, rich selects, etc.)
        - You are unsure what the page looks like right now
        Returns 'visual_description' — a literal description of what is currently on screen."""
        from model_profiles import vision_unavailable_reason
        unavailable = vision_unavailable_reason(ctx.deps.config)
        if unavailable:
            return {"ok": False, "status": "vision_unavailable", "reason": unavailable}
        try:
            shot = await _bro(ctx.deps.session.screenshot)
        except Exception as e:
            return {"ok": False, "error": f"screenshot failed: {e}"}
        description = await _call_vision_oracle(ctx.deps.config, shot, question)
        return {"ok": bool(description.strip()) and not description.startswith("(vision"), "visual_description": description}

    @agent.tool
    async def mouse_click(ctx: RunContext[Deps], x: float, y: float) -> dict:
        """LAST RESORT — click at pixel coordinates (x, y). Only for content with NO DOM
        element (canvas, SVG drawings) when find_on_screen returned raw coordinates
        instead of a ref. For everything else use click(ref) — coordinate clicks cannot
        be recorded into the generated test reliably.
        Internally snaps to the nearest interactive DOM element via elementFromPoint."""
        return await _bro(ctx.deps.session.mouse_click_coords, x, y)

    @agent.tool
    async def scan_page_errors(ctx: RunContext[Deps]) -> dict:
        """Scan the live DOM for visible validation errors, field errors, and alerts.
        Call this IMMEDIATELY after any action that triggers a 400/4xx API response,
        or whenever the page might be showing a validation message. Finds:
        - aria-invalid fields + their error descriptions
        - MUI FormHelperText error messages
        - role=alert elements (snackbars, inline alerts)
        - Generic error-class text elements
        Returns {count, errors: [{type, text}]}. Much more reliable than vision for
        small red text under form fields."""
        return await _bro(ctx.deps.session.scan_page_errors_dom)

    @agent.tool
    async def scroll_until_visible(ctx: RunContext[Deps], text: str) -> dict:
        """Scroll the page down until an element containing `text` becomes visible, then
        return a ref for it. Use this whenever the target is further down the page
        (lazy-loaded lists, 'appears as you scroll' elements) INSTEAD of scrolling and
        re-observing in a loop - ONE call does the whole hunt and records a single
        replay-safe scroll step. Returns {found, scrolls, ref} - then click(ref)."""
        return await _bro(ctx.deps.session.scroll_to_text, text)

    @agent.tool
    async def upload_file(ctx: RunContext[Deps], ref: str, file_path: str = "") -> dict:
        """Attach a file to an upload control (ready proof, tax invoice, product image,
        payment screenshot). Pass the ref of the upload button or file input from
        observe(). Leave file_path EMPTY to use the suite's standard test image —
        the generated test always replays with the standard fixture, so a custom
        path is rarely needed. Records a replay-safe upload step automatically."""
        return await _bro(ctx.deps.session.upload_file, ref, file_path)

    @agent.tool
    async def find_on_screen(ctx: RunContext[Deps], description: str) -> dict:
        """Find an element VISUALLY and get back a ref you can act on with click()/fill().
        Takes a screenshot, asks the vision model where the described element is, then
        resolves those pixels to the actual DOM element and registers it as a ref.
        Use when observe() did not list the element you can clearly see on screen.
        Returns {ref, role, name} on success — act on the ref like any other.
        Only if the target has NO DOM element (canvas/SVG drawing) you get raw {x, y}
        for mouse_click(). description example: 'the trash icon on the first table row'."""
        from model_profiles import vision_unavailable_reason
        unavailable = vision_unavailable_reason(ctx.deps.config)
        if unavailable:
            return {"ok": False, "status": "vision_unavailable", "reason": unavailable}
        try:
            shot = await _bro(ctx.deps.session.screenshot)
            dims = await _bro(ctx.deps.session.viewport_size)
        except Exception as e:
            return {"ok": False, "error": f"screenshot failed: {e}"}
        question = (
            f"Screenshot is {dims.get('width', 1280)}x{dims.get('height', 720)} pixels. "
            f"Find: \"{description}\". "
            f"Reply ONLY with valid JSON: {{\"x\": <number>, \"y\": <number>, "
            f"\"confidence\": \"high|medium|low\"}}. "
            f"x=0 is left edge, y=0 is top edge. "
            f"If not found: {{\"x\": null, \"y\": null, \"confidence\": \"none\"}}."
        )
        raw = await _call_vision_oracle(ctx.deps.config, shot, question)
        try:
            import re as _re
            m = _re.search(r'\{[^}]+\}', raw)
            if m:
                coords = json.loads(m.group())
                if coords.get("x") is not None:
                    x, y = float(coords["x"]), float(coords["y"])
                    ident = await _bro(ctx.deps.session.identify_at, x, y)
                    if ident.get("ok"):
                        ident["confidence"] = coords.get("confidence", "unknown")
                        ident["hint"] = f"Call click('{ident['ref']}') (or fill/select_option) to act on it."
                        return ident
                    return {"ok": True, "x": x, "y": y,
                            "confidence": coords.get("confidence", "unknown"),
                            "note": ident.get("error", ""),
                            "hint": f"No DOM element there — call mouse_click({x}, {y})."}
        except Exception:
            pass
        return {"ok": False, "raw_response": raw[:300],
                "hint": "Vision could not locate it. Re-check with look() or ask_user."}

    @agent.tool
    async def press_key(ctx: RunContext[Deps], key: str, times: int = 1) -> dict:
        """Press a keyboard key (optionally multiple times). Useful for:
        - Form navigation: Tab (next field), Shift+Tab (prev field)
        - Closing overlays: Escape
        - Text editing: Control+a (select all), Backspace, Delete
        NOTE: you do NOT need keyboard protocols for dropdowns/autocompletes —
        select_option(ref, value) runs them internally.
        key examples: 'ArrowDown', 'ArrowUp', 'Enter', 'Escape', 'Tab', 'Control+a'"""
        return await _bro(ctx.deps.session.press_key, key, times)

    @agent.tool
    async def wait_for_text(ctx: RunContext[Deps], text: str,
                             timeout_ms: int = 5000) -> dict:
        """Wait for specific text to appear visibly on the page (up to timeout_ms ms).
        Use AFTER fill() on a search/autocomplete field to wait for API results to load
        before trying to click an option. Much more reliable than a fixed sleep.
        Returns ok:True when the text appears, ok:False on timeout."""
        return await _bro(ctx.deps.session.wait_for_text_on_page, text, timeout_ms)

    @agent.tool
    async def fill(ctx: RunContext[Deps], ref: str, value: str) -> dict:
        """Type `value` into the input/textbox with the given ref. Records a test step.
        Number inputs are typed key-by-key automatically (MUI quirk) — just call fill.
        For a dropdown/autocomplete where you want an OPTION selected, call
        select_option(ref, value) instead — it types, waits, and picks the option.
        FALLBACK: if a ref is broken (e.g. MUI colon IDs like :r3:, :r5:), pass a direct CSS
        selector instead — e.g. 'input[type="email"]' or 'input[type="password"]' or
        'input[name="username"]'. Direct selectors are detected automatically and bypass
        the ref lookup. This is the correct approach for MUI login forms.
        Successful fills are automatically saved to the project input registry.
        In GUIDED mode a value that did not come from the user pauses the session
        for their input — this happens automatically, don't try to work around it."""
        if not direct_action_allowed(ctx.deps):
            return {"ok": False, "status": "controller_required", "note": "Use execute_goal with grounded refs"}
        value = await _value_gate(ctx, ref, value, "fill")
        result = await _bro(ctx.deps.session.act, "fill", ref, value)
        if ctx.deps.provenance and result.get("autocomplete_options"):
            ctx.deps.provenance.add_values(result["autocomplete_options"], "page-option")
        if result.get("ok") and value and value.strip():
            # Auto-record to input registry (skips sensitive fields automatically)
            el = ctx.deps.session.by_ref.get(ref, {})
            field_name = el.get("name") or el.get("label") or ref
            proj_id = (ctx.deps.project or {}).get("id") or ""
            _ireg.record(
                ats_root=ctx.deps.ats_root,
                project_id=proj_id or None,
                url=result.get("url", ""),
                field_name=str(field_name),
                value=value,
                field_type=el.get("type") or "text",
                selector=ref if BrowserSession._is_direct_selector(ref) else "",
            )
        return result

    @agent.tool
    async def get_input_registry(ctx: RunContext[Deps], url_filter: str = "") -> dict:
        """Read the project's input registry — all successful form-field values recorded
        across past sessions, grouped by URL route. Use this at the start of a task to
        discover what values are known to work for each page and field.
        Pass url_filter (e.g. '#/oms/cart') to narrow to a specific route.
        Returns a dict: { route: [ {field, type, value, ts, note?}, ... ] }"""
        proj_id = (ctx.deps.project or {}).get("id") or None
        data = _ireg.get(ctx.deps.ats_root, proj_id, url_filter)
        if not data:
            msg = "Input registry is empty" if not url_filter else f"No entries for '{url_filter}'"
            return {"ok": True, "entries": {}, "note": msg}
        total = sum(len(v) for v in data.values())
        return {"ok": True, "entries": data, "route_count": len(data), "total_inputs": total}

    @agent.tool
    async def record_input(ctx: RunContext[Deps], field_name: str, value: str,
                           field_type: str = "text", note: str = "") -> dict:
        """Manually save a successful input value to the project registry.
        Use this after selecting an autocomplete option, choosing a dropdown value,
        or any interaction where the auto-recorder couldn't capture the final value
        (e.g. the selected label differs from what was typed in the search box).
        field_type: 'text', 'autocomplete', 'dropdown', 'number', 'radio', 'checkbox'
        note: optional context (e.g. 'selected after typing Supertech')"""
        proj_id = (ctx.deps.project or {}).get("id") or None
        url = ctx.deps.session._url() if ctx.deps.session and ctx.deps.session.page else ""
        saved = _ireg.record(
            ats_root=ctx.deps.ats_root,
            project_id=proj_id,
            url=url,
            field_name=field_name,
            value=value,
            field_type=field_type,
            note=note,
        )
        if saved:
            return {"ok": True, "recorded": True, "field": field_name, "value": value}
        return {"ok": True, "recorded": False,
                "note": "Skipped — value is empty or field is sensitive (password/token/otp)"}

    @agent.tool
    async def select_option(ctx: RunContext[Deps], ref: str, value: str) -> dict:
        """Select `value` in ANY dropdown — native <select>, MUI/Radix/AntD combobox, portal
        autocomplete, or fully custom div-dropdown. ONE call does everything internally:
        type-to-search, wait for async options, option click, keyboard fallback, vision
        fallback. Records the test steps for whichever path worked, and saves the selected
        label to the input registry automatically.
        ref: the field/trigger ref from observe() (direct CSS selectors also accepted).
        value: what you want selected — exact option text, or a search term; the closest
        option is matched (decorated labels like 'NAME(phone)LEGAL NAME' are handled).
        If the result has blocked:true, read options_seen — retry once with an exact text
        from there, otherwise ask_user. Do NOT improvise other tools for dropdowns.
        In GUIDED mode a value that did not come from the user pauses the session
        for their input — this happens automatically, don't try to work around it."""
        if not direct_action_allowed(ctx.deps):
            return {"ok": False, "status": "controller_required", "note": "Use execute_goal with grounded refs"}
        value = await _value_gate(ctx, ref, value, "select in")
        res = await _bro(ctx.deps.session.select_from_dropdown, ref, value)

        # Vision tier (last resort): locate the option on screen, snap to DOM, click.
        if not res.get("ok") and res.get("blocked"):
            try:
                shot = await _bro(ctx.deps.session.screenshot)
                dims = await _bro(ctx.deps.session.viewport_size)
                q = (f"Screenshot is {dims.get('width', 1280)}x{dims.get('height', 720)} pixels. "
                     f"Find the dropdown option matching \"{value}\" in the open dropdown/list. "
                     f"Reply ONLY with valid JSON: {{\"x\": <number>, \"y\": <number>}}. "
                     f"If no such option is visible: {{\"x\": null, \"y\": null}}.")
                raw = await _call_vision_oracle(ctx.deps.config, shot, q)
                m = re.search(r'\{[^}]+\}', raw or "")
                if m:
                    c = json.loads(m.group())
                    if c.get("x") is not None:
                        clicked = await _bro(ctx.deps.session.mouse_click_coords,
                                             float(c["x"]), float(c["y"]))
                        if clicked.get("ok"):
                            await _bro(ctx.deps.session._record_option_click, value, "vision")
                            res = {"ok": True, "selected": value, "via": "vision",
                                   "url": clicked.get("url", ""),
                                   "verify": "selected via vision — confirm the field shows the intended value"}
            except Exception:
                pass

        # Values the page itself displayed are legitimate provenance (the user can
        # see them) — without this, retrying with an exact options_seen text would
        # re-trigger the guided gate.
        if ctx.deps.provenance:
            ctx.deps.provenance.add_values(res.get("options_seen"), "page-option")
            if res.get("selected"):
                ctx.deps.provenance.add_value(res["selected"], "page-option")

        # Auto-record the final selected label so future sessions reuse it (the
        # displayed label often differs from the typed search term).
        if res.get("ok") and res.get("selected"):
            try:
                el = ctx.deps.session.by_ref.get(ref, {})
                field_name = el.get("name") or el.get("placeholder") or ref
                proj_id = (ctx.deps.project or {}).get("id") or ""
                _ireg.record(
                    ats_root=ctx.deps.ats_root, project_id=proj_id or None,
                    url=res.get("url", ""), field_name=str(field_name),
                    value=str(res["selected"]), field_type="autocomplete",
                    note=(f"selected after typing '{value}'" if res["selected"] != value else ""),
                )
            except Exception:
                pass
        return res

    @agent.tool
    async def mark_step_manual(ctx: RunContext[Deps], prompt: str) -> dict:
        """Call IMMEDIATELY AFTER filling a field with a ONE-TIME value a human gave you
        via ask_user (an OTP from their phone, an SMS/email code, a captcha answer).
        It converts that last fill into a MANUAL-INPUT GATE: when the generated test
        runs later, it PAUSES at this step, shows `prompt` to the human, and resumes
        with whatever they type — instead of replaying the now-expired code.
        prompt example: 'Enter the OTP sent to +91-98xxxxx680'.
        Typical OTP sign-up flow: fill phone -> click Send OTP -> ask_user for the
        code -> fill the OTP field -> mark_step_manual -> continue the flow."""
        return await _bro(ctx.deps.session.mark_step_manual, prompt)

    @agent.tool
    async def add_checkpoint(ctx: RunContext[Deps], name: str, assert_type: str, value: str, ref: str = "") -> dict:
        """Record a verification checkpoint after the latest step. assert_type must be
        'url_contains', 'page_contains_text', 'page_not_contains_text' for case-sensitive
        substrings, or 'element_has_value'
        with a current ref and exact expected input/select value. A product's presence
        does not prove sorting: verify the selected option's value explicitly."""
        if (ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled"):
            return {"ok": False, "status": "decision_loop_required"}
        if assert_type not in ("url_contains", "page_contains_text", "page_not_contains_text", "element_has_value"):
            return {"ok": False, "error": "Use url_contains, page_contains_text, page_not_contains_text, or element_has_value (requires ref)"}
        return await _bro(ctx.deps.session.add_checkpoint, name, assert_type, value, ref)

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
        if ((ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled")
                and not getattr(ctx.deps, "decision_browser_complete", False)):
            return {"ok": False, "status": "outcomes_incomplete"}
        return await _bro(ctx.deps.session.create_test, ctx.deps.ats_root, ctx.deps.project,
                          tc_id, flow_id, description, preconditions, expected_result)

    @agent.tool
    async def save_user_story(ctx: RunContext[Deps], tc_id: str, flow_id: str,
                               summary: str, role: str, want: str, benefit: str,
                               acceptance_criteria: list, test_steps: list) -> dict:
        """Save a user story and step-by-step test steps for a test case.
        Call this right after create_test_case to populate the user story JSON.
        - acceptance_criteria: list of strings, one per acceptance criterion.
        - test_steps: list of {step, action, expected} dicts matching the spec table.
        Writes to tests/flows/<flow_id>/<flow_id>_user_stories.json."""
        ats_root = ctx.deps.ats_root
        flow_dir = os.path.join(_tests_root_for(ats_root, ctx.deps.project), "flows", flow_id)
        if not os.path.isdir(flow_dir):
            return {"status": "error", "message": f"flow '{flow_id}' not found"}
        stories_path = os.path.join(flow_dir, f"{flow_id}_user_stories.json")
        try:
            existing = _read_json_file(stories_path, {})
            existing[tc_id] = {
                "user_story": {
                    "summary": summary,
                    "role": role,
                    "want": want,
                    "benefit": benefit,
                    "acceptance_criteria": acceptance_criteria or [],
                },
                "test_steps": test_steps or [],
            }
            with open(stories_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            return {"status": "success", "tc_id": tc_id, "flow": flow_id,
                    "steps_count": len(test_steps or []),
                    "criteria_count": len(acceptance_criteria or [])}
        except Exception as e:
            return {"status": "error", "message": str(e)}

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
        if not re.fullmatch(r"[A-Za-z0-9_-]+", flow_id) or not re.fullmatch(r"TC-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", tc_id):
            return {"ok": False, "error": "Invalid flow or test identifier"}
        tests_root = _tests_root_for(ats_root, ctx.deps.project)
        flow_dir = os.path.join(tests_root, "flows", flow_id)
        if not os.path.isdir(tests_root) or not os.path.isdir(flow_dir):
            return {"ok": False, "error": f"flow '{flow_id}' not found under tests/flows"}
        underscored = tc_id.replace("-", "_")
        base_env = os.environ.copy()
        base_env["ATS_ROOT"] = ats_root
        base_env["ATS_APP_ROOT"] = _APP_ROOT
        base_env["PYTHONPATH"] = _APP_ROOT
        base_env["PYTHONUNBUFFERED"] = "1"
        proj = ctx.deps.project or {}
        if proj.get("id"):
            base_env["ATS_PROJECT_ID"] = proj["id"]
        base_env.setdefault("ATS_ENV", os.environ.get("ATS_ENV") or "dev")
        # The verify loop is unattended — a manual_input() gate (OTP) must skip,
        # not hang for its timeout. Detected below and reported honestly.
        base_env["ATS_NO_MANUAL_INPUT"] = "1"
        import uuid
        from verification import verified_junit
        verify_root = os.path.join(ats_root, "results", "_agent_verify", uuid.uuid4().hex)

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
            os.makedirs(rdir, exist_ok=True)
            junit = os.path.join(rdir, "junit.xml")
            env = dict(base_env)
            env["ATS_RESULTS_DIR"] = rdir   # known dir so we can find the failure screenshot
            # Authoring checks only need pass/fail + the failure screenshot: skip video,
            # tracing and API-body capture (the Run button keeps full diagnostics).
            env.update({"ATS_VIDEO": "off", "ATS_TRACING": "off", "ATS_NETWORK": "off"})
            cmd = [sys.executable, "-m", "pytest", flow_dir, "-k", underscored,
                   "--tb=short", "-q", "-ra", "-p", "no:cacheprovider", "--tracing=off", f"--junitxml={junit}"]
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
            return {"passed": (rc == 0 and not no_tests and verified_junit(junit, tc_id)), "rc": rc, "text": text, "rdir": rdir, "no_tests": no_tests}

        r1 = await _one(1)
        if r1["no_tests"]:
            return {"ok": True, "passed": False, "exit_code": r1["rc"],
                    "error": (f"no test matched '{underscored}' in flow '{flow_id}' — confirm "
                              "create_test_case succeeded and the tc_id/flow_id are right")}
        if "requires manual input" in r1["text"]:
            return {"ok": True, "passed": False, "manual_input_gate": True,
                    "note": ("This test PAUSES for a human value (e.g. an OTP) — it cannot be "
                             "verified unattended. It skipped cleanly here, which confirms the "
                             "gate is wired. Tell the user to run it from the app's RUN button "
                             "and answer the amber prompt; do NOT keep re-running it here and "
                             "do NOT treat this as a failure to fix.")}
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
        shot, err, page_text = _find_failure_artifacts(failed["rdir"])
        if err:
            result["error_detail"] = err[:800]
        # Page text (what was visible on screen) — works for non-vision models via DOM extraction
        if page_text:
            result["page_text_on_failure"] = ("Visible page content when test failed:\n" + page_text)
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
        if not re.fullmatch(r"[A-Za-z0-9_-]+", flow_id) or not re.fullmatch(r"TC-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", tc_id):
            return {"ok": False, "error": "Invalid flow or test identifier"}
        path = os.path.join(_tests_root_for(ctx.deps.ats_root, ctx.deps.project),
                            "flows", flow_id, f"test_{flow_id}.py")
        if not os.path.isfile(path):
            path = os.path.join(_tests_root_for(ctx.deps.ats_root, ctx.deps.project),
                                "flows", flow_id, "test_" + tc_id.lower().replace("-", "_") + ".py")
        if not os.path.isfile(path):
            return {"ok": False, "error": f"test file for flow '{flow_id}' not found"}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
        extra = {}
        if os.path.basename(path) == "test_" + tc_id.lower().replace("-", "_") + ".py":
            workflow_path = os.path.join(os.path.dirname(path), "workflow.json")
            if os.path.isfile(workflow_path):
                try:
                    from multi_app import Workflow
                    with open(workflow_path, encoding="utf-8") as workflow_file:
                        raw = workflow_file.read(50001)
                    extra = ({"workflow": Workflow.model_validate_json(raw).model_dump()} if len(raw) <= 50000
                             else {"workflow_error": "Workflow exceeds diagnostic read limit"})
                except Exception:
                    extra = {"workflow_error": "Workflow is not valid typed replay data"}
        func = "test_" + tc_id.replace("-", "_")
        m = re.search(r'(\n@pytest\.mark\.tc\("' + re.escape(tc_id) + r'"\)\ndef '
                      + re.escape(func) + r'\(.*?)(?=\n@pytest\.mark|\Z)', content, re.DOTALL)
        if m:
            return {"ok": True, "flow": flow_id, "tc_id": tc_id, "code": m.group(1).strip()[:6000], **extra}
        return {"ok": True, "flow": flow_id, "tc_id": tc_id, "code": content[:6000],
                "note": "exact function not found; returning the file head", **extra}

    @agent.tool
    async def clear_recording(ctx: RunContext[Deps], reason: str = "") -> dict:
        """Archive and clear for a concrete defect or different scenario, not cosmetic cleanup.
        With checkpoints, first create/replay; a restart requires a specific reason (20+ chars).
        Expected validation errors are test outcomes, not defects. Latest five snapshots are
        recoverable during this session via restore_recording; browser state is not reset."""
        result = await _bro(clear_saved_recording, ctx.deps.session, reason)
        emit({"event": "recording_change", "action": "clear", "ok": result.get("ok"),
              "snapshot_id": result.get("snapshot_id"), "status": result.get("status")})
        return result

    @agent.tool
    async def restore_recording(ctx: RunContext[Deps], snapshot_id: str) -> dict:
        """Restore a session-local recording snapshot listed by get_recorded_flow or clear_recording.
        Preserves displaced work. Restores steps, checkpoints and provenance notes, NOT the browser
        or a pass verdict. Create/replay it to verify. Observe before further browser actions."""
        result = await _bro(ctx.deps.session.recording_history.restore, ctx.deps.session, snapshot_id)
        emit({"event": "recording_change", "action": "restore", "ok": result.get("ok")})
        return result

    @agent.tool
    async def delete_test_case(ctx: RunContext[Deps], tc_id: str, flow_id: str) -> dict:
        """Delete a test case from a flow — removes it from test_cases.json, test_data.json AND the
        test_<flow>.py function. Use it to clean up a stub or a test you replaced. Confirm with the
        user before deleting anything you did not just create yourself."""
        return _delete_test_case(ctx.deps.ats_root, flow_id, tc_id,
                                 tests_dir=_tests_root_for(ctx.deps.ats_root, ctx.deps.project))

    # ── ATS app features: use the tool itself AS A USER (read/change settings,
    # browse the suite, read reports). File-backed — no browser involved. ──

    @agent.tool
    async def get_settings(ctx: RunContext[Deps]) -> dict:
        """Read the ATS app Settings (config.json): environments, platform accounts
        (seller/admin) WITH their login credentials, execution defaults, LLM providers,
        pass criteria, and Jira project. Use this to look up credentials, URLs, or any
        configured option a user would see in Settings (the Jira API token is redacted)."""
        if (ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled"):
            settings = {"apps": [{"label": a.get("label"), "url": a.get("url")} for a in (ctx.deps.project or {}).get("apps", [])],
                    "policy": (ctx.deps.config.get("agent_execution") or {}).get("decision_loop_policy", "read_only"),
                    "public_demo_auth": (ctx.deps.config.get("agent_execution") or {}).get("public_demo_auth") is True,
                    "policy_note": "Network writes are blocked. session_local_forms admits configured isolated demo interactions. Public demo login requires public_demo_auth=true and task-supplied public credentials; private login is unsupported."}
            # Bootstrap facts from the already-open page, without choosing or
            # dispatching navigation. Do not give the compiler action refs,
            # input values or credentials. The controller still owns browsing.
            if ctx.deps.session.page is not None:
                def entry_facts():
                    from decision_browser import origin
                    current = ctx.deps.session._url()
                    if origin(current) not in {origin(a["url"]) for a in settings["apps"] if a.get("url")}:
                        return {"status": "outside_registered_origin"}
                    observation = ctx.deps.session.inspect(80)
                    if not observation.get("ok"):
                        return {"status": "observation_unavailable"}
                    controls = [{"name": e.get("name") or e.get("placeholder"), "role": e.get("role"),
                                 "tag": e.get("tag"), "href": e.get("href")}
                                for e in ctx.deps.session.by_ref.values() if e.get("visible", True)]
                    return {"url": current, "title": observation.get("title"), "controls": controls[:80],
                            "controls_truncated": len(controls) > 80,
                            "scope": "Already-open entry page only; not proof of deeper pages or body-text assertions"}
                settings["entry_observation"] = await _bro(entry_facts)
            return settings
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
        flows_dir = os.path.join(_tests_root_for(ctx.deps.ats_root, ctx.deps.project), "flows")
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
        tcf = os.path.join(_tests_root_for(ctx.deps.ats_root, ctx.deps.project),
                           "flows", flow_id, "test_cases.json")
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
        if ctx.deps.provenance:
            # User-supplied docs are legitimate value provenance (PRDs carry test data).
            ctx.deps.provenance.add_text(text, "context-file")
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
        if ctx.deps.provenance:
            ctx.deps.provenance.add_text(text, "context-file")
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
            if isinstance(flows, dict):
                # Single flow returned as a bare dict (not wrapped in [])
                if "id" in flows and "name" in flows:
                    flows = [flows]
                else:
                    # Wrapped: {"flows": [...]} or similar
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

    @agent.tool
    async def clear_auth_storage(ctx: RunContext[Deps]) -> dict:
        """Wipe localStorage, sessionStorage, and cookies for the current origin so the
        browser forgets any stale session token. Use this ONLY when navigate() returned
        ⚠_AUTH_REQUIRED because API calls returned 401/403 but NO login form was visible
        (i.e. the frontend still thinks it is logged in). After calling this, navigate to
        the login URL and fill credentials. Do NOT call this on a clean session."""
        def _clear():
            try:
                page = ctx.deps.session.page
                url = page.url
                try:
                    page.evaluate("() => { try { localStorage.clear(); } catch(e){} "
                                  "try { sessionStorage.clear(); } catch(e){} }")
                except Exception:
                    pass
                try:
                    ctx.deps.session.context.clear_cookies()
                except Exception:
                    pass
                return {"ok": True, "cleared_for": url,
                        "next": "Navigate to the login URL and fill credentials."}
            except Exception as e:
                return {"ok": False, "error": str(e)[:200]}
        return await _bro(_clear)

    @agent.tool
    async def ask_user(ctx: RunContext[Deps], question: str) -> str:
        """Pause the current task and ask the user a question. MANDATORY in these situations:
        - You are on a login form and the saved session was not accepted (credentials missing
          or wrong) — ask for the correct email/password. Do NOT navigate away or skip to
          another app. The sequence is suspended here until you get the answer.
        - You need an OTP, a confirmation code, or any value only the user can supply.
        - A step is blocked and you cannot proceed without user input (wrong element, form
          rejection, unexpected state, missing data).
        - You are unsure which of multiple options to choose.
        NEVER skip a blocked step and continue with the next item — always call this first.
        The session pauses, the user sees your question highlighted in the chat, types their
        answer, and you resume from exactly this point. Returns whatever the user typed."""
        if ctx.deps.user_input_q is None:
            return "(no input channel — run_explore.py mode; cannot pause for user input)"
        emit({"event": "input_required", "question": question})
        ctx.deps._ask_state["waiting"] = True
        try:
            answer = await ctx.deps.user_input_q.get()
        finally:
            ctx.deps._ask_state["waiting"] = False
        if ctx.deps.provenance:
            ctx.deps.provenance.add_text(str(answer), "user-reply")
        # Wrap with a directive so the LLM doesn't respond conversationally — it must
        # immediately continue the task sequence from the step that was suspended.
        return (f"<user_reply>{answer}</user_reply>\n"
                "TASK RESUMES NOW — execute the next pending step immediately. "
                "Do NOT acknowledge the reply in text. Just act.")

    @agent.tool
    async def skip_step(ctx: RunContext[Deps], step_description: str, reason: str) -> dict:
        """Formally skip a planned step or checkpoint you cannot complete. Silent skipping
        is FORBIDDEN — if you must skip, it goes through this tool so the run report shows
        it honestly. In GUIDED mode the user must approve the skip first (the session
        pauses); they may instead tell you how to proceed — then DO that, don't skip.
        step_description: what you are skipping. reason: why it cannot be done."""
        deps = ctx.deps
        if deps.mode == "guided" and deps.user_input_q is not None:
            question = (f"GUIDED MODE — skip request: the agent wants to SKIP "
                        f"\"{step_description}\" because: {reason}. "
                        f"Reply 'ok' to allow the skip, or tell it what to do instead.")
            emit({"event": "input_required", "question": question, "kind": "guided_skip"})
            deps._ask_state["waiting"] = True
            try:
                answer = str(await deps.user_input_q.get()).strip()
            finally:
                deps._ask_state["waiting"] = False
            if deps.provenance:
                deps.provenance.add_text(answer, "user-reply")
            if answer.casefold() not in _APPROVAL_WORDS:
                return {"ok": False, "skipped": False,
                        "user_instruction": answer,
                        "directive": "The user did NOT approve the skip. Follow their "
                                     "instruction above instead, immediately."}
        deps.session.skips.append({"step": str(step_description)[:200],
                                   "reason": str(reason)[:300]})
        emit({"event": "log", "message": f"SKIPPED: {step_description} - {reason}"})
        return {"ok": True, "skipped": True,
                "note": "Recorded as an explicit skip — it will appear in the final report. "
                        "Mention it when you summarize."}

    _PLAN_STATUSES = ("pending", "active", "done", "failed", "skipped")

    @agent.tool
    async def set_plan(ctx: RunContext[Deps], steps: list) -> dict:
        """Publish your work plan as a LIVE CHECKLIST the user watches while you work.
        Call this FIRST for any multi-step task (3+ actions): pass the step descriptions
        in order (short, outcome-focused — 'Select customer Supertech', not 'call fill').
        Replaces any previous plan. Then keep it honest with update_plan as you progress.
        A visible, ticking plan is how the user trusts what you are doing."""
        plan = [{"step": str(s).strip()[:160], "status": "pending"}
                for s in (steps or [])[:30] if str(s).strip()]
        if not plan:
            return {"ok": False, "error": "steps must be a non-empty list of step descriptions"}
        ctx.deps.plan[:] = plan
        emit({"event": "plan", "steps": list(plan)})
        return {"ok": True, "steps": len(plan),
                "note": ("Plan is visible. Pass plan_step to execute_goal for automatic progress; "
                         "batch other updates with useful work." if
                         (ctx.deps.config.get("agent_execution") or {}).get("hybrid_enabled") else
                         "Plan is now visible. Call update_plan(step_number, 'active') when you "
                         "start a step and 'done'/'failed'/'skipped' when it finishes.")}

    @agent.tool
    async def update_plan(ctx: RunContext[Deps], step_number: int, status: str,
                          note: str = "") -> dict:
        """Update one checklist step (1-based step_number) to: 'active' (working on it now),
        'done', 'failed', 'skipped', or back to 'pending'. Optional short note shown next to
        the step (e.g. the value used, or why it failed). Update IMMEDIATELY as you work —
        a stale checklist is worse than none."""
        plan = ctx.deps.plan
        if not plan:
            return {"ok": False, "error": "no plan set — call set_plan first"}
        i = int(step_number) - 1
        if not (0 <= i < len(plan)):
            return {"ok": False, "error": f"step_number must be 1..{len(plan)}"}
        status = str(status).strip().lower()
        if status not in _PLAN_STATUSES:
            return {"ok": False, "error": f"status must be one of {_PLAN_STATUSES}"}
        plan[i]["status"] = status
        if note:
            plan[i]["note"] = str(note)[:160]
        emit({"event": "plan", "steps": list(plan)})
        return {"ok": True,
                "plan": [f"{j + 1}. [{p['status']}] {p['step']}" for j, p in enumerate(plan)]}

    @agent.instructions
    async def _project_memory_and_context(ctx: RunContext[Deps]) -> str:
        """Injected fresh each turn (instructions are regenerated per run and not stored in
        history): the operating mode, the memory file's contents + a short listing of
        available context files."""
        blocks = []
        multi_active = getattr(ctx.deps.session, "_multi_app_recording", None) is not None
        if multi_active:
            blocks.append("ACTIVE MULTI-APP: isolated app/actor contexts; no shared auth. Reuse returned observations. Report unsupported auth or terminal blockers; never switch to legacy browser tools.")
        if not multi_active and ((ctx.deps.project or {}).get("auth") or {}).get("type") == "none":
            blocks.append("PROJECT AUTH: auth.type is none. Do not assume the test runner logs in. "
                          "If this flow needs login, preserve its recorded login steps so a fresh "
                          "browser can replay them. A logged-in authoring page is not replay authentication.")
        decision_owned = bool((ctx.deps.config.get("agent_execution") or {}).get("decision_loop_enabled"))
        if decision_owned:
            blocks.append("DECISION-OWNED BROWSING: compile one browse_goal contract with inputs and milestones. No action refs, per-click planning or legacy execute_goal. Readiness probes are deterministic; the decision model owns all browser choices. Do not repeat a failed contract or weaken outcomes.")
        if not multi_active and not decision_owned and (ctx.deps.config.get("agent_execution") or {}).get("hybrid_enabled", False):
            blocks.append("HYBRID EXECUTION: Prefer execute_goal for a small group of authorized actions "
                          "on refs in the current observation. Supply input values explicitly. It hands "
                          "back control on navigation or uncertainty. actions_completed is NOT success "
                          "verification: add outcome checkpoints and independently replay the test.")
        if decision_owned:
            blocks.append("Use only approved non-secret inputs. Unknown values still follow GUIDED/AUTO provenance. Unsupported or uncertain outcomes remain blocked; do not bypass the controller.")
        elif multi_active:
            blocks.append("GUIDED: let unknown-value approval pause; never bypass it." if ctx.deps.mode == "guided" else
                          "AUTO: user-provided values preferred; assumed values remain recorded. Report blocked or skipped outcomes, never silently omit them.")
        elif ctx.deps.mode == "guided":
            blocks.append(
                "OPERATING MODE: GUIDED. The user wants to be consulted. fill/select_option "
                "automatically pause for the user whenever a value did not come from them — "
                "let that happen, never substitute a different made-up value to avoid the pause. "
                "Skipping anything requires skip_step (which asks the user). When in doubt "
                "between options, prefer ask_user over guessing.")
        else:
            blocks.append(
                "OPERATING MODE: AUTO. Work autonomously. If you must use a value the user "
                "did not provide, prefer the input registry; otherwise use a sensible value — "
                "it is recorded as an assumption and reported for review. Skips still go "
                "through skip_step. Blocked steps still mean: try harder or ask_user.")
        try:
            apps = project_store.project_apps(ctx.deps.project) if ctx.deps.project else []
            if apps:
                lines = []
                for a in apps:
                    cred = {} if multi_active or decision_owned else a.get("credentials") or {}
                    lines.append(f"- {a['label']}: {a['url']}"
                                 + (f"  (login: {cred['email']} / {cred['password']})"
                                    if cred.get("email") else ""))
                blocks.append(
                    "PROJECT APPS — every base URL this project tests (a project can span "
                    "several surfaces, e.g. a storefront AND an admin panel). Navigate between "
                    "them as the task requires:\n" + "\n".join(lines))
        except Exception:
            pass
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
    from model_profiles import build_agent_model
    return build_agent_model(config, provider_name)


def _resolve_credentials(project, config, platform_name="seller", index=0):
    """Where the agent's login comes from, in priority order:
      1. the project's PRIMARY app credentials (project.apps[0]),
      2. the project's own settings (project.auth.credentials), then
      3. config.json platforms.<platform>.users[index]  (the legacy 'system settings').
    Returns (email, password) or (None, None). The agent never types these — the
    session logs in once with them and reuses the saved storage_state thereafter."""
    apps = project_store.project_apps(project) if project else []
    if apps:
        c0 = apps[0].get("credentials") or {}
        if c0.get("email") and c0.get("password"):
            return c0["email"], c0["password"]
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
                content = getattr(part, "content", "")
                retry = getattr(part, "part_kind", "") == "retry-prompt"
                outcome = {"retry": retry, "validation_failed": retry and isinstance(content, list),
                           "ok": False if retry else (content.get("ok") if isinstance(content, dict) else None)}
                # 1500 keeps most results parseable JSON for the UI's structured
                # cards (tables/highlighting); parse failures degrade to plain text.
                emit({"event": "tool_result", "tool": getattr(part, "tool_name", ""),
                      "outcome": outcome, "summary": _short(content, 1500)})
            elif kind == "PartStartEvent":
                part = ev.part
                pk = getattr(part, "part_kind", "")
                if pk == "text" and getattr(part, "content", ""):
                    emit({"event": "text", "delta": part.content})
                elif pk == "thinking" and getattr(part, "content", ""):
                    emit({"event": "thinking", "delta": part.content})
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
        self.tool_budget = TOOL_BUDGET_DEFAULT  # max tool calls per turn; 0 = unlimited
        self.session_tokens = {"input": 0, "output": 0, "total": 0}  # cumulative token usage
        self.ready_info = None   # the 'ready' payload, re-emitted on reattach
        self._user_input_q: asyncio.Queue = asyncio.Queue()  # ask_user tool drains this
        self._chat_task: Optional[asyncio.Task] = None       # current running chat turn
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
        self.config["llm"] = {**(self.config.get("llm") or {}), "_session_provider": pname}

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

        # Collect extra storage states (e.g. admin panel JWT) to inject via init_script.
        extra_init_states = []
        if project_id and project:
            admin_cfg = (project or {}).get("admin") or {}
            admin_ss_rel = admin_cfg.get("storage_state")
            if admin_ss_rel:
                admin_ss_path = os.path.join(self.ats_root, "projects", project_id, admin_ss_rel)
                if os.path.exists(admin_ss_path):
                    try:
                        with open(admin_ss_path, "r", encoding="utf-8") as _f:
                            extra_init_states.append(json.load(_f))
                        log(f"[agent] admin panel auth found: {admin_ss_path}")
                    except Exception as _e:
                        log(f"[agent] could not load admin storage state: {_e}")

        _backend = (self.config.get("browser_backend") or "native").lower()
        if _backend == "mcp":
            from mcp_browser import MCPBrowserSession
            self.session = MCPBrowserSession()
            log("[agent] browser backend: Playwright MCP server (experimental)")
        else:
            self.session = BrowserSession()
        try:
            if _backend == "mcp":
                info = await _bro(self.session.start, start_url, load_state, headless,
                                  creds, ss, True, extra_init_states)
            else:
                info = await _bro(self.session.start, start_url, load_state, headless, creds, ss,
                                  True, extra_init_states, self.config.get("browser") or {})
        except Exception as e:
            emit({"event": "error", "message": f"browser failed to start: {e}"})
            return

        prov_cfg = ((self.config.get("llm", {}) or {}).get("providers", {}) or {}).get(pname, {}) or {}
        self.max_tokens = int(prov_cfg.get("max_tokens") or AGENT_MAX_TOKENS)
        from model_profiles import resolve_profile
        _, resolved_profile = resolve_profile(self.config, pname)
        hybrid = bool((self.config.get("agent_execution") or {}).get("hybrid_enabled"))
        self.agent = build_agent(TurnCompactingModel(model, compact=hybrid,
            multi_app_active=lambda: getattr(self.session, "_multi_app_recording", None) is not None),
            self.max_tokens, resolved_profile, hybrid=hybrid,
            decision_loop=bool((self.config.get("agent_execution") or {}).get("decision_loop_enabled")),
            decision_workflow=bool((self.config.get('agent_execution') or {}).get('multi_app_decision_loop_enabled')))
        # Tool budget: from init command > env var default. 0 = unlimited (no pause between turns).
        _tb = cmd.get("tool_budget")
        if _tb is not None:
            self.tool_budget = max(0, int(_tb))
        else:
            self.tool_budget = TOOL_BUDGET_DEFAULT
        # Vision-capable? -> run_test_case can hand failure SCREENSHOTS back to the model.
        # Default is OFF — many model endpoints (incl. mimo-v2.5-pro) reject image input with 404.
        # Enable explicitly via ATS_AGENT_VISION=on or per-provider "vision": true in config.json.
        _vis = (os.environ.get("ATS_AGENT_VISION") or "").lower()
        _prov_vision = bool((prov_cfg.get("capabilities") or {}).get("vision", prov_cfg.get("vision")))
        vision = (_vis == "on") or (_vis != "off" and _prov_vision)
        ctx_dir = project_store.resolve_context_dir(self.ats_root, project_id, project)
        mem_path = project_store.resolve_memory_path(self.ats_root, project_id)
        mode = str(cmd.get("agentMode") or "auto").lower()
        if mode not in ("auto", "guided"):
            mode = "auto"
        tracker = ProvenanceTracker()
        # Seed legitimate sources: project memory (durable app knowledge), settings
        # credentials/URLs, and past known-good inputs from the registry.
        try:
            if mem_path and os.path.isfile(mem_path):
                tracker.add_text(open(mem_path, "r", encoding="utf-8", errors="replace").read(),
                                 "project-memory")
        except Exception:
            pass
        tracker.add_values(settings_values(self.config), "settings")
        tracker.add_values(registry_values(self.ats_root, project_id), "input-registry")
        for _app in (project_store.project_apps(project) if project else []):
            _c = _app.get("credentials") or {}
            tracker.add_values([_app.get("url"), _c.get("email"), _c.get("password")], "settings")
        self.deps = Deps(session=self.session, ats_root=self.ats_root, project=project,
                         config=self.config, context_dir=ctx_dir, memory_path=mem_path, vision=vision,
                         user_input_q=self._user_input_q, mode=mode, provenance=tracker)
        # Restore the model's conversation memory on resume (this is what lets it "continue
        # from a point"); a fresh session starts empty. Then mark the session running and load
        # the saved transcript so the window can redraw the visible chat.
        self.history = _compact_history(
            agent_sessions.load_messages(self.ats_root, project_id, self.session_id)) if resumed else []
        if resumed:
            _t = (agent_sessions.read_session(self.ats_root, project_id, self.session_id) or {}).get("tokens") or {}
            self.session_tokens = {"input": int(_t.get("input", 0) or 0),
                                   "output": int(_t.get("output", 0) or 0),
                                   "total": int(_t.get("total", 0) or 0),
                                   "estimated": bool(_t.get("estimated")),
                                   "usage_incomplete": bool(_t.get("usage_incomplete"))}
        else:
            self.session_tokens = {"input": 0, "output": 0, "total": 0}
        try:
            agent_sessions.update_session_meta(self.ats_root, project_id, self.session_id,
                {"status": "running", "provider": pname, "model": model_name, "env": (cmd.get("env") or "")})
        except Exception:
            pass
        transcript = agent_sessions.load_transcript(self.ats_root, project_id, self.session_id) if resumed else []
        if resumed:
            try:
                meta0 = agent_sessions.read_session(self.ats_root, project_id, self.session_id) or {}
                if isinstance(meta0.get("plan"), list):
                    self.deps.plan[:] = meta0["plan"]
            except Exception:
                pass
        # Engine: "fast" (plan once, deterministic execution, replay-verified tests) or
        # "classic" (the original tool-calling agent). Fast is the default.
        ex_cfg = self.config.get("agent_execution") or {}
        self.engine = str(cmd.get("engine") or ex_cfg.get("engine") or "fast").lower()
        if self.engine not in ("fast", "classic"):
            self.engine = "fast"
        self.fast = None
        self.fast_ctx = {"provider": pname, "base_url": base_url or start_url or None,
                         "storage_state": load_state,
                         "test_data": {k: v for k, v in (("username", creds[0]), ("password", creds[1])) if v}
                                      if creds else {}}
        authed = info.get("authed", "none")
        self.ready_info = {"url": info.get("url", ""), "title": info.get("title", ""),
                           "provider": pname, "model": model_name,
                           "auth": authed != "none", "auth_via": authed,
                           "project": (project or {}).get("name") or project_id or None,
                           "project_id": project_id, "session_id": self.session_id}
        self.ready_info["engine"] = self.engine
        emit({"event": "ready", **self.ready_info, "resumed": resumed, "mode": mode,
              "plan": list(self.deps.plan), "transcript": transcript, "tokens": self.session_tokens})

    async def chat(self, cmd):
        if not self.agent:
            emit({"event": "error", "message": "agent not initialized — send {action:'init'} first"})
            return
        message = (cmd.get("message") or "").strip()
        attachments = cmd.get("attachments") or []
        if not message and not attachments:
            emit({"event": "error", "message": "empty message"})
            return
        if cmd.get("agentMode") in ("auto", "guided"):
            self.deps.mode = cmd["agentMode"]
        if self.deps.provenance:
            # Everything the user says/attaches is legitimate value provenance.
            self.deps.provenance.add_text(message, "user-message")
            for p, _name in _attachment_paths(attachments):
                try:
                    t, _tr = extract_file_text(p)
                    if t:
                        self.deps.provenance.add_text(t, "attachment")
                except Exception:
                    pass
        attachment_names = [name for _p, name in _attachment_paths(attachments)]
        if getattr(self, "engine", "classic") == "fast":
            await self._fast_chat(message, attachments, attachment_names)
            return
        # Inline document text + attach images as multimodal parts. Falls back to a
        # plain string when there are no binary parts (so text-only models are unaffected).
        prompt = _build_user_prompt(
            message or "Use the attached file(s) as context for the app under test.",
            attachments,
            vision=self.deps.vision)
        # Review uses original user/context text, never the compiler's paraphrase.
        # Binary-only requirements need explicit context rather than invented text.
        self.deps.decision_requirement = prompt if isinstance(prompt, str) else None
        hist_before = len(self.history)
        with capture_run_messages() as messages:
            try:
                result = await self.agent.run(
                    prompt, deps=self.deps, message_history=self.history,
                    event_stream_handler=_stream_handler,
                    # request_limit DEFAULTS to 50 — left alone it silently caps every
                    # long turn at 50 model requests no matter what tool_budget says
                    # (benchmark runs 1+2 both hit this, not their 80/200 budgets).
                    # Scale it with the budget; unlimited budget = unlimited requests.
                    usage_limits=UsageLimits(
                        tool_calls_limit=self.tool_budget or None,
                        request_limit=(self.tool_budget + 30) if self.tool_budget else None))
                self.history = _compact_history(result.all_messages())
                self._add_usage(result)
                self._persist_turn(message, attachment_names, result.new_messages())
                emit({"event": "turn_complete", "text": result.output})
            except UsageLimitExceeded:
                # Hit the per-turn action budget. Keep the partial history so "continue"
                # has context, but TRIM the trailing un-executed tool call (else
                # pydantic-ai rejects the next prompt), then summarize findings.
                if messages:
                    self.history = _compact_history(_trim_dangling_tool_calls(messages))
                # The budget stop has no RunResult — count usage from the NEW messages
                # of this turn (provider-reported usage rides on each ModelResponse).
                self._add_usage(msgs=self.history[hist_before:])
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
                if messages:
                    self.history = _compact_history(_trim_dangling_tool_calls(messages))
                new_messages = messages[hist_before:] if messages else []
                self._add_usage(msgs=new_messages, incomplete=True)
                self._persist_turn(message, attachment_names, new_messages,
                                   extra_bubbles=[{"role": "assistant", "text": msg}])
                emit({"event": "error", "message": msg, "terminal": True})
                emit({"event": "turn_complete", "status": "error", "text": msg})
                log(traceback.format_exc())

    async def _fast_chat(self, message, attachments, attachment_names):
        """One chat turn on the fast engine: plan, execute, replay-verify, save."""
        from qm_agent import FastAgent, summary_text
        from qm_map import PageMap
        task = message or "Use the attached file(s) as the test requirements."
        for path, name in _attachment_paths(attachments):
            try:
                text, _truncated = extract_file_text(path)
                if text:
                    task += f"\n\n[Attached: {name}]\n{text[:12000]}"
            except Exception:
                pass
        loop = asyncio.get_running_loop()

        def confirm(intent, element):
            # Blocks the browser thread until the user answers in the chat.
            self.deps._ask_state["waiting"] = True
            emit({"event": "input_required",
                  "question": f"This step looks irreversible: {element}. Run it? Reply yes or no."})
            try:
                answer = asyncio.run_coroutine_threadsafe(self._user_input_q.get(), loop).result(timeout=900)
            except Exception:
                answer = ""
            finally:
                self.deps._ask_state["waiting"] = False
            return str(answer).strip().lower() in ("y", "yes", "ok", "okay", "go", "run it", "confirm", "sure")

        def work():
            if self.fast is None:
                tests_root = (project_store.ensure_tests_scaffold(self.ats_root, self.project_id)
                              if self.project_id else os.path.join(self.ats_root, "tests"))
                page_map = PageMap(project_store.page_map_path(self.ats_root, self.project_id)
                                   if self.project_id else None)
                scan = str((self.config.get("agent_execution") or {}).get("fast_scan") or "auto")
                self.fast = FastAgent(self.session.page, self.session.browser, self.config,
                                      provider_name=self.fast_ctx["provider"], base_url=self.fast_ctx["base_url"],
                                      tests_root=tests_root, storage_state=self.fast_ctx["storage_state"],
                                      test_data=self.fast_ctx["test_data"], emit=emit, confirm=confirm,
                                      log_dir=os.path.join(self.ats_root, "results", "_fast_runs"),
                                      page_map=page_map, scan=scan)
            return self.fast.run_task(task)

        try:
            result = await _bro(work)
            text = summary_text(result)
            usage = getattr(self.fast.planner, "usage", None) if self.fast else None
            if usage:
                for key in ("input", "output"):
                    self.session_tokens[key] = self.session_tokens.get(key, 0) + usage.pop(key, 0)
                self.session_tokens["total"] = self.session_tokens.get("input", 0) + self.session_tokens.get("output", 0)
                emit({"event": "usage", **self.session_tokens})
            emit({"event": "turn_complete", "text": text, "fast_result": {
                k: result.get(k) for k in ("saved", "replay", "steps", "authoring_s", "stop_reason")}})
            self._persist_fast_turn(message, attachment_names, text)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            emit({"event": "error", "message": msg})
            emit({"event": "turn_complete", "status": "error", "text": msg})
            self._persist_fast_turn(message, attachment_names, msg)
            log(traceback.format_exc())

    def _persist_fast_turn(self, message, attachment_names, reply):
        if not self.session_id:
            return
        try:
            agent_sessions.append_bubbles(self.ats_root, self.project_id, self.session_id, [
                agent_sessions.user_bubble(message, attachment_names), {"role": "assistant", "text": reply}])
            meta = agent_sessions.read_session(self.ats_root, self.project_id, self.session_id) or {}
            patch = {"message_count": int(meta.get("message_count") or 0) + 2, "status": "idle",
                     "tokens": dict(self.session_tokens)}
            if message and (not (meta.get("title") or "").strip() or meta.get("title") == "New session"):
                patch["title"] = message.strip()[:80]
            agent_sessions.update_session_meta(self.ats_root, self.project_id, self.session_id, patch)
        except Exception as e:
            log(f"[agent] persist failed: {e}")

    async def show_browser(self, cmd):
        """Switch the testing browser to headed (visible) mode so the user can watch.
        If already headed, reports that. Emits show_browser_result with ok/message/url."""
        if not self.session:
            emit({"event": "show_browser_result", "ok": False, "error": "no active session"})
            return
        result = await _bro(self.session.show_browser)
        emit({"event": "show_browser_result", **result})

    async def reattach(self):
        """Re-emit `ready` (+ current transcript + token total) so a reopened window/dock reconnects
        to this STILL-RUNNING session without restarting its browser or losing memory."""
        if not self.session_id or not self.ready_info:
            emit({"event": "error", "message": "no active session to reattach"})
            return
        transcript = agent_sessions.load_transcript(self.ats_root, self.project_id, self.session_id)
        emit({"event": "ready", **self.ready_info, "resumed": True,
              "transcript": transcript, "tokens": self.session_tokens})

    def _add_usage(self, result=None, msgs=None, incomplete=False):
        """Accumulate this turn's token usage into the session total and emit a live
        counter ({event:'usage', input, output, total, estimated} — cumulative).
        Counts come from each NEW ModelResponse's provider-reported usage, which
        works for BOTH ways a turn can end: normal completion (result) AND the
        tool-budget stop (msgs — there is no result object on that path; the old
        code skipped usage entirely there, which kept the counter at 0 for every
        budget-limited agentic turn). Missing usage is marked incomplete rather
        than treating prompt/history characters as generated output."""
        if msgs is None and result is not None:
            try:
                msgs = result.new_messages()
            except Exception:
                msgs = []
        inp = out = 0
        for m in msgs or []:
            u = getattr(m, "usage", None)
            if u is not None:
                inp += int(getattr(u, "input_tokens", 0) or 0)
                out += int(getattr(u, "output_tokens", 0) or 0)
        estimated = bool(self.session_tokens.get("estimated"))
        if inp + out <= 0 or incomplete:
            # Input/tool history is NOT generated output, particularly after a 401.
            # Keep usage unknown; the separate usage_est stream carries input estimates.
            self.session_tokens["usage_incomplete"] = True
        self.session_tokens["input"] += inp
        self.session_tokens["output"] += out
        self.session_tokens["total"] += inp + out
        self.session_tokens["estimated"] = estimated
        self.session_tokens["usage_source"] = "unavailable_or_partial" if self.session_tokens.get("usage_incomplete") else "provider_reported"
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
            if self.deps is not None:
                patch["plan"] = list(self.deps.plan)   # restore the checklist on resume
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
        note = ("(Paused at my per-turn action limit. I've kept everything I found, so just "
                "say \"continue\" to keep going, or tell me what to focus on.)")
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
            # If ask_user is blocking, route the reply directly to it; otherwise start a chat turn.
            if rt.deps and rt.deps._ask_state.get("waiting"):
                await rt._user_input_q.put(cmd.get("message", ""))
            elif rt._chat_task and not rt._chat_task.done():
                emit({"event": "log", "message": "Still processing — your message will be sent when ready."})
            else:
                rt._chat_task = asyncio.create_task(rt.chat(cmd))
        elif action == "set_mode":
            m = str(cmd.get("mode") or "").lower()
            if rt.deps and m in ("auto", "guided"):
                rt.deps.mode = m
                emit({"event": "mode_changed", "mode": m})
            else:
                emit({"event": "error", "message": f"cannot set mode {m!r} (agent ready? mode valid?)"})
        elif action == "reattach":
            await rt.reattach()
        elif action == "show_browser":
            await rt.show_browser(cmd)
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
