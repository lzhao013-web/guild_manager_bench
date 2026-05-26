from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


RunStatus = Literal["completed", "failed"]
TurnStatus = Literal["completed", "failed"]


@dataclass(slots=True)
class ToolCallRecord:
    """一次 LLM tool call 及其返回。"""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    call_id: str | None = None

    @property
    def ok(self) -> bool | None:
        value = self.result.get("ok")
        return value if isinstance(value, bool) else None

    @property
    def error(self) -> str | None:
        value = self.result.get("error")
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "arguments": dict(self.arguments),
            "result": dict(self.result),
            "ok": self.ok,
            "error": self.error,
        }
        if self.call_id is not None:
            data["call_id"] = self.call_id
        return data


@dataclass(slots=True)
class ModelResponseRecord:
    """一次模型响应。"""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    step: int | None = None
    request_messages: list[dict[str, Any]] = field(default_factory=list)
    request_tools: list[dict[str, Any]] = field(default_factory=list)
    assistant_metadata: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "request": {
                "messages": [dict(message) for message in self.request_messages],
                "tools": [dict(tool) for tool in self.request_tools],
            },
            "text": self.text,
            "tool_calls": [dict(call) for call in self.tool_calls],
            "assistant_metadata": dict(self.assistant_metadata),
            "timing": dict(self.timing),
            "usage": dict(self.usage),
            "raw": self.raw,
        }


@dataclass(slots=True)
class TurnTrace:
    """一个游戏回合内的 LLM 交互轨迹。"""

    turn: int
    prompt: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    model_responses: list[ModelResponseRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    status: TurnStatus | None = None
    failure_reason: str | None = None

    def complete(self) -> None:
        self.status = "completed"

    def fail(self, reason: str) -> None:
        self.status = "failed"
        self.failure_reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "prompt": self.prompt,
            "messages": [dict(message) for message in self.messages],
            "model_responses": [
                response.to_dict()
                for response in self.model_responses
            ],
            "tool_calls": [
                call.to_dict()
                for call in self.tool_calls
            ],
            "status": self.status,
            "failure_reason": self.failure_reason,
        }


@dataclass(slots=True)
class LlmGameRun:
    """一次 LLM benchmark 跑局结果。"""

    status: RunStatus
    session_id: str
    final_observation: Mapping[str, Any]
    turns: list[TurnTrace]
    failure_reason: str | None = None
    archive_dir: str | None = None
    score: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "final_observation": dict(self.final_observation),
            "turns": [
                turn.to_dict()
                for turn in self.turns
            ],
            "failure_reason": self.failure_reason,
            "archive_dir": self.archive_dir,
            "score": None if self.score is None else dict(self.score),
        }
