"""
QAmate — Agent Sessions
==========================
Persistent, resumable conversation sessions for the chat agent (engine/agent_chat.py).

A **session** is one conversation thread with the agent, scoped to a project and saved to
disk so it survives process exit / app restart and can be resumed "from a point" — the way
Codex / Claude Code keep sessions. This is what turns the (formerly in-memory-only) chat
into a real agentic workspace.

Layout (under the project, mirroring project_store's context/memory split):
    projects/<id>/agent_sessions/<session-id>/
        session.json     -> metadata (title, timestamps, provider/model, status, counts)
        messages.json    -> pydantic-ai model history (the authoritative memory restored on
                            resume) — written from result.all_messages()
        transcript.jsonl -> append-only UI bubbles for redraw (user/assistant/tool/...)
No-project fallback (legacy mode): <ats_root>/.agent_context/agent_sessions/<sid>/

Ownership split (same pattern as project_store.py + main.js):
  * Python (this module + agent_chat) owns messages.json — pydantic-ai (de)serialization —
    and writes session.json + transcript.jsonl during a live session.
  * Node (main.js) reads/writes session.json + transcript.jsonl for the sidebar (list / new /
    rename / delete / load-transcript) as plain JSON; it never needs the model history.
KEEP the path convention here identical to main.js `_agentSessionsDir` / `_sessionPaths`.

Pure stdlib + one pydantic-ai adapter (ModelMessagesTypeAdapter) for the model history.

CLI (so Node/scripts can drive it without duplicating logic; Node currently does its own
JSON file ops, but this keeps a single source of truth for testing):
    python agent_sessions.py --selftest
    python agent_sessions.py list   <project_id|->
    python agent_sessions.py delete <project_id|-> <session_id>
"""

import os
import re
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart,
    ThinkingPart,
)

SESSIONS_DIRNAME = "agent_sessions"


# ── Paths (mirror main.js _agentSessionsDir) ─────────────────────────────────

def sessions_dir(ats_root, project_id):
    """Folder holding a project's sessions. projects/<id>/agent_sessions/ — or, when there
    is no project (legacy mode), <ats_root>/.agent_context/agent_sessions/."""
    if project_id:
        return os.path.join(project_store.project_dir(ats_root, project_id), SESSIONS_DIRNAME)
    return os.path.join(ats_root, ".agent_context", SESSIONS_DIRNAME)


def session_dir(ats_root, project_id, session_id):
    return os.path.join(sessions_dir(ats_root, project_id), session_id)


def session_file(ats_root, project_id, session_id):
    return os.path.join(session_dir(ats_root, project_id, session_id), "session.json")


def messages_file(ats_root, project_id, session_id):
    return os.path.join(session_dir(ats_root, project_id, session_id), "messages.json")


def transcript_file(ats_root, project_id, session_id):
    return os.path.join(session_dir(ats_root, project_id, session_id), "transcript.jsonl")


# ── Small helpers ────────────────────────────────────────────────────────────

def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:32] or "session"


