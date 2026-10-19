"""会话路由头（x-opencode-session）相关测试。

OpenCode Go 等网关缺少该头时会直接返回 HTTP 400 MissingSessionID，
因此三个 provider 适配器都必须稳定携带会话标识。
"""

from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest

from guild_manager_bench.bench.llm import (
    OPENCODE_SESSION_HEADER,
    AnthropicMessagesAgent,
    AnthropicMessagesConfig,
    AnthropicMessagesError,
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    OpenAIChatCompletionsAgent,
    OpenAIChatCompletionsConfig,
    OpenAICompatibleError,
    OpenAIResponsesAgent,
    OpenAIResponsesConfig,
    OpenAIResponsesError,
    run_llm_game,
)
from guild_manager_bench.bench.llm.session import (
    SHARED_SESSION_ENV,
    new_session_id,
    resolve_session_id,
    session_headers,
)


def test_new_session_id_is_unique_hex() -> None:
    first = new_session_id()
    second = new_session_id()

    assert len(first) == 32
    assert int(first, 16) >= 0
    assert first != second


def test_resolve_session_id_prefers_explicit_argument() -> None:
    resolved = resolve_session_id(
        " explicit-id ",
        {SHARED_SESSION_ENV: "dotenv-id", "OPENAI_SESSION_ID": "dotenv-provider"},
    )

    assert resolved.value == "explicit-id"
    assert resolved.explicit is True


def test_resolve_session_id_prefers_provider_env_over_shared_env(monkeypatch) -> None:
    monkeypatch.setenv(SHARED_SESSION_ENV, "shared-id")
    monkeypatch.setenv("OPENAI_SESSION_ID", "provider-id")

    resolved = resolve_session_id(None, {}, "OPENAI_SESSION_ID")

    assert resolved.value == "provider-id"
    assert resolved.explicit is True


def test_resolve_session_id_falls_back_to_shared_env(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_SESSION_ID", raising=False)
    monkeypatch.setenv(SHARED_SESSION_ENV, "shared-id")

    resolved = resolve_session_id(None, {}, "OPENAI_SESSION_ID")

    assert resolved.value == "shared-id"
    assert resolved.explicit is True


def test_resolve_session_id_uses_dotenv_when_no_process_env(monkeypatch) -> None:
    monkeypatch.delenv(SHARED_SESSION_ENV, raising=False)
    monkeypatch.delenv("OPENAI_SESSION_ID", raising=False)

    resolved = resolve_session_id(None, {"OPENAI_SESSION_ID": "dotenv-id"}, "OPENAI_SESSION_ID")

    assert resolved.value == "dotenv-id"
    assert resolved.explicit is True


def test_resolve_session_id_generates_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv(SHARED_SESSION_ENV, raising=False)
    monkeypatch.delenv("OPENAI_SESSION_ID", raising=False)

    resolved = resolve_session_id(None, {}, "OPENAI_SESSION_ID")

    assert len(resolved.value) == 32
    assert resolved.explicit is False


def test_session_headers_skips_blank_values() -> None:
    assert session_headers("abc") == {OPENCODE_SESSION_HEADER: "abc"}
    assert session_headers("  ") == {}
    assert session_headers(None) == {}


def test_chat_completions_config_defaults_to_generated_session_id() -> None:
    config = OpenAIChatCompletionsConfig(model="m")

    assert len(config.session_id) == 32
    assert config.session_id_explicit is False
    assert OpenAIChatCompletionsAgent(config)._headers()[OPENCODE_SESSION_HEADER] == (
        config.session_id
    )


def test_chat_completions_from_env_accepts_explicit_session_id() -> None:
    config = OpenAIChatCompletionsConfig.from_env(
        model="m",
        base_url="https://example.test/v1",
        session_id="cli-session",
        env_file=None,
    )

    assert config.session_id == "cli-session"
    assert config.session_id_explicit is True


def test_responses_from_env_accepts_explicit_session_id() -> None:
    config = OpenAIResponsesConfig.from_env(
        model="m",
        session_id="cli-session",
        env_file=None,
    )

    assert config.session_id == "cli-session"
    assert config.session_id_explicit is True


def test_anthropic_from_env_accepts_explicit_session_id() -> None:
    config = AnthropicMessagesConfig.from_env(
        model="m",
        session_id="cli-session",
        env_file=None,
    )

    assert config.session_id == "cli-session"
    assert config.session_id_explicit is True


def test_chat_completions_send_carries_session_header() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["headers"] = dict(headers)
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(
            model="m",
            api_key="k",
            base_url="https://example.test/v1",
            session_id="chat-session",
        ),
        transport=transport,
    )
    agent.respond(messages=({"role": "user", "content": "go"},), tools=())

    assert captured["headers"][OPENCODE_SESSION_HEADER] == "chat-session"


