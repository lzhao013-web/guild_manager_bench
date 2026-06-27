from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from guild_manager_bench.bench.llm.runner import LlmAgentResponse, LlmToolCall


class OpenAICompatibleError(RuntimeError):
    """OpenAI-compatible API 调用失败。"""


_RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_API_RETRIES = 2


def _urlopen_with_retry(
    request: urllib.request.Request,
    timeout: float,
    max_retries: int = _MAX_API_RETRIES,
):
    """打开 URL，对瞬时服务器错误自动重试 (最多 max_retries 次)。"""
    for attempt in range(max_retries + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_HTTP_CODES or attempt >= max_retries:
                raise
            time.sleep(2 ** attempt)
        except urllib.error.URLError as exc:
            if attempt >= max_retries:
                raise
            time.sleep(2 ** attempt)


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]
StreamTransport = Callable[
    [str, Mapping[str, str], Mapping[str, Any], float],
    Iterable[Mapping[str, Any]],
]
EventSink = Callable[[dict[str, Any]], None]
EnvFile = str | os.PathLike[str]


def load_dotenv_values(path: EnvFile = ".env") -> dict[str, str]:
    """解析简单的 dotenv 文件，不修改进程环境变量。"""

    env_path = Path(path)
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _parse_dotenv_value(raw_value.strip())
    return values


def _parse_dotenv_value(raw_value: str) -> str:
    if not raw_value:
        return ""
    if raw_value[0] in ("'", '"'):
        return _parse_quoted_dotenv_value(raw_value, raw_value[0])
    return _strip_unquoted_dotenv_comment(raw_value).strip()


def _parse_quoted_dotenv_value(raw_value: str, quote: str) -> str:
    chars: list[str] = []
    escaped = False
    for char in raw_value[1:]:
        if quote == '"' and escaped:
            chars.append(_decode_dotenv_escape(char))
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char == quote:
            return "".join(chars)
        chars.append(char)
    if escaped:
        chars.append("\\")
    return "".join(chars)


def _decode_dotenv_escape(char: str) -> str:
    return {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "\\": "\\",
        '"': '"',
    }.get(char, char)


def _strip_unquoted_dotenv_comment(raw_value: str) -> str:
    for index, char in enumerate(raw_value):
        if char == "#" and (index == 0 or raw_value[index - 1].isspace()):
            return raw_value[:index]
    return raw_value


def _first_config_value(
    explicit_value: str | int | float | None,
    dotenv_values: Mapping[str, str],
    *names: str,
    default: str | None = None,
) -> str | None:
    if explicit_value is not None:
        if isinstance(explicit_value, str):
            return explicit_value.strip() or None
        if isinstance(explicit_value, int | float) and not isinstance(explicit_value, bool):
            return str(explicit_value)
        return None
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    for name in names:
        value = dotenv_values.get(name)
        if value and value.strip():
            return value.strip()
    return default


@dataclass(frozen=True, slots=True)
class OpenAIChatCompletionsConfig:
    """OpenAI-compatible Chat Completions 配置。"""

    model: str
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 180.0
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    tool_choice: str | Mapping[str, Any] | None = "auto"
    reasoning_effort: str | None = None
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
        max_tokens: int | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        reasoning_effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> OpenAIChatCompletionsConfig:
        """从显式参数、进程环境变量或 dotenv 文件创建配置。"""

        dotenv_values = load_dotenv_values(env_file) if env_file is not None else {}
        resolved_model = _first_config_value(
            model,
            dotenv_values,
            "OPENAI_MODEL",
            "OPENAI_COMPAT_MODEL",
        )
        if not resolved_model:
            raise OpenAICompatibleError(
                "model is required or OPENAI_MODEL/OPENAI_COMPAT_MODEL must be set"
            )
        resolved_timeout = _first_config_value(
            timeout,
            dotenv_values,
            "OPENAI_TIMEOUT",
            "OPENAI_COMPAT_TIMEOUT",
        )
        if resolved_timeout is not None:
            resolved_timeout = float(resolved_timeout)
        else:
            resolved_timeout = 180.0
        return cls(
            model=resolved_model,
            api_key=_first_config_value(
                api_key,
                dotenv_values,
                "OPENAI_API_KEY",
                "OPENAI_COMPAT_API_KEY",
            ),
            base_url=_first_config_value(
                base_url,
                dotenv_values,
                "OPENAI_BASE_URL",
                "OPENAI_COMPAT_BASE_URL",
                default="https://api.openai.com/v1",
            ),
            timeout=resolved_timeout,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            reasoning_effort=_first_config_value(
                reasoning_effort,
                dotenv_values,
                "OPENAI_REASONING_EFFORT",
            ),
            extra_body={} if extra_body is None else dict(extra_body),
        )