def _short(obj, n=300):
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=True, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= n else s[:n] + "..."


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def new_session_id(title=""):
    """A sortable, human-readable id: YYYYMMDD-HHMMSS-<slug>."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_slugify(title)}"


# ── Meta CRUD (plain JSON — safe for Node to mirror) ─────────────────────────

def create_session(ats_root, project_id, title="", provider="", model="",
                   env="", headed=False, session_id=None):
    """Create a session folder + session.json and return the meta dict."""
    sid = session_id or new_session_id(title)
    now = _now_iso()
    meta = {
        "id": sid,
        "project_id": project_id,
        "title": (title or "New session")[:120],
        "created_at": now,
        "updated_at": now,
        "provider": provider or "",
        "model": model or "",
        "env": env or "",
        "headed": bool(headed),
        "status": "idle",        # idle | running | error
        "message_count": 0,
    }
    _write_json(session_file(ats_root, project_id, sid), meta)
    return meta


def read_session(ats_root, project_id, session_id):
    return _read_json(session_file(ats_root, project_id, session_id))


def list_sessions(ats_root, project_id):
    """All sessions for a project, newest-updated first. Missing dir -> []."""
    d = sessions_dir(ats_root, project_id)
    out = []
    try:
        names = os.listdir(d)
    except Exception:
        return out
    for name in names:
        meta = _read_json(session_file(ats_root, project_id, name))
        if isinstance(meta, dict) and meta.get("id"):
            out.append(meta)
    out.sort(key=lambda m: m.get("updated_at") or m.get("created_at") or "", reverse=True)
    return out


def update_session_meta(ats_root, project_id, session_id, patch):
    """Shallow-merge `patch` into session.json, always bumping updated_at."""
    meta = read_session(ats_root, project_id, session_id)
    if meta is None:
        # Session.json vanished (deleted mid-run) — recreate a minimal record so the turn
        # is not lost; callers pass enough in patch to be useful.
        meta = {"id": session_id, "project_id": project_id, "created_at": _now_iso()}
    meta.update(patch or {})
    meta["updated_at"] = _now_iso()
    _write_json(session_file(ats_root, project_id, session_id), meta)
    return meta


def rename_session(ats_root, project_id, session_id, title):
    return update_session_meta(ats_root, project_id, session_id, {"title": (title or "").strip()[:120]})


def delete_session(ats_root, project_id, session_id):
    import shutil
    d = session_dir(ats_root, project_id, session_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return True


def clear_session(ats_root, project_id, session_id):
    """Empty a session in place (Reset): wipe model history + transcript, keep the record."""
    save_messages(ats_root, project_id, session_id, [])
    p = transcript_file(ats_root, project_id, session_id)
    try:
        if os.path.exists(p):
            open(p, "w").close()
    except Exception:
        pass
    update_session_meta(ats_root, project_id, session_id, {"message_count": 0})
    return True


# ── Model history (pydantic-ai (de)serialization) ────────────────────────────

def load_messages(ats_root, project_id, session_id):
    """Restore the model's conversation history (list[ModelMessage]) for resume. Empty
    list when there is no saved history yet."""
    p = messages_file(ats_root, project_id, session_id)
    try:
        with open(p, "rb") as f:
            data = f.read()
    except Exception:
        return []
    if not data.strip():
        return []
    try:
        return list(ModelMessagesTypeAdapter.validate_json(data))
    except Exception:
        return []


def save_messages(ats_root, project_id, session_id, messages):
    """Persist the model history (list[ModelMessage], e.g. result.all_messages())."""
    p = messages_file(ats_root, project_id, session_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = ModelMessagesTypeAdapter.dump_json(list(messages or []))
    with open(p, "wb") as f:
        f.write(data)
    return len(messages or [])


# ── Transcript (UI bubbles for redraw) ───────────────────────────────────────

def append_bubbles(ats_root, project_id, session_id, bubbles):
    """Append UI bubbles to transcript.jsonl (ASCII-safe — the Electron console is cp1252)."""
    if not bubbles:
        return
    p = transcript_file(ats_root, project_id, session_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for b in bubbles:
            f.write(json.dumps(b, ensure_ascii=True) + "\n")


def load_transcript(ats_root, project_id, session_id):
    """Read transcript.jsonl back into a list of bubbles for redraw on resume."""
    p = transcript_file(ats_root, project_id, session_id)
    out = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return out
    return out


def _user_text(content):
    """Extract a plain string from a UserPromptPart content (str, or a multimodal list
    whose first item is the text body)."""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        for item in content:
            if isinstance(item, str):
                return item
    return ""


def bubbles_from_messages(messages, include_user=True, attachment_names=None, ts=None):
    """Turn pydantic-ai messages into renderer bubbles (roles: user/assistant/tool/
    tool_result), in chronological order. agent_chat records its own clean user bubble
    (original text, not the inlined-doc prompt), so it calls with include_user=False."""
    ts = ts or _now_iso()
    bubbles = []
    for m in messages or []:
        for part in getattr(m, "parts", []):
            if isinstance(part, UserPromptPart):
                if not include_user:
                    continue
                b = {"role": "user", "text": _user_text(part.content), "ts": ts}
                if attachment_names:
                    b["attachments"] = list(attachment_names)
                    attachment_names = None  # only the first user bubble carries them
                bubbles.append(b)
            elif isinstance(part, ThinkingPart):
                # Reasoning models (MiMo) — keep a truncated trace so reloaded
                # sessions still show the collapsible Thinking blocks.
                if (part.content or "").strip():
                    bubbles.append({"role": "thinking", "text": part.content[:4000], "ts": ts})
            elif isinstance(part, TextPart):
                if (part.content or "").strip():
                    bubbles.append({"role": "assistant", "text": part.content, "ts": ts})
            elif isinstance(part, ToolCallPart):
                args = part.args
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                bubbles.append({"role": "tool", "tool": part.tool_name, "args": args, "ts": ts})
            elif isinstance(part, ToolReturnPart):
                # 1500 keeps most results parseable JSON for the UI's structured cards.
                bubbles.append({"role": "tool_result", "tool": part.tool_name,
                                "text": _short(part.content, 1500), "ts": ts})
    return bubbles


def user_bubble(text, attachment_names=None):
    """A clean user bubble from the original message + attachment display names."""
    b = {"role": "user", "text": text or "", "ts": _now_iso()}
    if attachment_names:
        b["attachments"] = list(attachment_names)
    return b


# ── CLI / selftest ───────────────────────────────────────────────────────────

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=True, default=str))


def _selftest():
    """Offline round-trip: create -> save messages -> append transcript -> reload -> delete."""
    import tempfile
    from pydantic_ai.messages import ModelRequest, ModelResponse
    root = tempfile.mkdtemp(prefix="ats_sess_selftest_")
    pid = None  # exercise the no-project fallback path

    meta = create_session(root, pid, title="Smoke test", provider="mimo", model="x")
    sid = meta["id"]
    assert read_session(root, pid, sid)["title"] == "Smoke test"

    history = [
        ModelRequest(parts=[UserPromptPart(content="drive the orders flow")]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="navigate", args={"url": "https://x/orders"}, tool_call_id="c1"),
            TextPart(content="Opened the orders page."),
        ]),
        ModelRequest(parts=[ToolReturnPart(tool_name="navigate", content={"ok": True}, tool_call_id="c1")]),
    ]
    n = save_messages(root, pid, sid, history)
    back = load_messages(root, pid, sid)
    assert len(back) == n == 3, (len(back), n)

    bubbles = [user_bubble("drive the orders flow", ["spec.pdf"])] + \
        bubbles_from_messages(history, include_user=False)
    append_bubbles(root, pid, sid, bubbles)
    tr = load_transcript(root, pid, sid)
    roles = [b["role"] for b in tr]
    assert roles == ["user", "tool", "assistant", "tool_result"], roles
    assert tr[0].get("attachments") == ["spec.pdf"]

    update_session_meta(root, pid, sid, {"message_count": n, "status": "idle"})
    assert len(list_sessions(root, pid)) == 1

    delete_session(root, pid, sid)
    assert list_sessions(root, pid) == []

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    print("SELFTEST OK - sessions create/save/reload/transcript/delete round-trip clean")


def main(argv):
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--selftest" in argv:
        _selftest()
        return 0
    if len(argv) < 2:
        _emit({"status": "error", "message": "usage: agent_sessions.py <--selftest|list|delete> ..."})
        return 1
    cmd = argv[1]
    pid = None if (len(argv) > 2 and argv[2] == "-") else (argv[2] if len(argv) > 2 else None)
    if cmd == "list":
        _emit({"status": "success", "sessions": list_sessions(ats_root, pid)})
    elif cmd == "delete" and len(argv) > 3:
        delete_session(ats_root, pid, argv[3])
        _emit({"status": "success"})
    else:
        _emit({"status": "error", "message": f"unknown/incomplete command: {cmd}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
