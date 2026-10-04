"""Provider identity is configuration; wire protocols are adapter responsibilities.

A profile in ``config.llm.providers`` may name a preset from
``provider_catalog.json`` (``"preset": "mimo"``); the preset supplies the base
URL, key env name, token parameter, temperature support and thinking/reasoning
handling, and any field set on the profile overrides it. Profiles without a
preset are plain custom endpoints.
"""
import json
import os
from copy import deepcopy
from functools import lru_cache

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_catalog.json")

PLANNER_PROTOCOLS = {"openai", "openai_responses", "anthropic", "google", "ollama"}
DECISION_PROTOCOLS = {"typesafe", "openrouter_decisions"}
PROTOCOLS = PLANNER_PROTOCOLS | DECISION_PROTOCOLS
NO_KEY_PROTOCOLS = {"ollama"}

# Catalog metadata that is UI-only and never part of a resolved profile.
_CATALOG_ONLY = {"label", "role", "key_url", "docs_url", "endpoints", "models", "default_model", "thinking", "validate", "needs_key"}


class ProfileError(ValueError):
    pass


class NoProviderConfigured(ProfileError):
    """No default provider is set and none was requested."""


@lru_cache(maxsize=1)
def load_catalog():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def preset_for(name, cfg):
    presets = load_catalog()["presets"]
    preset_id = cfg.get("preset") or (name if name in presets and name != "custom" else None)
    if preset_id and preset_id not in presets:
        raise ProfileError(f"Unknown provider preset: {preset_id!r}")
    return preset_id, (presets.get(preset_id) if preset_id else None)


def _merge_settings(base, extra):
    out = dict(base or {})
    for key, value in (extra or {}).items():
        if key == "extra_body" and isinstance(value, dict):
            out["extra_body"] = {**(out.get("extra_body") or {}), **value}
        else:
            out[key] = value
    return out


def resolve_profile(config, name=None, role="planner"):
    llm = (config or {}).get("llm") or {}
    roles = llm.get("roles") or {}
    name = name or roles.get(role) or llm.get("_session_provider") or os.environ.get("ATS_LLM_PROVIDER") or roles.get("primary") or llm.get("default_provider")
    if not name:
        raise NoProviderConfigured("No AI provider configured. Add one in Settings -> AI / LLM.")
    profiles = llm.get("providers") or {}
    if name not in profiles:
        raise ProfileError(f"Unknown model profile: {name!r}")
    own = deepcopy(profiles[name])
    preset_id, preset = preset_for(name, own)
    cfg = {}
    if preset:
        cfg = {k: deepcopy(v) for k, v in preset.items() if k not in _CATALOG_ONLY}
        endpoints = preset.get("endpoints") or []
        chosen = next((e for e in endpoints if e["id"] == own.get("endpoint")), endpoints[0] if endpoints else None)
        if chosen:
            cfg["base_url"] = chosen["base_url"]
        if preset.get("default_model"):
            cfg["model"] = preset["default_model"]
        cfg["preset"] = preset_id
    cfg.update({k: v for k, v in own.items() if v not in (None, "")})
    protocol = cfg.setdefault("protocol", "openai")
    if protocol not in PROTOCOLS:
        raise ProfileError(f"Unsupported API protocol: {protocol!r}")
    thinking = (preset or {}).get("thinking")
    if thinking:
        mode = own.get("thinking") or thinking.get("default", "on")
        if mode not in {"on", "off"}:
            raise ProfileError("thinking must be 'on' or 'off'")
        cfg["thinking"] = mode
        cfg["model_settings"] = _merge_settings(thinking.get(mode), own.get("model_settings"))
    if not cfg.get("model"):
        raise ProfileError(f"Profile {name!r} requires a model")
    if cfg.get("token_parameter", "max_tokens") not in {"max_tokens", "max_completion_tokens"}:
        raise ProfileError("Unsupported token_parameter")
    timeout = cfg.setdefault("timeout", 60)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ProfileError("Model timeout must be between 0 and 300 seconds")
    return name, cfg


def require_capability(cfg, capability):
    if (cfg.get("capabilities") or {}).get(capability) is False:
        raise ProfileError(f"Selected profile does not support {capability}")


def needs_key(cfg):
    return cfg.get("protocol") not in NO_KEY_PROTOCOLS and cfg.get("needs_key", True) is not False


