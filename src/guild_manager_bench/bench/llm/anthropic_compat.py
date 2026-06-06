from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from guild_manager_bench.bench.llm.openai_compat import (
    EventSink,
    StreamTransport,
    Transport,
    _emit,
    _first_config_value,
    _optional_str,
    _parse_arguments,
    load_dotenv_values,
)
from guild_manager_bench.bench.llm.runner import LlmAgentResponse, LlmToolCall


class AnthropicMessagesError(RuntimeError):
    """Anthropic Messages API 调用失败。"""


def _optional_bool_config(
    explicit_value: bool | None,
    dotenv_values: Mapping[str, str],
    name: str,
) -> bool | None:
    if explicit_value is not None:
        return explicit_value
    value = _first_config_value(None, dotenv_values, name)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    raise AnthropicMessagesError(
        f"{name} must be true/false, yes/no, on/off, enabled/disabled, or 1/0"
    )


@dataclass(frozen=True, slots=True)
class AnthropicMessagesConfig:
    """Anthropic Messages API 配置。"""

    model: str
    api_key: str | None = None
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"
    timeout: float = 180.0
    max_tokens: int = 4096
    temperature: float | None = None
    top_p: float | None = None
    tool_choice: str | Mapping[str, Any] | None = "auto"
    thinking: bool | None = None
    effort: str | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        env_file: str | os.PathLike[str] | None = ".env",
        timeout: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        thinking: bool | None = None,
        effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AnthropicMessagesConfig:
        """从显式参数、进程环境变量或 dotenv 文件创建配置。"""

        dotenv_values = load_dotenv_values(env_file) if env_file is not None else {}
        resolved_model = _first_config_value(
            model,
            dotenv_values,
            "ANTHROPIC_MODEL",
        )
        if not resolved_model:
            raise AnthropicMessagesError(
                "model is required or ANTHROPIC_MODEL must be set"
            )
        resolved_timeout = _first_config_value(
            timeout,
            dotenv_values,
            "ANTHROPIC_TIMEOUT",
        )
        if resolved_timeout is not None:
            resolved_timeout = float(resolved_timeout)
        else:
            resolved_timeout = 180.0
        resolved_max_tokens = _first_config_value(
            max_tokens,
            dotenv_values,
            "ANTHROPIC_MAX_TOKENS",
        )
        if resolved_max_tokens is not None:
            resolved_max_tokens = int(resolved_max_tokens)
        else:
            resolved_max_tokens = 4096
        return cls(
            model=resolved_model,
            api_key=_first_config_value(
                api_key,
                dotenv_values,
                "ANTHROPIC_API_KEY",
            ),
            base_url=_first_config_value(
                base_url,
                dotenv_values,
                "ANTHROPIC_BASE_URL",
                default="https://api.anthropic.com",
            ),
            api_version=_first_config_value(
                api_version,
                dotenv_values,
                "ANTHROPIC_API_VERSION",
                default="2023-06-01",
            ),
            timeout=resolved_timeout,
            max_tokens=resolved_max_tokens,
            temperature=temperature,
            top_p=top_p,
            tool_choice=tool_choice,
            thinking=_optional_bool_config(
                thinking,
                dotenv_values,
                "ANTHROPIC_THINKING",
            ),
            effort=_first_config_value(
                effort,
                dotenv_values,
                "ANTHROPIC_EFFORT",
                "ANTHROPIC_REASONING_EFFORT",
            ),
            extra_body={} if extra_body is None else dict(extra_body),
        )


