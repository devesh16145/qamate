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


def test_mimo_preset_defaults_and_explicit_overrides():
    cfg = {"llm": {"providers": {"mimo": {"preset": "mimo"}}}}
    _, profile = resolve_profile(cfg, "mimo")
    assert profile["model"] == "mimo-v2.6-flash"
    assert profile["protocol"] == "openai"
    assert profile["api_key_env"] == "MIMO_API_KEY"
    assert profile["base_url"] == "https://api.xiaomimimo.com/v1"
    assert profile["token_parameter"] == "max_completion_tokens"
    assert profile["supports_temperature"] is False
    assert profile["model_settings"]["extra_body"] == {"thinking": {"type": "enabled"}}
    cfg["llm"]["providers"]["mimo"].update(model="user-selected-model", endpoint="tp-sgp", thinking="off")
    _, profile = resolve_profile(cfg, "mimo")
    assert profile["model"] == "user-selected-model"
    assert profile["base_url"] == "https://token-plan-sgp.xiaomimimo.com/v1"
    assert profile["model_settings"]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_profile_named_after_preset_inherits_it_and_explicit_fields_win():
    cfg = {"llm": {"providers": {"mimo": {"model": "x", "base_url": "https://example.test/v1"}}}}
    _, profile = resolve_profile(cfg, "mimo")
    assert profile["preset"] == "mimo" and profile["base_url"] == "https://example.test/v1" and profile["model"] == "x"


def test_no_provider_configured_is_reported_as_not_configured(monkeypatch):
    from model_profiles import NoProviderConfigured
    monkeypatch.delenv("ATS_LLM_PROVIDER", raising=False)
    with pytest.raises(NoProviderConfigured):
        resolve_profile({"llm": {"providers": {}}})
    with pytest.raises(llm.LLMNotConfigured):
        llm.make_provider({})


def test_catalog_presets_are_well_formed():
    from model_profiles import load_catalog, PROTOCOLS
    presets = load_catalog()["presets"]
    assert {"mimo", "anthropic", "openai", "gemini", "openrouter", "deepseek", "ollama", "custom"} <= set(presets)
    assert "mock" not in presets
    for pid, pre in presets.items():
        assert pre["label"] and pre["protocol"] in PROTOCOLS, pid
        ids = [m["id"] for m in pre["models"]]
        assert not pre["default_model"] or pre["default_model"] in ids, pid
        for ep in pre["endpoints"]:
            assert ep["base_url"].startswith(("https://", "http://localhost")), pid
        if pre.get("thinking"):
            assert {"on", "off"} <= set(pre["thinking"]) and pre["thinking"]["default"] in {"on", "off"}, pid


def test_jev_presets_are_decision_only(monkeypatch):
    from model_profiles import build_agent_model
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg = {"llm": {"providers": {"jev": {"preset": "jev"}, "jor": {"preset": "jev-openrouter"}}}}
    _, direct = resolve_profile(cfg, "jev", "decision")
    assert (direct["protocol"], direct["base_url"], direct["model"]) == ("typesafe", "https://api.typesafe.ai/v1", "jev-latest")
    _, gateway = resolve_profile(cfg, "jor", "decision")
    assert (gateway["protocol"], gateway["model"], gateway["api_key_env"]) == ("openrouter_decisions", "typesafe/jev-1.13", "OPENROUTER_API_KEY")
    assert "role" not in direct
    with pytest.raises(ProfileError, match="decision protocol"):
        build_agent_model(cfg, "jev")


def test_reasoning_presets_send_reasoning_back_in_tool_loops(monkeypatch):
    from model_profiles import build_agent_model
    monkeypatch.setenv("MIMO_API_KEY", "fake")
    model, _, model_id = build_agent_model({"llm": {"default_provider": "mimo", "providers": {"mimo": {"preset": "mimo"}}}})
    assert model_id == "mimo-v2.6-flash"
    assert model.profile.openai_chat_thinking_field == "reasoning_content"
    assert model.profile.openai_chat_send_back_thinking_parts == "field"


def test_plain_completion_applies_preset_request_extras(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "fake")
    calls = []
    def post(url, headers, payload, **kwargs):
        calls.append((url, payload))
        return {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr(llm, "_http_post_json", post)
    cfg = {"llm": {"providers": {"mimo": {"preset": "mimo"}}}}
    assert llm.make_provider(cfg, "mimo").complete("s", "u") == "ok"
    url, payload = calls[0]
    assert url == "https://api.xiaomimimo.com/v1/chat/completions"
    assert payload["thinking"] == {"type": "enabled"} and "temperature" not in payload and "max_completion_tokens" in payload


def test_gemini_plain_completion_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    calls = []
    monkeypatch.setattr(llm, "_http_post_json", lambda url, headers, payload, **kw: calls.append(url) or {"choices": [{"message": {"content": "ok"}}]})
    llm.make_provider({"llm": {"providers": {"g": {"preset": "gemini"}}}}, "g").complete("s", "u")
    assert calls[0] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def config(protocol="openai"):
    return {"llm": {"default_provider": "custom", "providers": {"custom": {"protocol": protocol, "model": "replace-me", "base_url": "http://localhost:1234/v1", "api_key_env": "TEST_MODEL_KEY"}}}}


def test_arbitrary_profile_is_supported():
    provider = llm.make_provider(config())
    assert isinstance(provider, llm.OpenAIProvider)
    assert provider.name == "custom"


def test_roles(monkeypatch):
    monkeypatch.delenv("ATS_LLM_PROVIDER", raising=False)
    cfg = config()
    cfg["llm"]["providers"]["extract"] = {"protocol": "ollama", "model": "local"}
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


@pytest.mark.parametrize("protocol", ["openai", "openai_responses", "anthropic", "google", "ollama"])
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
