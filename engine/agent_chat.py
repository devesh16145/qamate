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
import sys
import json
import asyncio
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_store
from app_explorer import _comprehensive_snapshot, element_to_model, _label
from dom_inspector import DESTRUCTIVE_KEYWORDS
from smart_locator import smart_locator, SelfHealError
from recorder_parser import generate_from_review
from agent_recorder import _locator_str, _q  # reuse the proven codegen helpers

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded


# ── stdout: JSON lines, ASCII-only (the Electron runner pipes through a cp1252
# console — non-ASCII crashes it; ensure_ascii=True escapes everything safely) ─
_OUT_LOCK = threading.Lock()


def emit(obj):
    with _OUT_LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def log(msg):
    emit({"event": "log", "message": str(msg)})


# ── The single thread that owns ALL Playwright objects ───────────────────────
_BROWSER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw")


async def _bro(fn, *args, **kwargs):
    """Run a (sync) Playwright operation on the pinned browser thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_BROWSER, lambda: fn(*args, **kwargs))


# ── Live browser session (every method runs ON the browser thread) ───────────

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

    # -- lifecycle -----------------------------------------------------------
    def start(self, start_url="", storage_state=None, headless=True, credentials=None, save_state_path=None):
        """Launch the browser, reuse a saved login if present, and — if we still
        land on a login form — log in ONCE from configured credentials and persist
        a fresh storage_state for next time (so the agent never has to log in by
        hand). Returns {url, title, authed} where authed is
        'storage_state' | 'login' | 'session' | 'none'."""
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
        before = len(self.issues)
        try:
            self._goto(url)
        except Exception as e:
            return {"ok": False, "error": f"navigation failed: {str(e)[:160]}", "url": self._url()}
        self._record_navigate(url)
        return {"ok": True, "url": self._url(), "title": self._title(),
                "new_issues": self.issues[before:]}

    def inspect(self, limit=40, include_hidden=False):
        raw = _comprehensive_snapshot(self.page)
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

    def act(self, kind, ref, value=None):
        el = self.by_ref.get(ref)
        if not el:
            return {"ok": False, "error": f"ref '{ref}' is not on the current page; call inspect_page first"}
        loc_str = _locator_str(el["primary"])
        try:
            live = smart_locator(self.page, el["primary"], fallbacks=el.get("fallbacks"),
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
        return {"ok": True, "url": self._url(), "title": self._title(),
                "new_issues": self.issues[before:]}

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
        return res


# ── Agent dependencies ───────────────────────────────────────────────────────

@dataclass
class Deps:
    session: BrowserSession
    ats_root: str
    project: Optional[dict]
    config: dict = field(default_factory=dict)


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
    "- When you have done what the user asked, call create_test_case EXACTLY ONCE and then STOP. "
    "It becomes a normal, runnable, self-healing test case. Use a clear tc_id (TC-<AREA>-NNN) and flow_id.\n\n"
    "Stay on task:\n"
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


def build_agent(model):
    agent = Agent(
        model,
        deps_type=Deps,
        system_prompt=_SYSTEM,
        retries=2,
        tool_timeout=150,
        model_settings={"temperature": 0.2, "max_tokens": 4096},
    )

    @agent.tool
    async def navigate(ctx: RunContext[Deps], url: str) -> dict:
        """Navigate the browser to a URL (absolute http(s) URL). Returns the resulting URL,
        page title, and any new breakage issues triggered by the load."""
        return await _bro(ctx.deps.session.navigate, url)

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
        self.deps = None
        self.history = []        # list[ModelMessage] — conversation memory
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

        project_id = cmd.get("project_id") or project_store.get_active_project_id(self.ats_root)
        project = project_store.get_project(self.ats_root, project_id) if project_id else None
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

        self.agent = build_agent(model)
        self.deps = Deps(session=self.session, ats_root=self.ats_root, project=project, config=self.config)
        self.history = []
        authed = info.get("authed", "none")
        emit({"event": "ready", "url": info.get("url", ""), "title": info.get("title", ""),
              "provider": pname, "model": model_name,
              "auth": authed != "none", "auth_via": authed,
              "project": (project or {}).get("name") or project_id or None})

    async def chat(self, cmd):
        if not self.agent:
            emit({"event": "error", "message": "agent not initialized — send {action:'init'} first"})
            return
        message = (cmd.get("message") or "").strip()
        if not message:
            emit({"event": "error", "message": "empty message"})
            return
        try:
            result = await self.agent.run(
                message, deps=self.deps, message_history=self.history,
                event_stream_handler=_stream_handler,
                usage_limits=UsageLimits(tool_calls_limit=20))
            self.history = result.all_messages()
            emit({"event": "turn_complete", "text": result.output})
        except UsageLimitExceeded:
            # Bound a runaway turn so the user regains control. History is left at the
            # previous turn; the live browser stays wherever it ended up.
            emit({"event": "turn_complete",
                  "text": ("(Stopped — I hit the per-turn budget of ~20 actions without finishing, "
                           "which usually means I got stuck. Tell me the next concrete step or refine "
                           "the goal, and I'll continue from the current page.)")})
        except Exception as e:
            emit({"event": "error", "message": f"{type(e).__name__}: {e}"})
            log(traceback.format_exc())

    def reset(self):
        self.history = []
        if self.session:
            self.session.steps = []
            self.session.assertions = []
            self.session.step_id = 0
            self.session.input_counter = 0
        emit({"event": "reset_ok"})

    async def shutdown(self):
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
    from pydantic_ai.models.test import TestModel
    agent = build_agent(TestModel())
    sess = BrowserSession()
    deps = Deps(session=sess, ats_root=".", project=None, config={})
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