class AnthropicMessagesAgent:
    """Anthropic Messages API 模型适配器。"""

    def __init__(
        self,
        config: AnthropicMessagesConfig,
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
        api_version: str | None = None,
        env_file: str | os.PathLike[str] | None = ".env",
        timeout: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        tool_choice: str | Mapping[str, Any] | None = "auto",
        thinking: bool | None = None,
        effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AnthropicMessagesAgent:
        """从 ANTHROPIC_* 配置创建适配器。"""

        return cls(
            AnthropicMessagesConfig.from_env(
                model=model,
                api_key=api_key,
                base_url=base_url,
                api_version=api_version,
                env_file=env_file,
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                tool_choice=tool_choice,
                thinking=thinking,
                effort=effort,
                extra_body=extra_body,
            )
        )

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        """调用 Messages 接口并解析 tool_use blocks。"""

        body = self._request_body(messages, tools)
        response = self._transport(
            self._messages_url(),
            self._headers(),
            body,
            self.config.timeout,
        )
        return _parse_messages_response(response)

    def respond_stream(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        event_sink: EventSink | None = None,
    ) -> LlmAgentResponse:
        """流式调用 Messages 接口并解析最终 tool_use blocks。"""

        body = self._request_body(messages, tools)
        body["stream"] = True
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        has_reasoning_content = False
        content_block_parts: dict[int, dict[str, Any]] = {}
        tool_call_parts: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        stop_reason: str | None = None
        chunk_count = 0

        for event in self._stream_transport(
            self._messages_url(),
            self._headers(),
            body,
            self.config.timeout,
        ):
            chunk_count += 1
            event_type = event.get("type")

            if event_type == "message_start":
                msg = event.get("message", {})
                msg_usage = msg.get("usage")
                if isinstance(msg_usage, Mapping):
                    usage.update(msg_usage)

            elif event_type == "content_block_start":
                block = event.get("content_block", {})
                block_type = block.get("type")
                index = event.get("index", 0)
                if isinstance(index, int) and isinstance(block, Mapping):
                    content_block_parts[index] = dict(block)
                if block_type == "tool_use":
                    tool_call_parts[index] = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input_json": "",
                    }

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        text_parts.append(text)
                        block = content_block_parts.get(event.get("index", 0))
                        if block is not None:
                            block["text"] = str(block.get("text") or "") + text
                        _emit(event_sink, "model_delta", text=text)
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if isinstance(thinking, str):
                        has_reasoning_content = True
                        reasoning_parts.append(thinking)
                        block = content_block_parts.get(event.get("index", 0))
                        if block is not None:
                            block["thinking"] = (
                                str(block.get("thinking") or "") + thinking
                            )
                        if thinking:
                            _emit(
                                event_sink,
                                "model_reasoning_delta",
                                text=thinking,
                            )
                elif delta_type == "signature_delta":
                    signature = delta.get("signature", "")
                    block = content_block_parts.get(event.get("index", 0))
                    if block is not None and isinstance(signature, str):
                        block["signature"] = (
                            str(block.get("signature") or "") + signature
                        )
                elif delta_type == "input_json_delta":
                    index = event.get("index", 0)
                    partial = delta.get("partial_json", "")
                    if index in tool_call_parts:
                        tool_call_parts[index]["input_json"] += partial
                    _emit(
                        event_sink,
                        "tool_call_delta",
                        index=index,
                        arguments_delta=partial,
                    )

            elif event_type == "message_delta":
                delta = event.get("delta", {})
                if isinstance(delta.get("stop_reason"), str):
                    stop_reason = delta["stop_reason"]
                msg_usage = event.get("usage")
                if isinstance(msg_usage, Mapping):
                    usage.update(msg_usage)

            elif event_type == "message_stop":
                break

        tool_calls = [
            LlmToolCall(
                name=str(part["name"]),
                arguments=_parse_arguments(part["input_json"]),
                call_id=_optional_str(part["id"]),
            )
            for _, part in sorted(tool_call_parts.items())
        ]
        for index, part in tool_call_parts.items():
            block = content_block_parts.get(index)
            if block is not None:
                block["input"] = _parse_arguments(part["input_json"])
        content_blocks = [
            dict(block)
            for _, block in sorted(content_block_parts.items())
        ]
        assistant_metadata: dict[str, Any] = {}
        if content_blocks:
            assistant_metadata["anthropic_content_blocks"] = content_blocks
        if has_reasoning_content:
            assistant_metadata["reasoning_content"] = "".join(reasoning_parts)
        text = "".join(text_parts)
        _emit(
            event_sink,
            "model_stream_completed",
            text=text,
            tool_calls=[call.to_dict() for call in tool_calls],
            usage=usage,
            chunk_count=chunk_count,
            stop_reason=stop_reason,
        )
        raw: dict[str, Any] = {
            "stream": True,
            "chunk_count": chunk_count,
        }
        if stop_reason is not None:
            raw["stop_reason"] = stop_reason
        if usage:
            raw["usage"] = usage
        return LlmAgentResponse(
            text=text,
            tool_calls=tuple(tool_calls),
            assistant_metadata=assistant_metadata,
            usage=usage,
            raw=raw,
        )

    def _request_body(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        system_text, remaining = _extract_system_prompt(messages)
        anthropic_tools = [_to_anthropic_tool(tool) for tool in tools]
        body: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": _to_anthropic_messages(remaining),
        }
        if system_text:
            body["system"] = system_text
        if anthropic_tools:
            body["tools"] = anthropic_tools
            tool_choice = _to_anthropic_tool_choice(self.config.tool_choice)
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p
        if self.config.thinking is not None:
            body["thinking"] = {
                "type": "adaptive" if self.config.thinking else "disabled"
            }
        if self.config.effort is not None:
            body["output_config"] = {"effort": self.config.effort}
        body.update(dict(self.config.extra_body))
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "guild-manager-bench/0.1",
            "anthropic-version": self.config.api_version,
        }
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        return headers

    def _messages_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/v1/messages"


# ---------------------------------------------------------------------------
# 消息格式转换
# ---------------------------------------------------------------------------


