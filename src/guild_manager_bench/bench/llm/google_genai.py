from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence
from urllib.parse import quote

from guild_manager_bench.bench.llm.openai_compat import (
    EnvFile,
    EventSink,
    StreamTransport,
    Transport,
    _emit,
    _first_config_value,
    _optional_str,
    load_dotenv_values,
)
from guild_manager_bench.bench.llm.runner import LlmAgentResponse, LlmToolCall
from guild_manager_bench.bench.llm.session import (
    new_session_id,
    resolve_session_id,
    session_headers,
)


class GoogleGenerateContentError(RuntimeError):
    """Google Gemini GenerateContent API 调用失败。"""


DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
ToolChoice = Literal["auto", "none", "required"]


@dataclass(frozen=True, slots=True)
class GoogleGenerateContentConfig:
    """Google Gemini 原生 API 配置，base_url 包含 API 版本路径。"""

    model: str
    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 600.0
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    tool_choice: ToolChoice | None = "auto"
    reasoning_effort: str | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    session_id: str = field(default_factory=new_session_id)
    session_id_explicit: bool = False

    def __post_init__(self) -> None:
        if self.reasoning_effort is not None and self.reasoning_effort not in {"minimal", "low", "medium", "high"}:
            raise GoogleGenerateContentError("google reasoning_effort must be minimal, low, medium, or high")

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        session_id: str | None = None,
        env_file: EnvFile | None = ".env",
        timeout: float | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        tool_choice: ToolChoice | None = "auto",
        reasoning_effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> GoogleGenerateContentConfig:
        """按显式参数、进程环境变量、dotenv 文件的顺序读取配置。"""

        dotenv_values = load_dotenv_values(env_file) if env_file is not None else {}
        resolved_model = _first_config_value(model, dotenv_values, "GOOGLE_MODEL")
        if not resolved_model:
            raise GoogleGenerateContentError("model is required or GOOGLE_MODEL must be set")
        resolved_timeout = _first_config_value(timeout, dotenv_values, "GOOGLE_TIMEOUT", default="600")
        resolved_max_tokens = _first_config_value(max_tokens, dotenv_values, "GOOGLE_MAX_TOKENS")
        resolved_session = resolve_session_id(session_id, dotenv_values, "GOOGLE_SESSION_ID")
        return cls(
            model=resolved_model,
            api_key=_first_config_value(
                api_key, dotenv_values, "GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
            ),
            base_url=_first_config_value(base_url, dotenv_values, "GOOGLE_BASE_URL", default=DEFAULT_BASE_URL),
            timeout=float(resolved_timeout),
            temperature=temperature,
            top_p=top_p,
            max_tokens=int(resolved_max_tokens) if resolved_max_tokens is not None else None,
            tool_choice=tool_choice,
            reasoning_effort=_first_config_value(reasoning_effort, dotenv_values, "GOOGLE_REASONING_EFFORT"),
            extra_body={} if extra_body is None else dict(extra_body),
            session_id=resolved_session.value,
            session_id_explicit=resolved_session.explicit,
        )


