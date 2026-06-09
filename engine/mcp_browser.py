"""
Playwright MCP Browser Backend — experimental parallel implementation.

Replaces the direct Python Playwright layer (BrowserSession) with the official
Playwright MCP server (microsoft/playwright-mcp), while keeping ALL agent
intelligence unchanged (playbook, auth, vision, input registry, etc.).

Switch:  config.json  "browser_backend": "mcp"   (default: "native")
Revert:  config.json  "browser_backend": "native"

MCPBrowserSession exposes the SAME public interface as BrowserSession so every
agent tool in agent_chat.py works without modification.

Limitations in MCP mode (by design — testing only):
  - Test recording / create_test → disabled (returns clear error)
  - Self-healing locators → not applicable (MCP uses aria refs, not fingerprints)
  - Input registry auto-record → disabled
  - scan_page_errors → DOM errors via browser_evaluate (best-effort)
  - Viewport: controlled by MCP server launch flags, not browser_config

Requirements:
  - Node.js in PATH
  - npx available (ships with Node.js 5.2+)
  - @playwright/mcp will be installed on first run via npx --yes
"""

import json
import os
import re
import sys
import time
import base64
import threading
import subprocess
import uuid


# ── Thin synchronous JSON-RPC 2.0 client for the Playwright MCP stdio server ─

