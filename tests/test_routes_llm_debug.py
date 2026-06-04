from guild_manager_bench.api.routes_llm_debug import (
    DEFAULT_LLM_DEBUG_TIMEOUT,
    LLM_DEBUG_EVENT_SEND_TIMEOUT,
    _float_value,
)


def test_llm_debug_timeouts_default_to_180_seconds() -> None:
    assert DEFAULT_LLM_DEBUG_TIMEOUT == 180.0
    assert LLM_DEBUG_EVENT_SEND_TIMEOUT == 180.0
    assert _float_value({}, "timeout", DEFAULT_LLM_DEBUG_TIMEOUT) == 180.0
