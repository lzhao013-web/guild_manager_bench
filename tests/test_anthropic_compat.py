from typing import Any, Mapping

from guild_manager_bench.bench.llm import (
    LlmToolCall,
    AnthropicMessagesAgent,
    AnthropicMessagesConfig,
    AnthropicMessagesError,
    load_dotenv_values,
)
from guild_manager_bench.bench.llm.runner import LlmAgentResponse, run_llm_turn
from guild_manager_bench.bench.llm.tools import GuildManagerTools


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------


def test_anthropic_config_reads_dotenv(tmp_path, monkeypatch) -> None:
    for name in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ANTHROPIC_BASE_URL=https://api.anthropic.com",
                "ANTHROPIC_API_KEY=sk-ant-test",
                "ANTHROPIC_MODEL=claude-sonnet-4-6",
            ]
        ),
        encoding="utf-8",
    )

    config = AnthropicMessagesConfig.from_env(env_file=env_file)

    assert config.base_url == "https://api.anthropic.com"
    assert config.api_key == "sk-ant-test"
    assert config.model == "claude-sonnet-4-6"


def test_anthropic_config_explicit_overrides_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_MODEL=dotenv-model\n"
        "ANTHROPIC_API_KEY=dotenv-key\n"
        "ANTHROPIC_BASE_URL=https://dotenv.test\n",
        encoding="utf-8",
    )

    config = AnthropicMessagesConfig.from_env(
        model="explicit-model",
        api_key="explicit-key",
        base_url="https://explicit.test",
        env_file=env_file,
    )

    assert config.model == "explicit-model"
    assert config.api_key == "explicit-key"
    assert config.base_url == "https://explicit.test"


def test_anthropic_config_env_overrides_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.test")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_MODEL=dotenv-model\n"
        "ANTHROPIC_API_KEY=dotenv-key\n"
        "ANTHROPIC_BASE_URL=https://dotenv.test\n",
        encoding="utf-8",
    )

    config = AnthropicMessagesConfig.from_env(env_file=env_file)

    assert config.model == "env-model"
    assert config.api_key == "env-key"
    assert config.base_url == "https://env.test"


def test_anthropic_config_missing_model_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    try:
        AnthropicMessagesConfig.from_env(env_file=env_file)
        assert False, "expected AnthropicMessagesError"
    except AnthropicMessagesError:
        pass


def test_anthropic_config_max_tokens_defaults_to_4096(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = AnthropicMessagesConfig.from_env(env_file=env_file)

    assert config.max_tokens == 4096


def test_anthropic_config_reads_thinking_and_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_THINKING", raising=False)
    monkeypatch.delenv("ANTHROPIC_EFFORT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ANTHROPIC_MODEL=claude-sonnet-4-6",
                "ANTHROPIC_THINKING=true",
                "ANTHROPIC_EFFORT=medium",
            ]
        ),
        encoding="utf-8",
    )

    config = AnthropicMessagesConfig.from_env(env_file=env_file)

    assert config.thinking is True
    assert config.effort == "medium"


# ---------------------------------------------------------------------------
# 非流式请求测试
# ---------------------------------------------------------------------------


def test_anthropic_agent_sends_messages_request_and_parses_tool_use() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["body"] = dict(body)
        captured["timeout"] = timeout
        return {
            "content": [
                {"type": "text", "text": "我先看看队伍状态。"},
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "end_turn",
                    "input": {"hunts": []},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        }

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(
            model="claude-sonnet-4-6",
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            timeout=30,
            temperature=0,
        ),
        transport=transport,
    )

    response = agent.respond(
        messages=(
            {"role": "system", "content": "你是公会管理助手。"},
            {"role": "user", "content": "play"},
        ),
        tools=(
            {
                "name": "end_turn",
                "description": "结束回合",
                "parameters": {"type": "object"},
            },
        ),
    )

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["body"]["model"] == "claude-sonnet-4-6"
    assert captured["body"]["max_tokens"] == 4096
    assert captured["body"]["system"] == "你是公会管理助手。"
    assert captured["body"]["messages"] == [{"role": "user", "content": "play"}]
    assert captured["body"]["tools"][0] == {
        "name": "end_turn",
        "description": "结束回合",
        "input_schema": {"type": "object"},
    }
    assert captured["body"]["tool_choice"] == {"type": "auto"}
    assert captured["body"]["temperature"] == 0

    assert response.text == "我先看看队伍状态。"
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="toolu_abc"),
    )
    assert response.usage == {"input_tokens": 100, "output_tokens": 50}