class PlaywrightMCPClient:
    """Spawns `npx @playwright/mcp@latest` and communicates via MCP stdio."""

    def __init__(self):
        self._proc = None
        self._write_lock = threading.Lock()
        self._resp_lock = threading.Lock()
        self._responses = {}   # str(id) -> {"event": Event, "data": dict|None}
        self._reader_thread = None
        self.ready = False

    def start(self, headless=True, storage_state_path=None):
        # On Windows, subprocess cannot resolve .ps1 scripts — use npx.cmd explicitly.
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        cmd = [npx, "--yes", "@playwright/mcp@latest"]
        if headless:
            cmd.append("--headless")
        if storage_state_path:
            cmd += ["--storage-state", storage_state_path]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "npx.cmd not found — Node.js required for MCP backend"}

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # MCP handshake
        resp = self._call("__init__", "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agrim-ats", "version": "1.0"},
        }, timeout=20)
        if resp is None or "error" in resp:
            return {"ok": False, "error": f"MCP init failed: {resp}"}

        self._notify("notifications/initialized", {})
        self.ready = True
        server_info = (resp.get("result") or {}).get("serverInfo", {})
        return {"ok": True, "server": server_info}

    def call_tool(self, name, arguments, timeout=30):
        if not self.ready:
            return {"ok": False, "error": "MCP server not ready"}
        req_id = str(uuid.uuid4())
        resp = self._call(req_id, "tools/call", {"name": name, "arguments": arguments}, timeout)
        if resp is None:
            return {"ok": False, "error": f"timeout ({timeout}s) calling {name}"}
        if "error" in resp:
            return {"ok": False, "error": (resp["error"] or {}).get("message", "MCP error")}
        content = (resp.get("result") or {}).get("content") or []
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
        images = [c for c in content if c.get("type") == "image"]
        return {"ok": True, "text": text, "images": images}

    def close(self):
        self.ready = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _call(self, req_id, method, params, timeout=30):
        event = threading.Event()
        with self._resp_lock:
            self._responses[str(req_id)] = {"event": event, "data": None}
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
                         ensure_ascii=True)
        with self._write_lock:
            try:
                self._proc.stdin.write(msg + "\n")
                self._proc.stdin.flush()
            except Exception:
                return None
        event.wait(timeout=timeout)
        with self._resp_lock:
            entry = self._responses.pop(str(req_id), {})
        return entry.get("data")

    def _notify(self, method, params):
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                         ensure_ascii=True)
        with self._write_lock:
            try:
                self._proc.stdin.write(msg + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass

    def _reader_loop(self):
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if "id" in msg:
                    key = str(msg["id"])
                    with self._resp_lock:
                        if key in self._responses:
                            self._responses[key]["data"] = msg
                            self._responses[key]["event"].set()
            except Exception:
                pass


# ── MCPBrowserSession — drop-in replacement for BrowserSession ────────────────

_DIRECT_SELECTOR_PREFIXES = (
    "input[", "button[", "select[", "textarea[",
    "[role=", "[type=", "[aria-", "[placeholder",
    "[name=", "[id=", "//", "text=", "role=",
)


def _is_direct_selector(ref):
    if not ref:
        return False
    return (
        any(ref.startswith(p) for p in _DIRECT_SELECTOR_PREFIXES) or
        (ref.startswith("[") and "=" in ref and "]" in ref)
    )


class MCPBrowserSession:
    """
    BrowserSession-compatible session backed by the Playwright MCP server.
    Core browser operations route through the MCP server; everything else
    (recording, auth, checkpoints) is either delegated or stubbed.
    """

    def __init__(self):
        self.mcp = PlaywrightMCPClient()

        # State attributes that agent tools reference directly
        self.issues = []
        self._auth_failures = []
        self._api_errors = []
        self.by_ref = {}
        self.steps = []
        self.assertions = []
        self.step_id = 0
        self.input_counter = 0
        self._start_args = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, start_url="", storage_state=None, headless=True,
              credentials=None, save_state_path=None,
              record_first_step=True, extra_init_states=None,
              browser_config=None):
        self._start_args = dict(
            start_url=start_url, storage_state=storage_state,
            headless=headless, credentials=credentials,
            save_state_path=save_state_path,
        )
        r = self.mcp.start(headless=headless)
        if not r["ok"]:
            return {"url": "", "title": "", "authed": "none",
                    "mcp_error": r.get("error")}

        authed = "none"

        # Navigate to origin first so localStorage is scoped to the right domain,
        # then inject tokens, then navigate to the target URL.
        origin_url = start_url or ""
        if origin_url:
            self.mcp.call_tool("browser_navigate", {"url": origin_url})
            time.sleep(1)  # let SPA boot before injecting

        if storage_state and os.path.exists(storage_state):
            if self._inject_storage_state(storage_state):
                authed = "storage_state"
                # Re-navigate so the SPA re-reads the injected tokens
                if origin_url:
                    self.mcp.call_tool("browser_navigate", {"url": origin_url})
                    time.sleep(2)  # SPA needs ~2s to process tokens and redirect

        return {"url": self._url(), "title": self._title(), "authed": authed,
                "note": "MCP backend active — recording/self-healing disabled"}

    def close(self):
        self.mcp.close()

    def restart(self):
        self.close()
        info = self.start(**self._start_args)
        return {"ok": True, **info}

    def show_browser(self):
        return {"ok": False,
                "message": "show_browser not available in MCP backend mode."}

    def _alive(self):
        return self.mcp._proc is not None and self.mcp._proc.poll() is None

    def _ensure_alive(self):
        return self._alive()

    # ── core browser operations ───────────────────────────────────────────────

    def navigate(self, url):
        self._auth_failures = []
        self._api_errors = []
        r = self.mcp.call_tool("browser_navigate", {"url": url})
        result = {"ok": r["ok"], "url": self._url(), "title": self._title(),
                  "new_issues": []}
        if not r["ok"]:
            result["error"] = r.get("error")
        return result

    def inspect(self, limit=40, include_hidden=False):
        r = self.mcp.call_tool("browser_snapshot", {})
        if not r["ok"]:
            return {"ok": False, "error": r.get("error", "snapshot failed")}
        elements = self._parse_snapshot(r["text"], limit)
        self.by_ref = {e["ref"]: e for e in elements}
        return {"ok": True, "url": self._url(), "title": self._title(),
                "element_count": len(elements), "hidden_count": 0,
                "elements": elements, "issues": []}

    def aria_snapshot_page(self, selector="body"):
        r = self.mcp.call_tool("browser_snapshot", {})
        if r["ok"]:
            return {"ok": True, "selector": selector, "tree": r["text"]}
        return {"ok": False, "error": r.get("error")}

    def screenshot(self):
        r = self.mcp.call_tool("browser_screenshot", {})
        if r.get("ok") and r.get("images"):
            try:
                return base64.b64decode(r["images"][0].get("data", ""))
            except Exception:
                pass
        return b""

    def act(self, kind, ref, value=None):
        if _is_direct_selector(ref):
            return self._act_direct(kind, ref, value)

        el = self.by_ref.get(ref) or {"name": ref}
        name = el.get("name") or ref
        self.step_id += 1

        try:
            if kind == "fill":
                val = str(value or "")
                r = self.mcp.call_tool("browser_type",
                                       {"element": name, "ref": ref, "text": val})
            elif kind == "select":
                val = str(value or "")
                r = self.mcp.call_tool("browser_select_option",
                                       {"element": name, "ref": ref, "values": [val]})
            else:
                r = self._smart_mcp_click(name, ref, el.get("input_type", ""))
        except Exception as e:
            self.step_id -= 1
            return {"ok": False, "error": str(e)[:160]}

        if not r.get("ok"):
            self.step_id -= 1
            return {"ok": False, "error": r.get("error", "MCP action failed")}
        return {"ok": True, "url": self._url(), "title": self._title(),
                "new_issues": []}

    def _act_direct(self, kind, ref, value=None):
        """CSS/XPath selector — use browser_evaluate since MCP has no selector API."""
        self.step_id += 1
        try:
            if kind == "fill":
                val = json.dumps(str(value or ""))
                fn = (f"() => {{ const el = document.querySelector({json.dumps(ref)});"
                      f" if (!el) return false;"
                      f" el.value = {val};"
                      f" el.dispatchEvent(new Event('input', {{bubbles:true}}));"
                      f" el.dispatchEvent(new Event('change', {{bubbles:true}}));"
                      f" return true; }}")
            elif kind == "click":
                fn = (f"() => {{ const el = document.querySelector({json.dumps(ref)});"
                      f" if (!el) return false; el.click(); return true; }}")
            else:
                self.step_id -= 1
                return {"ok": False, "error": "select_option with direct CSS not supported in MCP mode"}
            r = self.mcp.call_tool("browser_evaluate", {"function": fn})
        except Exception as e:
            self.step_id -= 1
            return {"ok": False, "error": str(e)[:160]}
        if not r.get("ok"):
            self.step_id -= 1
            return {"ok": False, "error": r.get("error", "evaluate failed")}
        return {"ok": True, "url": self._url(), "title": self._title(),
                "new_issues": []}

    def _smart_mcp_click(self, name, ref, input_type=""):
        """Use browser_check for radio/checkbox, browser_click for everything else."""
        itype = (input_type or "").lower()
        if itype in ("radio", "checkbox"):
            r = self.mcp.call_tool("browser_check", {"element": name, "ref": ref})
            if r.get("ok"):
                return r
        return self.mcp.call_tool("browser_click", {"element": name, "ref": ref})

    def mouse_click_coords(self, x, y):
        fn = (f"() => {{ const el = document.elementFromPoint({x}, {y});"
              f" if (!el) return null;"
              f" const t = el.closest('button,[role=\"button\"],a,input,select') || el;"
              f" t.click(); return t.tagName; }}")
        r = self.mcp.call_tool("browser_evaluate", {"function": fn})
        return {"ok": r.get("ok", False), "x": x, "y": y,
                "dom_snapped": True, "url": self._url()}

    def press_key(self, key, times=1):
        for _ in range(max(1, times)):
            self.mcp.call_tool("browser_press_key", {"key": key})
        return {"ok": True, "key": key, "times": times, "url": self._url()}

    def wait_for_text_on_page(self, text, timeout_ms=5000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            snap = self.mcp.call_tool("browser_snapshot", {})
            if snap.get("ok") and text in snap.get("text", ""):
                return {"ok": True, "found": text}
            time.sleep(0.4)
        return {"ok": False, "error": f"'{text}' did not appear within {timeout_ms}ms"}

    def get_options(self, filter_text=""):
        snap = self.mcp.call_tool("browser_snapshot", {})
        if not snap.get("ok"):
            return {"ok": True, "count": 0, "options": [], "source": "mcp"}
        opts = re.findall(r'-\s+option\s+"([^"]+)"', snap["text"])
        if filter_text:
            opts = [o for o in opts if filter_text.lower() in o.lower()]
        return {"ok": True, "count": len(opts), "options": opts[:30], "source": "mcp-aria"}

    def click_option_by_text(self, text, exact=False):
        snap = self.mcp.call_tool("browser_snapshot", {})
        if snap.get("ok"):
            m = re.search(r'-\s+option\s+"' + re.escape(text) + r'"\s+\[ref=(e\d+)\]',
                          snap["text"])
            if m:
                r = self.mcp.call_tool("browser_click",
                                       {"element": text, "ref": m.group(1)})
                return {"ok": r.get("ok", False), "clicked": text}
        return {"ok": False, "error": f"Option '{text}' not found in MCP snapshot"}

    def click_by_text_direct(self, text):
        fn = (f"() => {{ const all = Array.from(document.querySelectorAll('*'));"
              f" const m = all.filter(e => e.textContent.trim().includes({json.dumps(text)})"
              f"   && e.offsetHeight > 0 && e.offsetHeight < 100);"
              f" m.sort((a,b)=>Math.abs(a.textContent.length-{len(text)})"
              f"            -Math.abs(b.textContent.length-{len(text)}));"
              f" if (m[0]) {{ m[0].click(); return m[0].textContent.trim(); }}"
              f" return null; }}")
        r = self.mcp.call_tool("browser_evaluate", {"function": fn})
        return {"ok": r.get("ok") and bool(r.get("text")),
                "clicked": r.get("text") or text}

    def force_click_hidden(self, selector, nth=0):
        fn = (f"() => {{ const el = document.querySelectorAll({json.dumps(selector)})[{nth}];"
              f" if (!el) return false; el.click(); return true; }}")
        r = self.mcp.call_tool("browser_evaluate", {"function": fn})
        return {"ok": r.get("ok", False), "selector": selector, "nth": nth,
                "url": self._url()}

    def scan_page_errors_dom(self):
        fn = ("() => { const e=[]; "
              "document.querySelectorAll('[aria-invalid=\"true\"]').forEach(el=>{"
              "  const d=el.getAttribute('aria-describedby');"
              "  const m=d?(document.getElementById(d)||{}).textContent:'';"
              "  e.push({type:'field_error',text:(el.getAttribute('name')||'field')+': '+(m||'invalid')});"
              "});"
              "document.querySelectorAll('[role=\"alert\"],.MuiFormHelperText-root.Mui-error').forEach(el=>{"
              "  if(el.offsetParent)e.push({type:'alert',text:el.textContent.trim()});"
              "});"
              "return e;}")
        r = self.mcp.call_tool("browser_evaluate", {"function": fn})
        errors = []
        if r.get("ok") and r.get("text"):
            try:
                errors = json.loads(r["text"]) or []
            except Exception:
                pass
        return {"ok": True, "count": len(errors), "errors": errors}

    # ── recording stubs (disabled in MCP mode) ────────────────────────────────

    def add_checkpoint(self, name, assert_type, value):
        self.assertions.append({"type": assert_type, "value": value,
                                 "description": name})
        return {"ok": True, "checkpoint_count": len(self.assertions)}

    def recorded_flow(self):
        return {"steps": [], "checkpoints": [a["description"] for a in self.assertions],
                "issues_found": 0}

    def create_test(self, *args, **kwargs):
        return {"status": "error",
                "message": "Test recording is disabled in MCP backend mode. "
                            "Switch back to native backend (config.json browser_backend: native) "
                            "to record and create tests."}

    def overlay_opened(self):
        return False

    def viewport_size(self):
        return {"width": 1280, "height": 720}

    # ── helpers ───────────────────────────────────────────────────────────────

    def _inject_storage_state(self, storage_state_path):
        """Inject localStorage items from a Playwright storage_state JSON file.
        The admin panel uses only localStorage for auth (JWT token + user object).
        Returns True if at least one item was injected successfully."""
        try:
            with open(storage_state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return False
        injected = 0
        for origin_data in state.get("origins", []):
            ls_items = origin_data.get("localStorage") or []
            if not ls_items:
                continue
            items_json = json.dumps(ls_items, ensure_ascii=True)
            fn = (f"() => {{ const items = {items_json};"
                  f" items.forEach(x => localStorage.setItem(x.name, x.value));"
                  f" return items.length; }}")
            r = self.mcp.call_tool("browser_evaluate", {"function": fn})
            if r.get("ok"):
                injected += len(ls_items)
        return injected > 0

    @staticmethod
    def _parse_mcp_eval(text):
        """Extract the actual value from MCP browser_evaluate response text.
        MCP wraps results in '### Result\\n<value>\\n### Ran Playwright code' markup."""
        for line in (text or "").splitlines():
            stripped = line.strip().strip('"').strip("'")
            if stripped.startswith("http") or (stripped and not stripped.startswith("#")):
                return stripped
        return text or ""

    def _url(self):
        r = self.mcp.call_tool("browser_evaluate",
                               {"function": "() => window.location.href"}, timeout=5)
        if not r.get("ok"):
            return ""
        return self._parse_mcp_eval(r.get("text", ""))

    def _title(self):
        r = self.mcp.call_tool("browser_evaluate",
                               {"function": "() => document.title"}, timeout=5)
        if not r.get("ok"):
            return ""
        return self._parse_mcp_eval(r.get("text", ""))

    def _parse_snapshot(self, text, limit=40):
        """Parse Playwright MCP aria snapshot text into our element format.
        Snapshot lines look like:  - button "Submit" [ref=e1]
                                or - textbox "Email" [ref=e2] [disabled]
        """
        elements = []
        for line in (text or "").splitlines():
            m_ref = re.search(r'\[ref=(e\d+)\]', line)
            if not m_ref:
                continue
            ref = m_ref.group(1)
            m_role = re.match(r'\s*-\s+(\w[\w-]*)', line)
            role = m_role.group(1) if m_role else "element"
            m_name = re.search(r'"([^"]{1,80})"', line)
            name = m_name.group(1) if m_name else ""
            itype = ""
            if role in ("textbox", "searchbox"):
                itype = "text"
            elif role == "checkbox":
                itype = "checkbox"
            elif role == "radio":
                itype = "radio"
            elements.append({"ref": ref, "role": role, "name": name, "type": itype,
                              "input_type": itype})
            if len(elements) >= limit:
                break
        return elements
