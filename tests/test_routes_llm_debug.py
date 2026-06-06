from guild_manager_bench.bench.llm import (
    AnthropicMessagesAgent,
    OpenAIChatCompletionsAgent,
)
from guild_manager_bench.api.routes_llm_debug import (
    DEFAULT_LLM_DEBUG_TIMEOUT,
    LLM_DEBUG_EVENT_SEND_TIMEOUT,
    _debug_agent,
    _float_value,
    _optional_bool,
)


def test_llm_debug_timeouts_default_to_180_seconds() -> None:
    assert DEFAULT_LLM_DEBUG_TIMEOUT == 180.0
    assert LLM_DEBUG_EVENT_SEND_TIMEOUT == 180.0
    assert _float_value({}, "timeout", DEFAULT_LLM_DEBUG_TIMEOUT) == 180.0


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


def test_llm_debug_optional_bool_is_tristate() -> None:
    assert _optional_bool({}, "thinking") is None
    assert _optional_bool({"thinking": "enabled"}, "thinking") is True
    assert _optional_bool({"thinking": "disabled"}, "thinking") is False
