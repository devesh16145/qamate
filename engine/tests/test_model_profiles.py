import pytest
from model_profiles import resolve_profile, ProfileError, require_capability
import llm
from model_profiles import vision_unavailable_reason


def test_vision_preflight_is_explicit_and_role_aware(monkeypatch):
    monkeypatch.delenv("VISION_TEST_KEY", raising=False)
    assert vision_unavailable_reason({}) == "vision_not_configured"
    cfg = {"vision_oracle": {"model": "test", "api_key_env": "VISION_TEST_KEY"}}
    assert vision_unavailable_reason(cfg) == "vision_key_missing"
    monkeypatch.setenv("VISION_TEST_KEY", "fake")
    assert vision_unavailable_reason(cfg) is None
    cfg["vision_oracle"]["capabilities"] = {"vision": False}
    assert vision_unavailable_reason(cfg) == "vision_not_supported"
    cfg["llm"] = {"roles": {"vision": "local"}, "providers": {"local": {"protocol": "ollama", "model": "vision"}}}
    assert vision_unavailable_reason(cfg) is None
    cfg["llm"]["roles"]["vision"] = "missing"
    assert vision_unavailable_reason(cfg) == "invalid_vision_profile"
    assert vision_unavailable_reason({"vision_oracle": {"model": "test", "api_key_env": None}}) == "vision_key_missing"


def test_mimo_default_and_explicit_model_override():
    cfg = {"llm": llm.DEFAULT_LLM_CONFIG}
    _, profile = resolve_profile(cfg, "mimo")
    assert profile["model"] == "mimo-v2.6-flash"
    assert profile["protocol"] == "openai"
    assert profile["api_key_env"] == "MIMO_API_KEY"
    from copy import deepcopy
    custom = deepcopy(cfg)
    custom["llm"]["providers"]["mimo"]["model"] = "user-selected-model"
    assert resolve_profile(custom, "mimo")[1]["model"] == "user-selected-model"


def config(protocol="openai"):
    return {"llm": {"default_provider": "custom", "providers": {"custom": {"protocol": protocol, "model": "replace-me", "base_url": "http://localhost:1234/v1", "api_key_env": "TEST_MODEL_KEY"}}}}


def test_arbitrary_profile_is_supported():
    provider = llm.make_provider(config())
    assert isinstance(provider, llm.OpenAIProvider)
    assert provider.name == "custom"


def test_roles(monkeypatch):
    monkeypatch.delenv("ATS_LLM_PROVIDER", raising=False)
    cfg = config()
    cfg["llm"]["providers"]["extract"] = {"protocol": "mock"}
    cfg["llm"]["roles"] = {"extraction": "extract"}
    assert llm.make_provider(cfg).name == "extract"
    assert resolve_profile(cfg)[0] == "custom"
    assert resolve_profile(cfg, "custom", "extraction")[0] == "custom"
    cfg["llm"]["_session_provider"] = "extract"
    cfg["llm"]["roles"] = {}
    assert llm.make_provider(cfg).name == "extract"
    cfg["llm"]["roles"]["extraction"] = "custom"
    assert llm.make_provider(cfg).name == "custom"


def test_unknown_and_unsupported():
    with pytest.raises(ProfileError):
        resolve_profile(config(), "typo")
    with pytest.raises(ProfileError):
        resolve_profile(config("typo"))
    with pytest.raises(ProfileError):
        require_capability({"capabilities": {"tools": False}}, "tools")


def test_custom_endpoint_and_parameters(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "fake")
    cfg = config()
    cfg["llm"]["providers"]["custom"].update(token_parameter="max_completion_tokens", supports_temperature=False)
    calls = []
    def post(url, headers, payload, **kwargs):
        calls.append((url, payload))
        return {"choices": [{"message": {"content": "yes"}}]}
    monkeypatch.setattr(llm, "_http_post_json", post)
    assert llm.make_provider(cfg).complete("system", "user") == "yes"
    assert calls[0][0] == "http://localhost:1234/v1/chat/completions"
    assert "max_completion_tokens" in calls[0][1]
    assert "temperature" not in calls[0][1]


def test_native_anthropic_helper():
    assert isinstance(llm.make_provider(config("anthropic")), llm.AnthropicProvider)


def test_openrouter_decisions_cannot_be_planner(monkeypatch):
    from model_profiles import build_agent_model
    monkeypatch.setenv("TEST_MODEL_KEY", "fake")
    with pytest.raises(ProfileError, match="decision protocol"):
        build_agent_model(config("openrouter_decisions"))


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "ollama", "mock"])
def test_agent_models_construct_without_network(protocol, monkeypatch):
    from model_profiles import build_agent_model
    monkeypatch.setenv("TEST_MODEL_KEY", "fake")
    model, name, model_id = build_agent_model(config(protocol))
    assert name == "custom" and model_id == "replace-me"


def test_agent_token_parameter_translation(monkeypatch):
    from model_profiles import build_agent_model
    from pydantic_ai.models import ModelRequestParameters
    monkeypatch.setenv("TEST_MODEL_KEY", "fake")
    cfg = config()
    cfg["llm"]["providers"]["custom"].update(token_parameter="max_tokens", supports_temperature=False)
    model, _, _ = build_agent_model(cfg)
    settings, _ = model.prepare_request({"max_tokens": 123, "temperature": 0.2}, ModelRequestParameters())
    assert "max_tokens" not in settings and "temperature" not in settings
    assert settings["extra_body"]["max_tokens"] == 123


def test_native_vision_endpoint(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "fake")
    calls = []
    def post(url, headers, payload, **kwargs):
        calls.append((url, payload))
        return {"content": [{"type": "text", "text": "a button"}]}
    monkeypatch.setattr(llm, "_http_post_json", post)
    cfg = config("anthropic")["llm"]["providers"]["custom"]
    assert llm.call_vision_oracle(cfg, b"fake png", "What is shown?") == "a button"
    assert calls[0][0] == "http://localhost:1234/v1/messages"
    assert calls[0][1]["messages"][0]["content"][0]["source"]["type"] == "base64"