class GoogleGenerateContentAgent:
    """通过 GenerateContent / StreamGenerateContent 运行 Gemini 模型。"""

    def __init__(
        self,
        config: GoogleGenerateContentConfig,
        *,
        transport: Transport | None = None,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        self.config = config
        self._session_id = config.session_id
        self._transport = transport or _urllib_transport
        self._stream_transport = stream_transport or _urllib_stream_transport

    def bind_session(self, session_id: str) -> None:
        """让同一局及其续跑使用稳定的网关路由会话。"""

        value = session_id.strip()
        if not value:
            raise GoogleGenerateContentError("session_id must be a non-empty string")
        self._session_id = value

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        session_id: str | None = None,
        env_file: EnvFile | None = ".env",
        timeout: float | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        tool_choice: ToolChoice | None = "auto",
        reasoning_effort: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> GoogleGenerateContentAgent:
        return cls(GoogleGenerateContentConfig.from_env(
            model=model, api_key=api_key, base_url=base_url, session_id=session_id,
            env_file=env_file, timeout=timeout, temperature=temperature, top_p=top_p,
            max_tokens=max_tokens, tool_choice=tool_choice, reasoning_effort=reasoning_effort,
            extra_body=extra_body,
        ))

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        response = self._transport(
            self._generate_url(streaming=False), self._headers(streaming=False),
            self._request_body(messages, tools), self.config.timeout,
        )
        candidate = _first_candidate(response)
        if candidate is None:
            raise GoogleGenerateContentError("generate content response has no candidates")
        parts = _candidate_parts(candidate)
        text, reasoning, calls = _parse_parts(parts)
        return LlmAgentResponse(
            text=text, tool_calls=calls,
            assistant_metadata=_assistant_metadata(parts, reasoning),
            usage=_normalize_usage(response.get("usageMetadata", {})), raw=dict(response),
        )

    def respond_stream(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        event_sink: EventSink | None = None,
    ) -> LlmAgentResponse:
        parts: list[dict[str, Any]] = []
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: list[LlmToolCall] = []
        native_usage: dict[str, Any] = {}
        chunk_count = 0
        has_candidate = False
        finish_reason: str | None = None
        for chunk in self._stream_transport(
            self._generate_url(streaming=True), self._headers(streaming=True),
            self._request_body(messages, tools), self.config.timeout,
        ):
            chunk_count += 1
            candidate = _first_candidate(chunk)
            chunk_usage = chunk.get("usageMetadata")
            if isinstance(chunk_usage, Mapping):
                # Gemini 的用量片段是累计值，不能逐片段相加。
                native_usage.update(chunk_usage)
            if candidate is None:
                continue
            has_candidate = True
            if candidate.get("finishReason"):
                finish_reason = candidate["finishReason"]
            chunk_parts = _candidate_parts(candidate)
            text, reasoning, chunk_calls = _parse_parts(chunk_parts)
            parts.extend(chunk_parts)
            if text:
                text_parts.append(text)
                _emit(event_sink, "model_delta", text=text)
            if reasoning is not None:
                reasoning_parts.append(reasoning)
                if reasoning:
                    _emit(event_sink, "model_reasoning_delta", text=reasoning)
            for call in chunk_calls:
                _emit(
                    event_sink, "tool_call_delta", index=len(calls), call_id=call.call_id,
                    name=call.name, arguments_delta=json.dumps(dict(call.arguments), ensure_ascii=False),
                )
                calls.append(call)

        if not has_candidate:
            raise GoogleGenerateContentError("generate content stream has no candidates")
        if finish_reason is None:
            raise GoogleGenerateContentError("generate content stream ended without finishReason")
        text = "".join(text_parts)
        reasoning = "".join(reasoning_parts) if reasoning_parts else None
        usage = _normalize_usage(native_usage)
        _emit(
            event_sink, "model_stream_completed", text=text,
            tool_calls=[call.to_dict() for call in calls], usage=usage,
            chunk_count=chunk_count, finish_reason=finish_reason,
        )
        return LlmAgentResponse(
            text=text, tool_calls=calls,
            assistant_metadata=_assistant_metadata(parts, reasoning), usage=usage,
            raw={
                "stream": True, "chunk_count": chunk_count, "finish_reason": finish_reason,
                "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish_reason}],
                "usageMetadata": native_usage,
            },
        )

    def _request_body(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        system_parts, contents = _to_google_contents(messages)
        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {
                    "name": tool["name"], "description": tool.get("description", ""),
                    "parametersJsonSchema": tool["parameters"],
                }
                for tool in tools
            ]}]
            if self.config.tool_choice is not None:
                mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}[self.config.tool_choice]
                body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        generation: dict[str, Any] = {
            key: value for key, value in (
                ("temperature", self.config.temperature), ("topP", self.config.top_p),
                ("maxOutputTokens", self.config.max_tokens),
            ) if value is not None
        }
        if self.config.reasoning_effort is not None:
            generation["thinkingConfig"] = {"thinkingLevel": self.config.reasoning_effort.upper()}
        if generation:
            body["generationConfig"] = generation
        body.update(dict(self.config.extra_body))
        return body

    def _headers(self, *, streaming: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if streaming else "application/json",
            "User-Agent": "guild-manager-bench/1.0",
            **session_headers(self._session_id),
        }
        if self.config.api_key:
            headers["x-goog-api-key"] = self.config.api_key
        return headers

    def _generate_url(self, *, streaming: bool) -> str:
        model = quote(self.config.model.removeprefix("models/"), safe="")
        method = "streamGenerateContent?alt=sse" if streaming else "generateContent"
        return f"{self.config.base_url.rstrip('/')}/models/{model}:{method}"