def vision_unavailable_reason(config):
    """Local capability preflight only; never probes a provider or reveals a key."""
    if config.get("_vision_runtime_unavailable"):
        return "provider_rejected_vision"
    name = ((config.get("llm") or {}).get("roles") or {}).get("vision")
    try:
        cfg = resolve_profile(config, name, "vision")[1] if name else config.get("vision_oracle") or {}
    except ProfileError:
        return "invalid_vision_profile"
    if not cfg.get("model"):
        return "vision_not_configured"
    if (cfg.get("capabilities") or {}).get("vision") is False:
        return "vision_not_supported"
    protocol = cfg.get("protocol", "openai")
    if protocol not in PLANNER_PROTOCOLS:
        return "unsupported_vision_protocol"
    key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
    if needs_key(cfg) and (not isinstance(key_env, str) or not key_env or not os.environ.get(key_env, "")):
        return "vision_key_missing"
    return None


def _openai_profile(cfg):
    """Per-preset Chat Completions quirks: reasoning models (MiMo, DeepSeek) must
    get their reasoning sent back in tool loops or they reject the request."""
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    field = cfg.get("thinking_field")
    if not field:
        return None
    return OpenAIModelProfile(openai_chat_thinking_field=field, openai_chat_send_back_thinking_parts="field")


def build_agent_model(config, name=None, role="planner"):
    name, cfg = resolve_profile(config, name, role)
    require_capability(cfg, "tools")
    protocol = cfg["protocol"]
    if protocol in DECISION_PROTOCOLS:
        raise ProfileError(f"{protocol} is a decision protocol, not a tool-calling planner")
    key = os.environ.get(cfg.get("api_key_env") or "", "")
    if needs_key(cfg) and not key:
        raise ProfileError(f"{name}: add the API key in Settings -> AI / LLM (or set {cfg.get('api_key_env') or 'api_key_env'})")
    settings = cfg.get("model_settings")
    if protocol in {"openai", "ollama", "openai_responses"}:
        from openai import AsyncOpenAI
        from pydantic_ai.providers.openai import OpenAIProvider
        base = cfg.get("base_url") or ("http://localhost:11434" if protocol == "ollama" else "https://api.openai.com/v1")
        if protocol == "ollama" and not base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        client = AsyncOpenAI(api_key=key or "ollama", base_url=base, timeout=cfg["timeout"], max_retries=0,
                             default_headers=cfg.get("headers") or None)
        provider = OpenAIProvider(openai_client=client)
        if protocol == "openai_responses":
            from pydantic_ai.models.openai import OpenAIResponsesModel
            model = OpenAIResponsesModel(cfg["model"], provider=provider, settings=settings)
        else:
            from pydantic_ai.models.openai import OpenAIChatModel

            class ConfiguredChatModel(OpenAIChatModel):
                def prepare_request(self, model_settings, model_request_parameters):
                    settings, parameters = super().prepare_request(model_settings, model_request_parameters)
                    settings = dict(settings or {})
                    if cfg.get("token_parameter", "max_tokens") == "max_tokens" and "max_tokens" in settings:
                        cap = settings.pop("max_tokens")
                        settings["extra_body"] = {**(settings.get("extra_body") or {}), "max_tokens": cap}
                    if not cfg.get("supports_temperature", True):
                        settings.pop("temperature", None)
                    return settings, parameters

            model = ConfiguredChatModel(cfg["model"], provider=provider, profile=_openai_profile(cfg), settings=settings)
    elif protocol == "anthropic":
        try:
            from anthropic import AsyncAnthropic
            from pydantic_ai.providers.anthropic import AnthropicProvider
            from pydantic_ai.models.anthropic import AnthropicModel
        except ImportError as exc:
            raise ProfileError("Native Anthropic support requires pydantic-ai-slim[anthropic]") from exc
        client = AsyncAnthropic(api_key=key, base_url=cfg.get("base_url") or "https://api.anthropic.com", timeout=cfg["timeout"], max_retries=0)
        model = AnthropicModel(cfg["model"], provider=AnthropicProvider(anthropic_client=client), settings=settings)
    elif protocol == "google":
        try:
            from pydantic_ai.providers.google import GoogleProvider
            from pydantic_ai.models.google import GoogleModel
        except ImportError as exc:
            raise ProfileError("Native Gemini support requires pydantic-ai-slim[google]") from exc
        model = GoogleModel(cfg["model"], provider=GoogleProvider(api_key=key), settings=settings)
    else:
        raise ProfileError(f"{protocol} is a decision protocol, not a tool-calling planner")
    return model, name, cfg["model"]
