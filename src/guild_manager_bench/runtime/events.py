from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping


SessionEventType = Literal[
    "session_started",
    "preparation_applied",
    "turn_ended",
    "action_rejected",
]


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """会话事件，用于实时观看、日志记录和回放。"""

    sequence: int
    turn: int
    event_type: SessionEventType
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be >= 1")
        if not isinstance(self.turn, int) or self.turn < 1:
            raise ValueError("turn must be >= 1")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    """把会话事件转成 JSON 友好的字典。"""

    return {
        "sequence": event.sequence,
        "turn": event.turn,
        "type": event.event_type,
        "payload": dict(event.payload),
    }