def test_anthropic_agent_sends_thinking_and_effort_controls() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"content": [{"type": "text", "text": "ok"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(
            model="claude-sonnet-4-6",
            thinking=True,
            effort="high",
        ),
        transport=transport,
    )

    agent.respond(
        messages=({"role": "user", "content": "play"},),
        tools=(),
    )

    assert captured["body"]["thinking"] == {"type": "adaptive"}
    assert captured["body"]["output_config"] == {"effort": "high"}


def test_anthropic_agent_can_explicitly_disable_thinking() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"content": [{"type": "text", "text": "ok"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(
            model="claude-sonnet-4-6",
            thinking=False,
        ),
        transport=transport,
    )

    agent.respond(
        messages=({"role": "user", "content": "play"},),
        tools=(),
    )

    assert captured["body"]["thinking"] == {"type": "disabled"}


# ---------------------------------------------------------------------------
# 消息转换测试
# ---------------------------------------------------------------------------


def test_anthropic_message_conversion_merges_consecutive_tool_results() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"content": [{"type": "text", "text": "done"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        transport=transport,
    )

    agent.respond(
        messages=(
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "name": "get_party", "arguments": {}},
                    {"id": "call_2", "name": "get_monsters", "arguments": {}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_party",
                "content": "队伍信息",
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "name": "get_monsters",
                "content": "怪物信息",
            },
        ),
        tools=(),
    )

    messages = captured["body"]["messages"]
    # assistant -> tool_result(tool_1, tool_2) merged into one user message
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "start"
    assert messages[1]["role"] == "assistant"
    # 空字符串 content 不生成 text block，只有 tool_use blocks
    assert messages[1]["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_party", "input": {}},
        {"type": "tool_use", "id": "call_2", "name": "get_monsters", "input": {}},
    ]
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "队伍信息"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "怪物信息"},
    ]


def test_anthropic_message_conversion_extracts_system_prompt() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"content": [{"type": "text", "text": "ok"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        transport=transport,
    )

    agent.respond(
        messages=(
            {"role": "system", "content": "规则一"},
            {"role": "system", "content": "规则二"},
            {"role": "user", "content": "go"},
        ),
        tools=(),
    )

    assert captured["body"]["system"] == "规则一\n\n规则二"
    assert captured["body"]["messages"] == [{"role": "user", "content": "go"}]


def test_anthropic_assistant_tool_calls_become_tool_use_blocks() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"content": [{"type": "text", "text": "done"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        transport=transport,
    )

    agent.respond(
        messages=(
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": "让我看看",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "get_party",
                        "arguments": {"session_id": "abc"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_party",
                "content": "队伍详情",
            },
        ),
        tools=(),
    )

    messages = captured["body"]["messages"]
    assert messages[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "让我看看"},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "get_party",
                "input": {"session_id": "abc"},
            },
        ],
    }


def test_anthropic_thinking_blocks_are_preserved_for_followup_tool_results() -> None:
    captured_bodies: list[dict[str, Any]] = []
    response_blocks = [
        {
            "type": "thinking",
            "thinking": "先检查队伍。",
            "signature": "signed-thinking",
        },
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "get_party",
            "input": {},
        },
    ]

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured_bodies.append(dict(body))
        if len(captured_bodies) == 1:
            return {"content": response_blocks}
        return {"content": [{"type": "text", "text": "done"}]}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        transport=transport,
    )

    response = agent.respond(
        messages=({"role": "user", "content": "start"},),
        tools=(),
    )
    agent.respond(
        messages=(
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": response.text,
                "tool_calls": [call.to_dict() for call in response.tool_calls],
                **response.assistant_metadata,
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_1",
                "name": "get_party",
                "content": "队伍详情",
            },
        ),
        tools=(),
    )

    assert response.assistant_metadata == {
        "anthropic_content_blocks": response_blocks,
        "reasoning_content": "先检查队伍。",
    }
    assert captured_bodies[1]["messages"][1]["content"] == response_blocks


