import pytest

from guild_manager_bench.bench.llm import (
    AnthropicMessagesAgent,
    GoogleGenerateContentAgent,
    OpenAIChatCompletionsAgent,
    OpenAIResponsesAgent,
)
from guild_manager_bench.api.routes_llm_debug import (
    DEFAULT_LLM_DEBUG_TIMEOUT,
    LLM_DEBUG_EVENT_SEND_TIMEOUT,
    _debug_agent,
    _float_value,
    _optional_bool,
)


def test_llm_debug_request_timeout_defaults_to_600_seconds() -> None:
    assert DEFAULT_LLM_DEBUG_TIMEOUT == 600.0
    assert LLM_DEBUG_EVENT_SEND_TIMEOUT == 180.0
    assert _float_value({}, "timeout", DEFAULT_LLM_DEBUG_TIMEOUT) == 600.0


def test_llm_debug_builds_anthropic_agent_with_thinking_controls() -> None:
    agent = _debug_agent(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": "https://api.anthropic.com",
            "thinking": True,
            "thinking_effort": "high",
        }
    )

    assert isinstance(agent, AnthropicMessagesAgent)
    assert agent.config.thinking is True
    assert agent.config.effort == "high"


def test_llm_debug_defaults_to_openai_agent() -> None:
    agent = _debug_agent({"model": "test-model"})

    assert isinstance(agent, OpenAIChatCompletionsAgent)


def test_llm_debug_builds_openai_responses_agent() -> None:
    agent = _debug_agent(
        {
            "provider": "openai-responses",
            "model": "gpt-5-mini",
            "reasoning_effort": "high",
        }
    )

    assert isinstance(agent, OpenAIResponsesAgent)
    assert agent.config.reasoning_effort == "high"


def test_llm_debug_builds_google_agent_with_native_settings() -> None:
    agent = _debug_agent({
        "provider": "google", "model": "gemini-2.5-flash", "api_key": "test-key",
        "base_url": "https://google-proxy.test/v1beta", "temperature": 0.4,
        "max_tokens": 8192, "timeout": 60,
    })

    assert isinstance(agent, GoogleGenerateContentAgent)
    assert agent.config.model == "gemini-2.5-flash"
    assert agent.config.api_key == "test-key"
    assert agent.config.base_url == "https://google-proxy.test/v1beta"
    assert agent.config.temperature == 0.4
    assert agent.config.max_tokens == 8192
    assert agent.config.timeout == 60


@pytest.mark.parametrize("provider, field", [
    ("openai", "reasoning_effort"), ("openai-responses", "reasoning_effort"),
    ("anthropic", "effort"), ("google", "reasoning_effort"),
])
def test_llm_debug_uses_unified_reasoning_effort(provider, field):
    agent = _debug_agent({"provider": provider, "model": "test-model", "reasoning_effort": "high"})
    assert getattr(agent.config, field) == "high"


def test_llm_debug_new_anthropic_effort_takes_precedence_over_legacy_field():
    agent = _debug_agent({
        "provider": "anthropic", "model": "test-model",
        "reasoning_effort": "high", "thinking_effort": "low",
    })
    assert agent.config.effort == "high"


@pytest.mark.parametrize("provider, effort", [("google", "max"), ("anthropic", "minimal")])
def test_llm_debug_rejects_unsupported_provider_effort(provider, effort):
    with pytest.raises(RuntimeError, match="reasoning_effort"):
        _debug_agent({"provider": provider, "model": "test-model", "reasoning_effort": effort})


def test_llm_debug_optional_bool_is_tristate() -> None:
    assert _optional_bool({}, "thinking") is None
    assert _optional_bool({"thinking": "enabled"}, "thinking") is True
    assert _optional_bool({"thinking": "disabled"}, "thinking") is False
