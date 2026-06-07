"""
Agrim ATS — Pluggable LLM layer
===============================

Provider-agnostic LLM access for the PRD Extractor (L2) and Test Synthesizer
(L3). Customers bring their own key (per the product decision); providers talk
plain HTTP via `urllib` so there is **no SDK dependency** to version-pin or ship.

Providers:
  - anthropic  (default)  Claude — POST /v1/messages, prompt caching on the system block
  - openai                 POST /v1/chat/completions
  - ollama                 local models — POST {base_url}/api/chat
  - mock                   deterministic, no network/key (tests + dry-run)

Keys are read from environment variables named by each provider's `api_key_env`
(injected by the Electron main process from OS-keychain `safeStorage` at spawn,
never written to disk/repo). Config (config.json "llm") only holds models +
the env-var names, never the secret itself.

Public API:
  make_provider(config, name=None) -> Provider
  provider.complete(system, user, max_tokens=?, temperature=?) -> str
  complete_json(provider, system, user, ...) -> parsed JSON (robust to fences/prose)
"""

import os
import re
import json
import base64
import urllib.request
import urllib.error


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    """Raised when a provider is selected but its API key isn't set. Callers
    should catch this and fall back to the deterministic path (or prompt the
    user to configure a key)."""


DEFAULT_LLM_CONFIG = {
    "default_provider": "mimo",
    "providers": {
        # Xiaomi MiMo — OpenAI-compatible (api.xiaomimimo.com/v1, Bearer auth).
        # Current default for explorer/synthesis testing.
        "mimo": {"model": "mimo-v2.5-pro", "base_url": "https://token-plan-sgp.xiaomimimo.com/v1", "api_key_env": "MIMO_API_KEY", "max_tokens": 4096},
        "anthropic": {"model": "claude-opus-4-8", "api_key_env": "ATS_ANTHROPIC_KEY", "max_tokens": 4096},
        "openai": {"model": "gpt-4o", "api_key_env": "ATS_OPENAI_KEY", "max_tokens": 4096},
        "ollama": {"model": "llama3.1", "base_url": "http://localhost:11434", "max_tokens": 4096},
        "mock": {},
    },
}


def _http_post_json(url, headers, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:600]
        raise LLMError(f"HTTP {e.code} from {url}: {body}")
    except urllib.error.URLError as e:
        raise LLMError(f"could not reach {url}: {e.reason}")
    except Exception as e:
        raise LLMError(f"request to {url} failed: {e}")


# ── Providers ────────────────────────────────────────────────────────────────

class BaseProvider:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg or {}

    def _key(self):
        env = self.cfg.get("api_key_env", "")
        key = os.environ.get(env, "") if env else ""
        if not key:
            raise LLMNotConfigured(
                f"{self.name} API key not set (expected env {env!r}); "
                f"configure it in Settings or switch provider."
            )
        return key

    def complete(self, system, user, max_tokens=None, temperature=0.2):
        raise NotImplementedError


class MockProvider(BaseProvider):
    """Deterministic. Returns ATS_LLM_MOCK_RESPONSE if set, else a stub. Lets the
    LLM code path run in tests/dry-run with no key or network."""
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        return os.environ.get("ATS_LLM_MOCK_RESPONSE", '{"mock": true}')


class AnthropicProvider(BaseProvider):
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        payload = {
            "model": self.cfg.get("model", "claude-opus-4-8"),
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 4096),
            "temperature": temperature,
            # System sent as a cacheable block (prompt caching) — big PRD/app-model
            # system prompts are reused across requirements, so this is a real win.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self._key(), "anthropic-version": "2023-06-01"}
        resp = _http_post_json("https://api.anthropic.com/v1/messages", headers, payload)
        return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


class OpenAIProvider(BaseProvider):
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        payload = {
            "model": self.cfg.get("model", "gpt-4o"),
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 4096),
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        base = self.cfg.get("base_url", "https://api.openai.com/v1")
        headers = {"Authorization": f"Bearer {self._key()}"}
        resp = _http_post_json(f"{base}/chat/completions", headers, payload)
        return resp["choices"][0]["message"]["content"]


class OllamaProvider(BaseProvider):
    """Local models — no key required."""
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        base = self.cfg.get("base_url", "http://localhost:11434")
        payload = {
            "model": self.cfg.get("model", "llama3.1"),
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        resp = _http_post_json(f"{base}/api/chat", {}, payload)
        return (resp.get("message") or {}).get("content", "")


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "mimo": OpenAIProvider,   # Xiaomi MiMo speaks the OpenAI chat-completions API
    "ollama": OllamaProvider,
    "mock": MockProvider,
}


def make_provider(config, name=None):
    """Build a provider from config['llm']. `name` overrides the default."""
    llm_cfg = (config or {}).get("llm") or DEFAULT_LLM_CONFIG
    name = name or os.environ.get("ATS_LLM_PROVIDER") or llm_cfg.get("default_provider", "anthropic")
    providers = llm_cfg.get("providers", {})
    cfg = providers.get(name, {})
    cls = _PROVIDERS.get(name)
    if not cls:
        raise LLMError(f"unknown LLM provider '{name}' (have: {', '.join(_PROVIDERS)})")
    return cls(name, cfg)


# ── JSON helpers ─────────────────────────────────────────────────────────────

def extract_json(text):
    """Best-effort parse of JSON from an LLM response (handles ```json fences,
    leading prose, and trailing commentary)."""
    if not text:
        raise LLMError("empty LLM response")
    t = text.strip()
    # Strip code fences
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # Find the first balanced {...} or [...]
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(t)):
            if t[i] == open_ch:
                depth += 1
            elif t[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    raise LLMError(f"could not parse JSON from response: {text[:200]}")


def complete_json(provider, system, user, max_tokens=None, temperature=0.1):
    """Complete and parse a JSON response. Appends a JSON-only instruction."""
    user2 = user + "\n\nRespond with ONLY valid JSON. No prose, no code fences."
    raw = provider.complete(system, user2, max_tokens=max_tokens, temperature=temperature)
    return extract_json(raw)


# ── Vision oracle ────────────────────────────────────────────────────────────

def call_vision_oracle(vision_cfg: dict, screenshot_bytes: bytes, question: str,
                       timeout: int = 30) -> str:
    """One-shot multimodal call to a vision-capable model (OpenAI-compatible).
    vision_cfg: {model, base_url, api_key_env, max_tokens}
    Returns the model's text description of the screenshot."""
    model = vision_cfg.get("model", "")
    base_url = (vision_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key_env = vision_cfg.get("api_key_env", "OPENAI_API_KEY")
    max_tokens = int(vision_cfg.get("max_tokens", 512))
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise LLMNotConfigured(
            f"Vision oracle key not set (env {api_key_env!r}). "
            f"Add it to Settings or set the env var."
        )
    img_b64 = base64.b64encode(screenshot_bytes).decode()
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]
        }]
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _http_post_json(f"{base_url}/chat/completions", headers, payload, timeout=timeout)
    return resp["choices"][0]["message"]["content"]


# ── CLI (smoke) ──

if __name__ == "__main__":
    import sys
    cfg = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")) as f:
            cfg = json.load(f)
    except Exception:
        pass
    name = sys.argv[1] if len(sys.argv) > 1 else None
    prov = make_provider(cfg, name)
    print(f"provider: {prov.name} model: {prov.cfg.get('model', '(n/a)')}")
    try:
        print(prov.complete("You are a test.", "Say hello in 3 words.", max_tokens=32))
    except LLMNotConfigured as e:
        print(f"[not configured] {e}")
    except LLMError as e:
        print(f"[error] {e}")