class OpenAIChatCompletionsAgent:
    """OpenAI-compatible Chat Completions 模型适配器。"""

    def __init__(
        self,
        config: OpenAIChatCompletionsConfig,
        *,
        transport: Transport | None = None,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _urllib_transport
        self._stream_transport = stream_transport or _urllib_stream_transport

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
        max_tokens: int | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        reasoning_effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> OpenAIChatCompletionsAgent:
        """从 OPENAI_* 或 OPENAI_COMPAT_* 配置创建适配器。"""

        return cls(
            OpenAIChatCompletionsConfig.from_env(
                model=model,
                api_key=api_key,
                base_url=base_url,
                env_file=env_file,
                timeout=timeout,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
            )
        )

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        """调用 Chat Completions 接口并解析 tool calls。"""

        body = self._request_body(messages, tools)
        response = self._transport(
            self._chat_completions_url(),
            self._headers(),
            body,
            self.config.timeout,
        )
        return _parse_chat_completion_response(response)

    def respond_stream(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        event_sink: EventSink | None = None,
    ) -> LlmAgentResponse:
        """流式调用 Chat Completions 接口并解析最终 tool calls。"""

        body = self._request_body(messages, tools)
        body["stream"] = True
        if "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        content_parts: list[str] = []
        reasoning_content_parts: list[str] = []
        has_reasoning_content = False
        tool_call_parts: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        usage: dict[str, Any] = {}
        finish_reason: str | None = None

        for chunk in self._stream_transport(
            self._chat_completions_url(),
            self._headers(),
            body,
            self.config.timeout,
        ):
            chunk_count += 1
            chunk_usage = _usage_from_response(chunk)
            if chunk_usage:
                usage = chunk_usage
            chunk_finish_reason = _finish_reason_from_chunk(chunk)
            if chunk_finish_reason is not None:
                finish_reason = chunk_finish_reason
            delta = _first_delta(chunk)
            if not delta:
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                _emit(event_sink, "model_delta", text=content)
            reasoning_content = delta.get("reasoning_content")
            if isinstance(reasoning_content, str):
                has_reasoning_content = True
                reasoning_content_parts.append(reasoning_content)
                if reasoning_content:
                    _emit(event_sink, "model_reasoning_delta", text=reasoning_content)

            for item in delta.get("tool_calls") or ():
                if not isinstance(item, Mapping):
                    continue
                index = _tool_call_index(item)
                part = tool_call_parts.setdefault(
                    index,
                    {"id": None, "name": "", "arguments": ""},
                )
                if isinstance(item.get("id"), str):
                    part["id"] = item["id"]
                function = item.get("function", {})
                if isinstance(function, Mapping):
                    if isinstance(function.get("name"), str):
                        part["name"] += function["name"]
                    if isinstance(function.get("arguments"), str):
                        part["arguments"] += function["arguments"]
                _emit(
                    event_sink,
                    "tool_call_delta",
                    index=index,
                    call_id=part.get("id"),
                    name=part.get("name", ""),
                    arguments_delta=(
                        function.get("arguments", "")
                        if isinstance(function, Mapping)
                        else ""
                    ),
                )

        tool_calls = [
            LlmToolCall(
                name=str(part.get("name") or ""),
                arguments=_parse_arguments(part.get("arguments")),
                call_id=_optional_str(part.get("id")),
            )
            for _, part in sorted(tool_call_parts.items())
        ]
        text = "".join(content_parts)
        _emit(
            event_sink,
            "model_stream_completed",
            text=text,
            tool_calls=[call.to_dict() for call in tool_calls],
            usage=usage,
            chunk_count=chunk_count,
            finish_reason=finish_reason,
        )
        raw: dict[str, Any] = {
            "stream": True,
            "chunk_count": chunk_count,
        }
        if finish_reason is not None:
            raw["finish_reason"] = finish_reason
        if usage:
            raw["usage"] = usage
        return LlmAgentResponse(
            text=text,
            tool_calls=tuple(tool_calls),
            assistant_metadata=(
                {"reasoning_content": "".join(reasoning_content_parts)}
                if has_reasoning_content
                else {}
            ),
            usage=usage,
            raw=raw,
        )

    def _request_body(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        openai_tools = [_to_openai_tool(tool) for tool in tools]
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                _to_openai_message(message)
                for message in _merge_system_messages(messages)
            ],
        }
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
        if self.config.max_tokens is not None:
            body["max_tokens"] = self.config.max_tokens
        if self.config.reasoning_effort is not None:
            body["reasoning_effort"] = self.config.reasoning_effort
        body.update(dict(self.config.extra_body))
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "guild-manager-bench/0.1",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _chat_completions_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"


