"""Provider identity is configuration; wire protocols are adapter responsibilities."""
import os
from copy import deepcopy


class ProfileError(ValueError):
    pass


def resolve_profile(config, name=None, role="planner"):
    llm = (config or {}).get("llm") or {}
    roles = llm.get("roles") or {}
    name = name or roles.get(role) or llm.get("_session_provider") or os.environ.get("ATS_LLM_PROVIDER") or roles.get("primary") or llm.get("default_provider", "mock")
    profiles = llm.get("providers") or {}
    if name not in profiles:
        raise ProfileError(f"Unknown model profile: {name!r}")
    cfg = deepcopy(profiles[name])
    # Legacy configurations stay valid. New profiles should declare their protocol.
    protocol = cfg.setdefault("protocol", {"anthropic": "anthropic", "ollama": "ollama", "mock": "mock"}.get(name, "openai"))
    if protocol not in {"openai", "anthropic", "ollama", "mock", "typesafe", "openrouter_decisions"}:
        raise ProfileError(f"Unsupported API protocol: {protocol!r}")
    if name in {"mimo", "mimo-ultraspeed"}:
        cfg.setdefault("token_parameter", "max_completion_tokens")
        cfg.setdefault("supports_temperature", False)
    if protocol != "mock" and not cfg.get("model"):
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
    if protocol not in {"openai", "anthropic", "ollama"}:
        return "unsupported_vision_protocol"
    key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
    if protocol != "ollama" and (not isinstance(key_env, str) or not key_env or not os.environ.get(key_env, "")):
        return "vision_key_missing"
    return None


def build_agent_model(config, name=None, role="planner"):
    name, cfg = resolve_profile(config, name, role)
    require_capability(cfg, "tools")
    protocol = cfg["protocol"]
    key = os.environ.get(cfg.get("api_key_env", ""), "")
    if protocol not in {"ollama", "mock"} and not key:
        raise ProfileError(f"{name}: configure the {cfg.get('api_key_env', 'missing api_key_env')} environment key")
    if protocol in {"openai", "ollama"}:
        from openai import AsyncOpenAI
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.models.openai import OpenAIChatModel
        base = cfg.get("base_url") or ("http://localhost:11434" if protocol == "ollama" else "https://api.openai.com/v1")
        if protocol == "ollama" and not base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        client = AsyncOpenAI(api_key=key or "ollama", base_url=base, timeout=cfg["timeout"], max_retries=0)
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
        model = ConfiguredChatModel(cfg["model"], provider=OpenAIProvider(openai_client=client), settings=cfg.get("model_settings"))
    elif protocol == "anthropic":
        try:
            from anthropic import AsyncAnthropic
            from pydantic_ai.providers.anthropic import AnthropicProvider
            from pydantic_ai.models.anthropic import AnthropicModel
        except ImportError as exc:
            raise ProfileError("Native Anthropic support requires pydantic-ai-slim[anthropic]") from exc
        client = AsyncAnthropic(api_key=key, base_url=cfg.get("base_url") or "https://api.anthropic.com", timeout=cfg["timeout"], max_retries=0)
        model = AnthropicModel(cfg["model"], provider=AnthropicProvider(anthropic_client=client), settings=cfg.get("model_settings"))
    elif protocol == "mock":
        from pydantic_ai.models.test import TestModel
        model = TestModel(call_tools=[])
    else:
        raise ProfileError(f"{protocol} is a decision protocol, not a tool-calling planner")
    return model, name, cfg.get("model", "mock")