def test_responses_send_carries_session_header() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["headers"] = dict(headers)
        return {"id": "resp_1", "output": []}

    agent = OpenAIResponsesAgent(
        OpenAIResponsesConfig(
            model="m",
            api_key="k",
            base_url="https://example.test/v1",
            session_id="responses-session",
        ),
        transport=transport,
    )
    agent.respond(messages=({"role": "user", "content": "go"},), tools=())

    assert captured["headers"][OPENCODE_SESSION_HEADER] == "responses-session"


def test_anthropic_send_carries_session_header() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["headers"] = dict(headers)
        return {"id": "msg_1", "content": [{"type": "text", "text": "ok"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(
            model="m",
            api_key="k",
            base_url="https://example.test",
            session_id="anthropic-session",
        ),
        transport=transport,
    )
    agent.respond(messages=({"role": "user", "content": "go"},), tools=())

    assert captured["headers"][OPENCODE_SESSION_HEADER] == "anthropic-session"


def test_bind_session_overrides_header_for_later_requests() -> None:
    captured: list[dict[str, str]] = []

    def stream_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Iterable[Mapping[str, Any]]:
        captured.append(dict(headers))
        return [{"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}]

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(
            model="m",
            api_key="k",
            base_url="https://example.test/v1",
        ),
        stream_transport=stream_transport,
    )
    agent.respond_stream(messages=({"role": "user", "content": "go"},), tools=())
    agent.bind_session("bound-session")
    agent.respond_stream(messages=({"role": "user", "content": "go"},), tools=())

    assert len(captured) == 2
    assert captured[0][OPENCODE_SESSION_HEADER] == agent.config.session_id
    assert captured[1][OPENCODE_SESSION_HEADER] == "bound-session"


@pytest.mark.parametrize(
    ("factory", "error"),
    [
        (
            lambda: OpenAIChatCompletionsAgent(OpenAIChatCompletionsConfig(model="m")),
            OpenAICompatibleError,
        ),
        (
            lambda: OpenAIResponsesAgent(OpenAIResponsesConfig(model="m")),
            OpenAIResponsesError,
        ),
        (
            lambda: AnthropicMessagesAgent(AnthropicMessagesConfig(model="m")),
            AnthropicMessagesError,
        ),
    ],
)
def test_bind_session_rejects_blank_session_id(factory, error) -> None:
    agent = factory()

    with pytest.raises(error):
        agent.bind_session("   ")


def test_run_llm_game_binds_game_session_id_to_agent() -> None:
    agent = _BindingAgent()

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        session_id="routing-check",
        config=LlmRunConfig(max_tool_calls_per_turn=1, archive_dir=None),
    )

    assert run.status == "completed"
    assert agent.bound == ["routing-check"]


def test_run_llm_game_keeps_explicit_session_id() -> None:
    agent = _BindingAgent(
        config=OpenAIChatCompletionsConfig(model="m", session_id="cli-session", session_id_explicit=True)
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        session_id="game-session",
        config=LlmRunConfig(max_tool_calls_per_turn=1, archive_dir=None),
    )

    assert run.status == "completed"
    assert agent.bound == []


def test_run_llm_game_sends_game_session_id_in_request_header() -> None:
    captured: list[dict[str, str]] = []

    def stream_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Iterable[Mapping[str, Any]]:
        captured.append(dict(headers))
        return [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "end_turn",
                                        "arguments": '{"hunts": []}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(
            model="m",
            api_key="k",
            base_url="https://example.test/v1",
        ),
        stream_transport=stream_transport,
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        session_id="header-check",
        config=LlmRunConfig(max_tool_calls_per_turn=1, archive_dir=None),
    )

    assert run.status == "completed"
    assert captured
    assert all(headers[OPENCODE_SESSION_HEADER] == "header-check" for headers in captured)


class _BindingAgent:
    """记录 bind_session 调用的最小适配器。"""

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self.bound: list[str] = []

    def bind_session(self, session_id: str) -> None:
        self.bound.append(session_id)

    def respond(
        self,
        *,
        messages: Any,
        tools: Any,
    ) -> LlmAgentResponse:
        return LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"
