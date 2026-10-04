"""
QAmate — Pluggable LLM layer
===============================

Provider-agnostic LLM access for the PRD Extractor (L2) and Test Synthesizer
(L3). Customers bring their own key (per the product decision); providers talk
plain HTTP via `urllib` so there is **no SDK dependency** to version-pin or ship.

Protocols (presets in provider_catalog.json pick one):
  - anthropic          Claude — POST /v1/messages, prompt caching on the system block
  - openai             any OpenAI-compatible Chat Completions endpoint (MiMo, OpenRouter, DeepSeek, custom)
  - openai_responses   OpenAI — plain completions use Chat Completions (tools go via the agent's Responses model)
  - google             Gemini — plain completions via Gemini's OpenAI-compatible endpoint
  - ollama             local models — POST {base_url}/api/chat

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
from model_profiles import resolve_profile, ProfileError, NoProviderConfigured, needs_key


class LLMError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class LLMNotConfigured(LLMError):
    """Raised when a provider is selected but its API key isn't set. Callers
    should catch this and fall back to the deterministic path (or prompt the
    user to configure a key)."""


# Used only when config.json has no "llm" section at all: nothing is configured,
# so LLM features report "not configured" and callers fall back.
DEFAULT_LLM_CONFIG = {"providers": {}}


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
        raise LLMError(f"HTTP {e.code} from {url}: {body}", status_code=e.code)
    except urllib.error.URLError as e:
        raise LLMError(f"could not reach {url}: {e.reason}")
    except Exception as e:
        raise LLMError(f"request to {url} failed: {e}")


# ── Providers ────────────────────────────────────────────────────────────────

class BaseProvider:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg or {}
        self.last_usage = None

    def _key(self):
        if not needs_key(self.cfg):
            return ""
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


class AnthropicProvider(BaseProvider):
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        payload = {
            "model": self.cfg["model"],
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 4096),
            "temperature": temperature,
            # System sent as a cacheable block (prompt caching) — big PRD/app-model
            # system prompts are reused across requirements, so this is a real win.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if not self.cfg.get("supports_temperature", True):
            payload.pop("temperature", None)
        headers = {"x-api-key": self._key(), "anthropic-version": "2023-06-01"}
        base = (self.cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        endpoint = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        resp = _http_post_json(endpoint, headers, payload, timeout=self.cfg.get("timeout", 60))
        self.last_usage = resp.get("usage")
        return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


class OpenAIProvider(BaseProvider):
    default_base = "https://api.openai.com/v1"

    def _base(self):
        return (self.cfg.get("base_url") or self.default_base).rstrip("/")

    def complete(self, system, user, max_tokens=None, temperature=0.2):
        payload = {
            "model": self.cfg["model"],
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 4096),
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        token_parameter = self.cfg.get("token_parameter", "max_tokens")
        if token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise LLMError("Unsupported token_parameter")
        payload[token_parameter] = payload.pop("max_tokens")
        if not self.cfg.get("supports_temperature", True):
            payload.pop("temperature", None)
        # Preset request extras, e.g. MiMo/DeepSeek {"thinking": {"type": "enabled"}}.
        payload.update(((self.cfg.get("model_settings") or {}).get("extra_body")) or {})
        headers = {"Authorization": f"Bearer {self._key()}", **(self.cfg.get("headers") or {})}
        resp = _http_post_json(f"{self._base()}/chat/completions", headers, payload, timeout=self.cfg.get("timeout", 60))
        self.last_usage = resp.get("usage")
        return resp["choices"][0]["message"]["content"]


class GeminiProvider(OpenAIProvider):
    """Gemini through its OpenAI-compatible endpoint ({base}/openai)."""
    default_base = "https://generativelanguage.googleapis.com/v1beta"

    def _base(self):
        base = super()._base()
        return base if base.endswith("/openai") else base + "/openai"


class OllamaProvider(BaseProvider):
    """Local models — no key required."""
    def complete(self, system, user, max_tokens=None, temperature=0.2):
        base = (self.cfg.get("base_url") or "http://localhost:11434").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        payload = {
            "model": self.cfg["model"],
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        resp = _http_post_json(f"{base}/api/chat", {}, payload, timeout=self.cfg.get("timeout", 60))
        if "prompt_eval_count" in resp or "eval_count" in resp:
            self.last_usage = {"input_tokens": resp.get("prompt_eval_count"), "output_tokens": resp.get("eval_count")}
        return (resp.get("message") or {}).get("content", "")


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "openai_responses": OpenAIProvider,
    "google": GeminiProvider,
    "ollama": OllamaProvider,
}


def make_provider(config, name=None, role="extraction"):
    """Build a provider from config['llm']. `name` overrides the default."""
    effective = config if (config or {}).get("llm") else {"llm": DEFAULT_LLM_CONFIG}
    try:
        name, cfg = resolve_profile(effective, name, role)
    except NoProviderConfigured as exc:
        raise LLMNotConfigured(str(exc)) from exc
    except ProfileError as exc:
        raise LLMError(str(exc)) from exc
    cls = _PROVIDERS.get(cfg["protocol"])
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
    from model_profiles import require_capability
    require_capability(vision_cfg, "vision")
    protocol = vision_cfg.get("protocol", "openai")
    if protocol not in {"openai", "openai_responses", "google", "anthropic", "ollama"}:
        raise LLMError(f"Vision is unsupported for protocol {protocol}")
    model = vision_cfg.get("model", "")
    base_url = (vision_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key_env = vision_cfg.get("api_key_env", "OPENAI_API_KEY")
    max_tokens = int(vision_cfg.get("max_tokens", 512))
    api_key = os.environ.get(api_key_env, "")
    if not api_key and needs_key(vision_cfg):
        raise LLMNotConfigured(
            f"Vision oracle key not set (env {api_key_env!r}). "
            f"Add it to Settings or set the env var."
        )
    img_b64 = base64.b64encode(screenshot_bytes).decode()
    timeout = min(timeout, vision_cfg.get("timeout", timeout))
    if protocol == "anthropic":
        base = (vision_cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        endpoint = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        payload = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": question}]}]}
        response = _http_post_json(endpoint, {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, payload, timeout=timeout)
        return "".join(block.get("text", "") for block in response.get("content", []) if block.get("type") == "text")
    if protocol == "ollama":
        base_url = (vision_cfg.get("base_url") or "http://localhost:11434").rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
    if protocol == "google" and not base_url.endswith("/openai"):
        base_url += "/openai"
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
    token_parameter = vision_cfg.get("token_parameter", "max_tokens")
    if token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise LLMError("Unsupported token_parameter")
    payload[token_parameter] = payload.pop("max_tokens")
    payload.update(((vision_cfg.get("model_settings") or {}).get("extra_body")) or {})
    headers = {"Authorization": f"Bearer {api_key or 'ollama'}", **(vision_cfg.get("headers") or {})}
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
