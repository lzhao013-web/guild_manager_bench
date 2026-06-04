from typing import Any, Mapping

from guild_manager_bench.bench.llm import (
    LlmToolCall,
    OpenAIChatCompletionsAgent,
    OpenAIChatCompletionsConfig,
    load_dotenv_values,
)
from guild_manager_bench.bench.llm.runner import LlmAgentResponse, run_llm_turn
from guild_manager_bench.bench.llm.tools import GuildManagerTools


def test_openai_config_reads_openai_compat_dotenv(tmp_path, monkeypatch) -> None:
    for name in (
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_COMPAT_MODEL",
        "OPENAI_COMPAT_API_KEY",
        "OPENAI_COMPAT_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_COMPAT_BASE_URL=https://api.deepseek.com/v1",
                "OPENAI_COMPAT_API_KEY=test-secret",
                "OPENAI_COMPAT_MODEL=deepseek-v4-flash",
            ]
        ),
        encoding="utf-8",
    )

    config = OpenAIChatCompletionsConfig.from_env(env_file=env_file)

    assert config.base_url == "https://api.deepseek.com/v1"
    assert config.api_key == "test-secret"
    assert config.model == "deepseek-v4-flash"


def test_openai_config_explicit_values_override_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_COMPAT_MODEL=dotenv-model\n"
        "OPENAI_COMPAT_API_KEY=dotenv-key\n"
        "OPENAI_COMPAT_BASE_URL=https://dotenv.test/v1\n",
        encoding="utf-8",
    )

    config = OpenAIChatCompletionsConfig.from_env(
        model="explicit-model",
        api_key="explicit-key",
        base_url="https://explicit.test/v1",
        env_file=env_file,
    )

    assert config.model == "explicit-model"
    assert config.api_key == "explicit-key"
    assert config.base_url == "https://explicit.test/v1"


def test_openai_config_accepts_numeric_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_COMPAT_MODEL=dotenv-model\n", encoding="utf-8")

    config = OpenAIChatCompletionsConfig.from_env(
        model="explicit-model",
        timeout=60.0,
        env_file=env_file,
    )

    assert config.model == "explicit-model"
    assert config.timeout == 60.0


def test_openai_config_environment_values_override_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.test/v1")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_COMPAT_MODEL=dotenv-model\n"
        "OPENAI_COMPAT_API_KEY=dotenv-key\n"
        "OPENAI_COMPAT_BASE_URL=https://dotenv.test/v1\n",
        encoding="utf-8",
    )

    config = OpenAIChatCompletionsConfig.from_env(env_file=env_file)

    assert config.model == "env-model"
    assert config.api_key == "env-key"
    assert config.base_url == "https://env.test/v1"


def test_dotenv_parser_handles_quotes_export_and_comments(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# ignored",
                "export OPENAI_COMPAT_MODEL=\"quoted-model\" # ignored",
                "OPENAI_COMPAT_API_KEY='quoted-secret'",
                "OPENAI_COMPAT_BASE_URL=https://api.example.test/v1 # ignored",
                "JSON={\"enabled\":true}",
            ]
        ),
        encoding="utf-8",
    )

    values = load_dotenv_values(env_file)

    assert values == {
        "OPENAI_COMPAT_MODEL": "quoted-model",
        "OPENAI_COMPAT_API_KEY": "quoted-secret",
        "OPENAI_COMPAT_BASE_URL": "https://api.example.test/v1",
        "JSON": '{"enabled":true}',
    }


def test_openai_agent_sends_chat_completion_request_and_parses_tool_call() -> None:
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
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "end_turn",
                                    "arguments": '{"hunts": []}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(
            model="test-model",
            api_key="test-key",
            base_url="https://example.test/v1/",
            timeout=12,
            temperature=0,
        ),
        transport=transport,
    )

    response = agent.respond(
        messages=(
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

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["User-Agent"] == "guild-manager-bench/0.1"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["tools"][0]["type"] == "function"
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="call_abc"),
    )
    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }


def test_openai_message_conversion_preserves_tool_call_ids() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured["body"] = dict(body)
        return {"choices": [{"message": {"content": "done"}}]}

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(model="test-model"),
        transport=transport,
    )

    agent.respond(
        messages=(
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "need observation",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "get_party",
                        "arguments": {},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_party",
                "content": '{"ok": true}',
            },
        ),
        tools=(),
    )

    messages = captured["body"]["messages"]
    assert messages[1]["reasoning_content"] == "need observation"
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert messages[2]["tool_call_id"] == "call_1"


def test_openai_response_preserves_reasoning_content_for_followup() -> None:
    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "I should inspect the state first.",
                        "tool_calls": [
                            {
                                "id": "call_reasoning",
                                "type": "function",
                                "function": {
                                    "name": "get_party",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        }

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(model="test-model"),
        transport=transport,
    )

    response = agent.respond(
        messages=({"role": "user", "content": "start"},),
        tools=(),
    )

    assert response.assistant_metadata == {
        "reasoning_content": "I should inspect the state first."
    }


def test_openai_streaming_response_emits_deltas_and_accumulates_tool_call() -> None:
    events: list[dict[str, Any]] = []

    def stream_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ):
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        yield {"choices": [{"delta": {"reasoning_content": "先看状态。"}}]}
        yield {"choices": [{"delta": {"content": "计划"}}]}
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_stream",
                                "type": "function",
                                "function": {
                                    "name": "end_turn",
                                    "arguments": '{"hunts"',
                                },
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": ": []}",
                                },
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
            },
        }

    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(model="test-model"),
        stream_transport=stream_transport,
    )

    response = agent.respond_stream(
        messages=({"role": "user", "content": "start"},),
        tools=(),
        event_sink=events.append,
    )

    assert response.text == "计划"
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="call_stream"),
    )
    assert response.assistant_metadata == {"reasoning_content": "先看状态。"}
    assert response.usage == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }
    assert response.raw == {
        "stream": True,
        "chunk_count": 5,
        "finish_reason": "tool_calls",
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
        },
    }
    assert events[0] == {"type": "model_reasoning_delta", "text": "先看状态。"}
    assert events[1] == {"type": "model_delta", "text": "计划"}
    assert events[-1]["type"] == "model_stream_completed"
    assert events[-1]["usage"]["total_tokens"] == 28
    assert events[-1]["chunk_count"] == 5


def test_runner_messages_are_accepted_by_openai_agent_shape() -> None:
    class Agent:
        def __init__(self) -> None:
            self.calls = 0

        def respond(self, *, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LlmAgentResponse(
                    tool_calls=(LlmToolCall("get_party", {}),),
                    assistant_metadata={"reasoning_content": "need tools"},
                )
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "call_1"
            assert messages[-2]["reasoning_content"] == "need tools"
            return LlmAgentResponse(
                tool_calls=(LlmToolCall("end_turn", {"hunts": []}, call_id="call_2"),)
            )

    tools = GuildManagerTools.from_data_dir()
    session_id = tools.start_session("openai-shape")["session_id"]
    trace = run_llm_turn(Agent(), tools, session_id)

    assert trace.status == "completed"
    assert trace.messages[1]["tool_calls"][0]["id"] == "call_1"
    assert trace.messages[-2]["tool_calls"][0]["id"] == "call_2"