# ---------------------------------------------------------------------------
# 流式响应测试
# ---------------------------------------------------------------------------


def test_anthropic_streaming_response_emits_deltas_and_accumulates_tool_use() -> None:
    events: list[dict[str, Any]] = []

    def stream_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ):
        assert body["stream"] is True
        yield {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 100}},
        }
        yield {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "计划"},
        }
        yield {"type": "content_block_stop", "index": 0}
        yield {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "end_turn",
            },
        }
        yield {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"hunts"'},
        }
        yield {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": ": []}"},
        }
        yield {"type": "content_block_stop", "index": 1}
        yield {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 50},
        }
        yield {"type": "message_stop"}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        stream_transport=stream_transport,
    )

    response = agent.respond_stream(
        messages=({"role": "user", "content": "start"},),
        tools=(),
        event_sink=events.append,
    )

    assert response.text == "计划"
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="toolu_abc"),
    )
    assert response.usage == {"input_tokens": 100, "output_tokens": 50}
    assert response.raw == {
        "stream": True,
        "chunk_count": 10,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    assert events[0] == {"type": "model_delta", "text": "计划"}
    assert events[-1]["type"] == "model_stream_completed"
    assert events[-1]["usage"]["output_tokens"] == 50


def test_anthropic_streaming_response_accumulates_thinking_and_signature() -> None:
    events: list[dict[str, Any]] = []

    def stream_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ):
        yield {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "检查资源。"},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "signed"},
        }
        yield {"type": "content_block_stop", "index": 0}
        yield {"type": "message_stop"}

    agent = AnthropicMessagesAgent(
        AnthropicMessagesConfig(model="claude-sonnet-4-6"),
        stream_transport=stream_transport,
    )

    response = agent.respond_stream(
        messages=({"role": "user", "content": "start"},),
        tools=(),
        event_sink=events.append,
    )

    assert response.assistant_metadata == {
        "anthropic_content_blocks": [
            {
                "type": "thinking",
                "thinking": "检查资源。",
                "signature": "signed",
            }
        ],
        "reasoning_content": "检查资源。",
    }
    assert events[0] == {"type": "model_reasoning_delta", "text": "检查资源。"}


# ---------------------------------------------------------------------------
# Runner 集成形状测试
# ---------------------------------------------------------------------------


def test_runner_messages_are_accepted_by_anthropic_agent_shape() -> None:
    class Agent:
        def __init__(self) -> None:
            self.calls = 0

        def respond(self, *, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LlmAgentResponse(
                    tool_calls=(LlmToolCall("get_party", {}),),
                    assistant_metadata={
                        "anthropic_content_blocks": [
                            {
                                "type": "thinking",
                                "thinking": "inspect",
                                "signature": "signed",
                            },
                            {
                                "type": "tool_use",
                                "id": "call_1",
                                "name": "get_party",
                                "input": {},
                            },
                        ]
                    },
                )
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "call_1"
            assert messages[-2]["anthropic_content_blocks"][0]["signature"] == "signed"
            return LlmAgentResponse(
                tool_calls=(LlmToolCall("end_turn", {"hunts": []}, call_id="call_2"),)
            )

    tools = GuildManagerTools.from_data_dir()
    session_id = tools.start_session("anthropic-shape")["session_id"]
    trace = run_llm_turn(Agent(), tools, session_id)

    assert trace.status == "completed"
    assert trace.messages[1]["tool_calls"][0]["id"] == "call_1"
    assert trace.messages[-2]["tool_calls"][0]["id"] == "call_2"
