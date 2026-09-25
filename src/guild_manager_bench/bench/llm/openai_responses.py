from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.openai_compat import (
    EnvFile,
    EventSink,
    OpenAICompatibleError,
    StreamTransport,
    Transport,
    _emit,
    _first_config_value,
    _optional_str,
    _parse_arguments,
    _urllib_stream_transport,
    _urllib_transport,
    load_dotenv_values,
)
from guild_manager_bench.bench.llm.runner import LlmAgentResponse, LlmToolCall


class OpenAIResponsesError(RuntimeError):
    """OpenAI Responses API 调用失败。"""


@dataclass(frozen=True, slots=True)
class OpenAIResponsesConfig:
    """OpenAI Responses API 配置。"""

    model: str
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 180.0
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    tool_choice: str | Mapping[str, Any] | None = "auto"
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        env_file: EnvFile | None = ".env",
        timeout: float | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        max_tokens: int | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> OpenAIResponsesConfig:
        """从 OPENAI_RESPONSES_*、OPENAI_* 或 dotenv 文件创建配置。"""

        if max_output_tokens is not None and max_tokens is not None:
            raise OpenAIResponsesError(
                "max_output_tokens and max_tokens cannot both be set"
            )
        dotenv_values = load_dotenv_values(env_file) if env_file is not None else {}
        resolved_model = _first_config_value(
            model,
            dotenv_values,
            "OPENAI_RESPONSES_MODEL",
            "OPENAI_MODEL",
        )
        if not resolved_model:
            raise OpenAIResponsesError(
                "model is required or OPENAI_RESPONSES_MODEL/OPENAI_MODEL must be set"
            )
        resolved_timeout = _first_config_value(
            timeout,
            dotenv_values,
            "OPENAI_RESPONSES_TIMEOUT",
            "OPENAI_TIMEOUT",
        )
        output_token_value = max_output_tokens
        if output_token_value is None:
            output_token_value = max_tokens
        resolved_max_output_tokens = _first_config_value(
            output_token_value,
            dotenv_values,
            "OPENAI_RESPONSES_MAX_OUTPUT_TOKENS",
            "OPENAI_MAX_OUTPUT_TOKENS",
        )
        return cls(
            model=resolved_model,
            api_key=_first_config_value(
                api_key,
                dotenv_values,
                "OPENAI_RESPONSES_API_KEY",
                "OPENAI_API_KEY",
            ),
            base_url=_first_config_value(
                base_url,
                dotenv_values,
                "OPENAI_RESPONSES_BASE_URL",
                "OPENAI_BASE_URL",
                default="https://api.openai.com/v1",
            ),
            timeout=(
                float(resolved_timeout)
                if resolved_timeout is not None
                else 180.0
            ),
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=(
                int(resolved_max_output_tokens)
                if resolved_max_output_tokens is not None
                else None
            ),
            tool_choice=tool_choice,
            reasoning_effort=_first_config_value(
                reasoning_effort,
                dotenv_values,
                "OPENAI_RESPONSES_REASONING_EFFORT",
                "OPENAI_REASONING_EFFORT",
            ),
            reasoning_summary=_first_config_value(
                reasoning_summary,
                dotenv_values,
                "OPENAI_RESPONSES_REASONING_SUMMARY",
            ),
            extra_body={} if extra_body is None else dict(extra_body),
        )