def _extract_system_prompt(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """从消息列表中提取 system 消息，返回 (system_text, remaining_messages)。"""

    parts: list[str] = []
    remaining: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
        else:
            remaining.append(dict(msg))
    return "\n\n".join(parts), remaining


def _to_anthropic_tool(schema: Mapping[str, Any]) -> dict[str, Any]:
    """将内部工具 schema 转换为 Anthropic 工具格式。"""

    return {
        "name": schema["name"],
        "description": schema.get("description", ""),
        "input_schema": schema.get("parameters", {"type": "object"}),
    }


def _to_anthropic_tool_choice(
    tool_choice: str | Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """将内部 tool_choice 转换为 Anthropic 格式。"""

    if tool_choice is None or tool_choice == "none":
        return None
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "required":
            return {"type": "any"}
        return None
    if isinstance(tool_choice, Mapping):
        tc_type = tool_choice.get("type")
        # 已是 Anthropic 格式
        if tc_type in ("auto", "any", "tool"):
            return tool_choice
        # OpenAI 格式 {"type": "function", "function": {"name": "..."}}
        if tc_type == "function":
            func = tool_choice.get("function", {})
            if isinstance(func, Mapping) and func.get("name"):
                return {"type": "tool", "name": func["name"]}
    return None


def _assistant_to_content_blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """将内部 assistant 消息转换为 Anthropic content blocks 数组。"""

    preserved_blocks = message.get("anthropic_content_blocks")
    if isinstance(preserved_blocks, Sequence) and not isinstance(
        preserved_blocks, str | bytes
    ):
        blocks = [
            dict(block)
            for block in preserved_blocks
            if isinstance(block, Mapping)
        ]
        if blocks:
            return blocks

    blocks: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    for call in message.get("tool_calls") or []:
        if not isinstance(call, Mapping):
            continue
        blocks.append({
            "type": "tool_use",
            "id": call.get("id", ""),
            "name": call.get("name", ""),
            "input": dict(call["arguments"]) if isinstance(call.get("arguments"), Mapping) else {},
        })
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


def _to_anthropic_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """将内部消息列表转换为 Anthropic Messages 格式。

    system 消息应已被提取为顶层参数，此处只处理 user/assistant/tool 消息。
    连续的 tool 消息会合并到同一个 user 消息中（Anthropic 要求严格交替）。
    """

    result: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "tool":
            # 收集连续的 tool 消息合并为一个 user 消息
            tool_results: list[dict[str, Any]] = []
            while i < len(messages) and messages[i].get("role") == "tool":
                t = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t.get("tool_call_id", ""),
                    "content": t.get("content", ""),
                })
                i += 1
            result.append({"role": "user", "content": tool_results})

        elif role == "assistant":
            content_blocks = _assistant_to_content_blocks(msg)
            result.append({"role": "assistant", "content": content_blocks})
            i += 1

        elif role == "user":
            result.append({"role": "user", "content": msg.get("content", "")})
            i += 1

        else:
            i += 1

    return result


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


def _parse_messages_response(response: Mapping[str, Any]) -> LlmAgentResponse:
    """解析 Anthropic Messages 非流式响应。"""

    content = response.get("content")
    if not isinstance(content, list):
        raise AnthropicMessagesError("response content must be an array")

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    has_reasoning_content = False
    content_blocks: list[dict[str, Any]] = []
    tool_calls: list[LlmToolCall] = []

    for block in content:
        if not isinstance(block, Mapping):
            continue
        content_blocks.append(dict(block))
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "thinking":
            thinking = block.get("thinking")
            if isinstance(thinking, str):
                has_reasoning_content = True
                reasoning_parts.append(thinking)
        elif block_type == "tool_use":
            tool_calls.append(
                LlmToolCall(
                    name=str(block.get("name") or ""),
                    arguments=(
                        dict(block["input"])
                        if isinstance(block.get("input"), Mapping)
                        else {}
                    ),
                    call_id=_optional_str(block.get("id")),
                )
            )

    usage_raw = response.get("usage")
    usage = dict(usage_raw) if isinstance(usage_raw, Mapping) else {}
    assistant_metadata: dict[str, Any] = {}
    if content_blocks:
        assistant_metadata["anthropic_content_blocks"] = content_blocks
    if has_reasoning_content:
        assistant_metadata["reasoning_content"] = "".join(reasoning_parts)

    return LlmAgentResponse(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        assistant_metadata=assistant_metadata,
        usage=usage,
        raw=dict(response),
    )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AnthropicMessagesError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AnthropicMessagesError(str(exc)) from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AnthropicMessagesError("response was not valid JSON") from exc
    if not isinstance(data, Mapping):
        raise AnthropicMessagesError("response JSON must be an object")
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or line.startswith("event:"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise AnthropicMessagesError(
                        "stream chunk was not valid JSON"
                    ) from exc
                if isinstance(payload, Mapping):
                    yield payload
                    if payload.get("type") == "message_stop":
                        break
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AnthropicMessagesError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AnthropicMessagesError(str(exc)) from exc
