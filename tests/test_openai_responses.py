from typing import Any, Mapping

from guild_manager_bench.bench.llm import (
    LlmToolCall,
    OpenAIResponsesAgent,
    OpenAIResponsesConfig,
)


def test_responses_config_reads_specific_env_and_falls_back_to_openai(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "OPENAI_RESPONSES_MODEL",
        "OPENAI_RESPONSES_API_KEY",
        "OPENAI_RESPONSES_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_MODEL=fallback-model\n"
        "OPENAI_API_KEY=fallback-key\n"
        "OPENAI_RESPONSES_BASE_URL=https://responses.test/v1\n"
        "OPENAI_RESPONSES_MODEL=responses-model\n",
        encoding="utf-8",
    )

    config = OpenAIResponsesConfig.from_env(env_file=env_file)

    assert config.model == "responses-model"
    assert config.api_key == "fallback-key"
    assert config.base_url == "https://responses.test/v1"


def test_responses_agent_builds_request_and_parses_function_call() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        captured.update(url=url, headers=dict(headers), body=dict(body), timeout=timeout)
        return {
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "id": "rs_1",
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "先结束回合。"}],
                },
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_abc",
                    "name": "end_turn",
                    "arguments": '{"hunts": []}',
                    "status": "completed",
                },
            ],
            "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        }

    agent = OpenAIResponsesAgent(
        OpenAIResponsesConfig(
            model="gpt-5-mini",
            api_key="secret",
            base_url="https://api.example.test/v1/",
            timeout=12,
            max_output_tokens=1000,
            reasoning_effort="high",
            reasoning_summary="auto",
        ),
        transport=transport,
    )
    response = agent.respond(
        messages=(
            {"role": "system", "content": "基础规则"},
            {"role": "user", "content": "开始"},
            {"role": "system", "content": "终局规则"},
        ),
        tools=(
            {
                "name": "end_turn",
                "description": "结束回合",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        ),
    )

    assert captured["url"] == "https://api.example.test/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "gpt-5-mini"
    assert captured["body"]["instructions"] == "基础规则\n\n终局规则"
    assert captured["body"]["input"] == [{"role": "user", "content": "开始"}]
    assert captured["body"]["tools"][0] == {
        "type": "function",
        "name": "end_turn",
        "description": "结束回合",
        "parameters": {"type": "object", "additionalProperties": False},
    }
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["body"]["max_output_tokens"] == 1000
    assert captured["body"]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="call_abc"),
    )
    assert response.assistant_metadata["reasoning_content"] == "先结束回合。"
    assert response.assistant_metadata["openai_responses_output"][0]["id"] == "rs_1"
    assert response.usage["total_tokens"] == 25


def test_responses_followup_replays_output_items_and_function_output() -> None:
    captured: dict[str, Any] = {}

    def transport(url, headers, body, timeout):
        captured["body"] = body
        return {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "完成"}],
                }
            ]
        }

    previous_output = [
        {"id": "rs_1", "type": "reasoning", "encrypted_content": "encrypted"},
        {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_party",
            "arguments": "{}",
        },
    ]
    agent = OpenAIResponsesAgent(
        OpenAIResponsesConfig(model="test-model"),
        transport=transport,
    )

    response = agent.respond(
        messages=(
            {"role": "user", "content": "开始"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "name": "get_party", "arguments": {}}],
                "openai_responses_output": previous_output,
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_party",
                "content": "成功 get_party",
            },
        ),
        tools=(),
    )

    assert captured["body"]["input"] == [
        {"role": "user", "content": "开始"},
        *previous_output,
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "成功 get_party",
        },
    ]
    assert response.text == "完成"


def test_responses_stream_accumulates_text_tool_call_and_usage() -> None:
    events: list[dict[str, Any]] = []

    def stream_transport(url, headers, body, timeout):
        assert body["stream"] is True
        yield {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_stream",
                "name": "end_turn",
                "arguments": "",
            },
        }
        yield {
            "type": "response.reasoning_summary_text.delta",
            "delta": "准备结束。",
        }
        yield {"type": "response.output_text.delta", "delta": "执行"}
        yield {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"hunts":',
        }
        yield {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": " []}",
        }
        yield {
            "type": "response.completed",
            "response": {
                "id": "resp_stream",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "执行"}],
                    },
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "call_id": "call_stream",
                        "name": "end_turn",
                        "arguments": '{"hunts": []}',
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        }

    agent = OpenAIResponsesAgent(
        OpenAIResponsesConfig(model="test-model"),
        stream_transport=stream_transport,
    )
    response = agent.respond_stream(
        messages=({"role": "user", "content": "开始"},),
        tools=(),
        event_sink=events.append,
    )

    assert response.text == "执行"
    assert response.tool_calls == (
        LlmToolCall("end_turn", {"hunts": []}, call_id="call_stream"),
    )
    assert response.usage["total_tokens"] == 14
    assert response.raw == {
        "stream": True,
        "chunk_count": 6,
        "response_id": "resp_stream",
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }
    assert {event["type"] for event in events} >= {
        "model_reasoning_delta",
        "model_delta",
        "tool_call_delta",
        "model_stream_completed",
    }