class OpenAIResponsesAgent:
    """OpenAI Responses API 模型适配器。"""

    def __init__(
        self,
        config: OpenAIResponsesConfig,
        *,
        transport: Transport | None = None,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _responses_urllib_transport
        self._stream_transport = stream_transport or _responses_urllib_stream_transport

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        env_file: EnvFile | None = ".env",
        timeout: float | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        max_tokens: int | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> OpenAIResponsesAgent:
        return cls(
            OpenAIResponsesConfig.from_env(
                model=model,
                api_key=api_key,
                base_url=base_url,
                env_file=env_file,
                timeout=timeout,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
                reasoning_effort=reasoning_effort,
                reasoning_summary=reasoning_summary,
                extra_body=extra_body,
            )
        )

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        response = self._transport(
            self._responses_url(),
            self._headers(),
            self._request_body(messages, tools),
            self.config.timeout,
        )
        return _parse_responses_response(response)

    def respond_stream(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        event_sink: EventSink | None = None,
    ) -> LlmAgentResponse:
        body = self._request_body(messages, tools)
        body["stream"] = True
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        output_items: dict[int, dict[str, Any]] = {}
        function_parts: dict[int, dict[str, Any]] = {}
        completed_response: Mapping[str, Any] | None = None
        chunk_count = 0

        for event in self._stream_transport(
            self._responses_url(),
            self._headers(),
            body,
            self.config.timeout,
        ):
            chunk_count += 1
            event_type = event.get("type")
            if event_type == "error":
                error = event.get("error")
                raise OpenAIResponsesError(_error_message(error))
            if event_type in {"response.failed", "response.incomplete"}:
                response = event.get("response")
                if event_type == "response.failed":
                    raise OpenAIResponsesError(_response_failure_message(response))
                if isinstance(response, Mapping):
                    completed_response = response
            elif event_type in {"response.output_text.delta", "response.refusal.delta"}:
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
                    if delta:
                        _emit(event_sink, "model_delta", text=delta)
            elif event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = event.get("delta")
                if isinstance(delta, str):
                    reasoning_parts.append(delta)
                    if delta:
                        _emit(event_sink, "model_reasoning_delta", text=delta)
            elif event_type == "response.output_item.added":
                _capture_output_item(event, output_items, function_parts)
            elif event_type == "response.function_call_arguments.delta":
                index = _event_output_index(event)
                part = function_parts.setdefault(
                    index,
                    {"type": "function_call", "call_id": None, "name": "", "arguments": ""},
                )
                delta = event.get("delta")
                if isinstance(delta, str):
                    part["arguments"] = str(part.get("arguments") or "") + delta
                    _emit(
                        event_sink,
                        "tool_call_delta",
                        index=index,
                        call_id=part.get("call_id"),
                        name=part.get("name", ""),
                        arguments_delta=delta,
                    )
            elif event_type == "response.output_item.done":
                _capture_output_item(event, output_items, function_parts, replace=True)
            elif event_type == "response.completed":
                response = event.get("response")
                if isinstance(response, Mapping):
                    completed_response = response

        if completed_response is not None:
            parsed = _parse_responses_response(completed_response)
        else:
            fallback_output = [
                output_items[index]
                for index in sorted(output_items)
            ]
            known_indexes = set(output_items)
            fallback_output.extend(
                function_parts[index]
                for index in sorted(function_parts)
                if index not in known_indexes
            )
            parsed = _parse_responses_response(
                {
                    "output": fallback_output,
                    "output_text": "".join(text_parts),
                }
            )
            if reasoning_parts and "reasoning_content" not in parsed.assistant_metadata:
                metadata = dict(parsed.assistant_metadata)
                metadata["reasoning_content"] = "".join(reasoning_parts)
                parsed = LlmAgentResponse(
                    text=parsed.text,
                    tool_calls=parsed.tool_calls,
                    assistant_metadata=metadata,
                    usage=parsed.usage,
                    raw=parsed.raw,
                )

        raw: dict[str, Any] = {
            "stream": True,
            "chunk_count": chunk_count,
        }
        if completed_response is not None:
            response_id = completed_response.get("id")
            status = completed_response.get("status")
            if isinstance(response_id, str):
                raw["response_id"] = response_id
            if isinstance(status, str):
                raw["status"] = status
        if parsed.usage:
            raw["usage"] = dict(parsed.usage)
        _emit(
            event_sink,
            "model_stream_completed",
            text=parsed.text,
            tool_calls=[call.to_dict() for call in parsed.tool_calls],
            usage=dict(parsed.usage),
            chunk_count=chunk_count,
            status=raw.get("status"),
        )
        return LlmAgentResponse(
            text=parsed.text,
            tool_calls=parsed.tool_calls,
            assistant_metadata=parsed.assistant_metadata,
            usage=parsed.usage,
            raw=raw,
        )

    def _request_body(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        instructions, remaining = _extract_instructions(messages)
        openai_tools = [_to_responses_tool(tool) for tool in tools]
        body: dict[str, Any] = {
            "model": self.config.model,
            "input": _to_responses_input(remaining),
        }
        if instructions:
            body["instructions"] = instructions
        if openai_tools:
            body["tools"] = openai_tools
        if self.config.tool_choice is not None and (
            openai_tools or self.config.tool_choice != "auto"
        ):
            body["tool_choice"] = self.config.tool_choice
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p
        if self.config.max_output_tokens is not None:
            body["max_output_tokens"] = self.config.max_output_tokens
        reasoning: dict[str, Any] = {}
        if self.config.reasoning_effort is not None:
            reasoning["effort"] = self.config.reasoning_effort
        if self.config.reasoning_summary is not None:
            reasoning["summary"] = self.config.reasoning_summary
        if reasoning:
            body["reasoning"] = reasoning
        body.update(dict(self.config.extra_body))
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "guild-manager-bench/1.0",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _responses_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/responses"


def _extract_instructions(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    remaining: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                instructions.append(content)
        else:
            remaining.append(dict(message))
    return "\n\n".join(instructions), remaining


def _to_responses_input(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            previous_output = message.get("openai_responses_output")
            if isinstance(previous_output, Sequence) and not isinstance(previous_output, str):
                preserved = [dict(item) for item in previous_output if isinstance(item, Mapping)]
                if preserved:
                    items.extend(preserved)
                    continue
            content = message.get("content")
            if isinstance(content, str) and content:
                items.append({"role": "assistant", "content": content})
            for call in message.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "arguments": json.dumps(
                            call.get("arguments", {}),
                            ensure_ascii=False,
                        ),
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
        elif role in {"user", "developer", "system"}:
            items.append(
                {
                    "role": role,
                    "content": str(message.get("content") or ""),
                }
            )
    return items


def _to_responses_tool(schema: Mapping[str, Any]) -> dict[str, Any]:
    tool = {
        "type": "function",
        "name": schema["name"],
        "description": schema.get("description", ""),
        "parameters": schema.get("parameters", {"type": "object"}),
    }
    if "strict" in schema:
        tool["strict"] = bool(schema["strict"])
    return tool


def _parse_responses_response(response: Mapping[str, Any]) -> LlmAgentResponse:
    output = response.get("output")
    if output is None:
        output = []
    if not isinstance(output, list):
        raise OpenAIResponsesError("responses response output must be an array")

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[LlmToolCall] = []
    preserved_output: list[dict[str, Any]] = []
    for raw_item in output:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        preserved_output.append(item)
        item_type = item.get("type")
        if item_type == "message":
            for part in _content_parts(item.get("content")):
                part_type = part.get("type")
                value = part.get("text")
                if part_type in {"output_text", "text"} and isinstance(value, str):
                    text_parts.append(value)
                elif part_type == "refusal" and isinstance(part.get("refusal"), str):
                    text_parts.append(part["refusal"])
        elif item_type == "function_call":
            tool_calls.append(
                LlmToolCall(
                    name=str(item.get("name") or ""),
                    arguments=_parse_arguments(item.get("arguments")),
                    call_id=_optional_str(item.get("call_id")) or _optional_str(item.get("id")),
                )
            )
        elif item_type == "reasoning":
            for part in _content_parts(item.get("summary")):
                value = part.get("text")
                if isinstance(value, str):
                    reasoning_parts.append(value)

    if not text_parts and isinstance(response.get("output_text"), str):
        text_parts.append(response["output_text"])
    metadata: dict[str, Any] = {}
    if preserved_output:
        metadata["openai_responses_output"] = preserved_output
    if reasoning_parts:
        metadata["reasoning_content"] = "".join(reasoning_parts)
    usage = response.get("usage")
    return LlmAgentResponse(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        assistant_metadata=metadata,
        usage=dict(usage) if isinstance(usage, Mapping) else {},
        raw=dict(response),
    )


def _content_parts(value: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _event_output_index(event: Mapping[str, Any]) -> int:
    index = event.get("output_index")
    return index if isinstance(index, int) and not isinstance(index, bool) else 0


def _capture_output_item(
    event: Mapping[str, Any],
    output_items: dict[int, dict[str, Any]],
    function_parts: dict[int, dict[str, Any]],
    *,
    replace: bool = False,
) -> None:
    item = event.get("item")
    if not isinstance(item, Mapping):
        return
    index = _event_output_index(event)
    data = dict(item)
    if replace or index not in output_items:
        output_items[index] = data
    if data.get("type") == "function_call":
        if replace or index not in function_parts:
            function_parts[index] = data


def _responses_urllib_transport(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    try:
        return _urllib_transport(url, headers, body, timeout)
    except OpenAICompatibleError as exc:
        raise OpenAIResponsesError(str(exc)) from exc


def _responses_urllib_stream_transport(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
):
    try:
        yield from _urllib_stream_transport(url, headers, body, timeout)
    except OpenAICompatibleError as exc:
        raise OpenAIResponsesError(str(exc)) from exc


def _error_message(error: Any) -> str:
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return str(error or "Responses API stream error")


def _response_failure_message(response: Any) -> str:
    if isinstance(response, Mapping):
        error = response.get("error")
        if error:
            return _error_message(error)
        details = response.get("incomplete_details")
        if details:
            return str(details)
    return "Responses API request failed"