def _to_openai_tool(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {"type": "object"}),
        },
    }


def _merge_system_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    system_parts: list[str] = []
    remaining: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        remaining.append(dict(message))
    if not system_parts:
        return remaining
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *remaining]


def _to_openai_message(message: Mapping[str, Any]) -> dict[str, Any]:
    role = message["role"]
    if role == "assistant":
        content = message.get("content")
        reasoning_content = message.get("reasoning_content")
        tool_calls = message.get("tool_calls") or []

        data: dict[str, Any] = {
            "role": "assistant",
            "content": content if isinstance(content, str) and content else None,
        }
        if isinstance(reasoning_content, str):
            data["reasoning_content"] = reasoning_content
        if tool_calls:
            data["tool_calls"] = [
                _to_openai_tool_call(call)
                for call in tool_calls
            ]
        # Some providers (e.g. Xiaomi) reject assistant messages that have
        # neither content, reasoning_content, nor tool_calls set.
        if not data.get("content") and "reasoning_content" not in data and "tool_calls" not in data:
            data["content"] = ""
        return data
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message["tool_call_id"],
            "content": message.get("content", ""),
        }
    return {
        "role": role,
        "content": message.get("content", ""),
    }


def _to_openai_tool_call(call: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": call["id"],
        "type": "function",
        "function": {
            "name": call["name"],
            "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
        },
    }


def _parse_chat_completion_response(response: Mapping[str, Any]) -> LlmAgentResponse:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAICompatibleError("chat completion response has no choices")
    message = choices[0].get("message", {})
    if not isinstance(message, Mapping):
        raise OpenAICompatibleError("chat completion choice has no message")

    content = message.get("content") or ""
    tool_calls = [
        _parse_tool_call(item)
        for item in message.get("tool_calls") or []
        if isinstance(item, Mapping)
    ]
    return LlmAgentResponse(
        text=content,
        tool_calls=tuple(tool_calls),
        assistant_metadata=_assistant_metadata_from_openai_message(message),
        usage=_usage_from_response(response),
        raw=dict(response),
    )


def _first_delta(chunk: Mapping[str, Any]) -> Mapping[str, Any] | None:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    delta = choices[0].get("delta")
    return delta if isinstance(delta, Mapping) else None


def _finish_reason_from_chunk(chunk: Mapping[str, Any]) -> str | None:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    finish_reason = choices[0].get("finish_reason")
    return finish_reason if isinstance(finish_reason, str) else None


def _usage_from_response(response: Mapping[str, Any]) -> dict[str, Any]:
    usage = response.get("usage")
    return dict(usage) if isinstance(usage, Mapping) else {}


def _assistant_metadata_from_openai_message(
    message: Mapping[str, Any],
) -> dict[str, Any]:
    if "reasoning_content" not in message:
        return {}
    value = message.get("reasoning_content")
    return {"reasoning_content": value if isinstance(value, str) else ""}


def _parse_tool_call(item: Mapping[str, Any]) -> LlmToolCall:
    if item.get("type") != "function":
        return LlmToolCall(
            name=str(item.get("type", "unknown_tool")),
            arguments={},
            call_id=_optional_str(item.get("id")),
        )
    function = item.get("function", {})
    if not isinstance(function, Mapping):
        function = {}
    name = str(function.get("name") or "")
    arguments = _parse_arguments(function.get("arguments"))
    return LlmToolCall(
        name=name,
        arguments=arguments,
        call_id=_optional_str(item.get("id")),
    )


def _parse_arguments(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _tool_call_index(item: Mapping[str, Any]) -> int:
    value = item.get("index")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _emit(event_sink: EventSink | None, event_type: str, **payload: Any) -> None:
    if event_sink is None:
        return
    event_sink({"type": event_type, **payload})


def _urllib_transport(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with _urlopen_with_retry(request, timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAICompatibleError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OpenAICompatibleError(str(exc)) from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpenAICompatibleError("response was not valid JSON") from exc
    if not isinstance(data, Mapping):
        raise OpenAICompatibleError("response JSON must be an object")
    return data


def _urllib_stream_transport(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
) -> Iterable[Mapping[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with _urlopen_with_retry(request, timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                else:
                    continue
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise OpenAICompatibleError("stream chunk was not valid JSON") from exc
                if isinstance(payload, Mapping):
                    yield payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAICompatibleError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OpenAICompatibleError(str(exc)) from exc
