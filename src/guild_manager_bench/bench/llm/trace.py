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

    @property
    def ok(self) -> bool | None:
        value = self.result.get("ok")
        return value if isinstance(value, bool) else None

    @property
    def error(self) -> str | None:
        value = self.result.get("error")
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": dict(self.arguments),
            "result": dict(self.result),
            "ok": self.ok,
            "error": self.error,
        }


@dataclass(slots=True)
class ModelResponseRecord:
    """一次模型响应。"""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [dict(call) for call in self.tool_calls],
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
        }