def _to_google_contents(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    native_call_ids: set[str] = set()
    for message in messages:
        role = message["role"]
        if role == "system":
            system_parts.append({"text": message["content"]})
            continue
        if role == "assistant":
            if "google_content_parts" in message:
                # 签名属于原生 Part，不能从正文和工具调用重建或合并。
                parts = [dict(part) for part in message["google_content_parts"]]
            else:
                parts = []
                if message.get("content"):
                    parts.append({"text": message["content"]})
                for call in message.get("tool_calls", ()):
                    function = {"name": call["name"], "args": dict(call["arguments"])}
                    if call.get("id"):
                        function["id"] = call["id"]
                    parts.append({"functionCall": function})
            for part in parts:
                function = part.get("functionCall")
                if isinstance(function, Mapping) and function.get("id"):
                    native_call_ids.add(function["id"])
            # MAX_TOKENS 可以没有任何 Part，runner 随后会追加重试提示。
            if not parts:
                continue
            content_role = "model"
        elif role == "tool":
            function_response: dict[str, Any] = {
                "name": message["name"], "response": {"output": message["content"]},
            }
            # runner 会补本地 call_id，但无原生 id 的调用不能因此凭空获得 id。
            if message["tool_call_id"] in native_call_ids:
                function_response["id"] = message["tool_call_id"]
            parts = [{"functionResponse": function_response}]
            content_role = "user"
        elif role == "user":
            parts = [{"text": message["content"]}]
            content_role = "user"
        else:
            raise GoogleGenerateContentError(f"unsupported message role: {role}")
        if content_role == "user" and contents and contents[-1]["role"] == "user":
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": content_role, "parts": parts})
    return system_parts, contents


def _first_candidate(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if "error" in response:
        raise GoogleGenerateContentError(f"Gemini API error: {response['error']}")
    feedback = response.get("promptFeedback", {})
    if isinstance(feedback, Mapping) and feedback.get("blockReason"):
        raise GoogleGenerateContentError(f"Gemini prompt blocked: {feedback['blockReason']}")
    candidates = response.get("candidates", [])
    if not isinstance(candidates, list):
        raise GoogleGenerateContentError("response candidates must be an array")
    if not candidates:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, Mapping):
        raise GoogleGenerateContentError("response candidate must be an object")
    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason not in {"STOP", "MAX_TOKENS"}:
        raise GoogleGenerateContentError(f"Gemini generation stopped: {finish_reason}")
    return candidate


def _candidate_parts(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = candidate.get("content", {})
    if not isinstance(content, Mapping):
        raise GoogleGenerateContentError("candidate content must be an object")
    parts = content.get("parts", [])
    if not isinstance(parts, list) or any(not isinstance(part, Mapping) for part in parts):
        raise GoogleGenerateContentError("candidate parts must be an array of objects")
    return [dict(part) for part in parts]


def _parse_parts(parts: Sequence[Mapping[str, Any]]) -> tuple[str, str | None, list[LlmToolCall]]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: list[LlmToolCall] = []
    for part in parts:
        if "text" in part:
            if not isinstance(part["text"], str):
                raise GoogleGenerateContentError("part text must be a string")
            (reasoning_parts if part.get("thought") else text_parts).append(part["text"])
        if "functionCall" in part:
            function = part["functionCall"]
            if not isinstance(function, Mapping) or not isinstance(function.get("name"), str) or not function["name"]:
                raise GoogleGenerateContentError("functionCall must have a non-empty name")
            args = function.get("args", {})
            if not isinstance(args, Mapping):
                raise GoogleGenerateContentError("functionCall args must be an object")
            calls.append(LlmToolCall(function["name"], dict(args), _optional_str(function.get("id"))))
    return "".join(text_parts), "".join(reasoning_parts) if reasoning_parts else None, calls


def _assistant_metadata(parts: Sequence[Mapping[str, Any]], reasoning: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"google_content_parts": [dict(part) for part in parts]}
    if reasoning is not None:
        metadata["reasoning_content"] = reasoning
    return metadata


def _normalize_usage(native: Mapping[str, Any]) -> dict[str, Any]:
    usage = dict(native)
    for source, target in (
        ("promptTokenCount", "input_tokens"), ("totalTokenCount", "total_tokens"),
        ("cachedContentTokenCount", "cache_read_input_tokens"),
    ):
        if source in native:
            usage[target] = native[source]
    if "candidatesTokenCount" in native or "thoughtsTokenCount" in native:
        # 思考 Token 属于输出，单看 candidatesTokenCount 会低估 Gemini 用量。
        usage["output_tokens"] = native.get("candidatesTokenCount", 0) + native.get("thoughtsTokenCount", 0)
    if "thoughtsTokenCount" in native:
        usage["output_tokens_details"] = {"reasoning_tokens": native["thoughtsTokenCount"]}
    return usage


def _decode_payload(data: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise GoogleGenerateContentError("response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise GoogleGenerateContentError("response JSON must be an object")
    return payload


def _urllib_transport(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float,
) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _decode_payload(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GoogleGenerateContentError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GoogleGenerateContentError(str(exc)) from exc


def _urllib_stream_transport(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float,
) -> Iterable[Mapping[str, Any]]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data_lines:
                        yield _decode_payload("\n".join(data_lines))
                        data_lines.clear()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].removeprefix(" "))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GoogleGenerateContentError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GoogleGenerateContentError(str(exc)) from exc
