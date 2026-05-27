from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.refs import (
    build_numeric_refs,
    resolve_tool_arguments,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools, ToolCallError


@dataclass(slots=True)
class ToolBudget:
    """单个 LLM 回合的非 end_turn 工具调用预算。"""

    max_tool_calls: int
    used: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_tool_calls, int)
            or isinstance(self.max_tool_calls, bool)
            or self.max_tool_calls < 0
        ):
            raise ValueError("max_tool_calls must be >= 0")
        if not isinstance(self.used, int) or isinstance(self.used, bool) or self.used < 0:
            raise ValueError("used must be >= 0")

    @property
    def remaining(self) -> int:
        """返回剩余的非 end_turn 工具调用次数。"""

        return max(0, self.max_tool_calls - self.used)

    @property
    def exhausted(self) -> bool:
        """预算是否已经耗尽。"""

        return self.remaining == 0

    def consume(self) -> None:
        """消耗一次非 end_turn 工具调用预算。"""

        self.used += 1


@dataclass(slots=True)
class MemoStore:
    """LLM run 内跨回合保留的备忘录。"""

    entries: list[str] = field(default_factory=list)
    max_entries: int = 20
    max_entry_chars: int = 2000

    def __post_init__(self) -> None:
        if not isinstance(self.max_entries, int) or self.max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if not isinstance(self.max_entry_chars, int) or self.max_entry_chars < 1:
            raise ValueError("max_entry_chars must be >= 1")
        self.entries = [str(entry) for entry in self.entries if str(entry).strip()]
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries :]

    def write(self, content: Any) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        text = content.strip()
        if not text:
            raise ValueError("content must not be empty")
        if len(text) > self.max_entry_chars:
            raise ValueError(f"content must be <= {self.max_entry_chars} characters")

        dropped = 0
        self.entries.append(text)
        if len(self.entries) > self.max_entries:
            dropped = len(self.entries) - self.max_entries
            del self.entries[:dropped]
        return {
            "content": text,
            "count": len(self.entries),
            "max_entries": self.max_entries,
            "dropped_oldest": dropped,
        }

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self.entries)


class TurnToolHarness:
    """单个游戏回合内的 LLM 工具调用包装器。

    该类只管理评测协议：预算、允许工具和 end_turn 结束信号。
    具体游戏状态和动作结算仍由 GuildManagerTools 负责。
    """

    def __init__(
        self,
        tools: GuildManagerTools,
        session_id: str,
        *,
        max_tool_calls: int,
        memo_store: MemoStore | None = None,
    ) -> None:
        self.tools = tools
        self.session_id = session_id
        self.memo_store = memo_store or MemoStore()
        self.budget = ToolBudget(max_tool_calls=max_tool_calls)
        self.ended = False
        self._agent_tool_names = tuple(
            schema["name"]
            for schema in self.tool_schemas()
        )

    def tool_schemas(self) -> list[dict[str, Any]]:
        """返回当前回合可注册给 LLM 的工具 schema。"""

        return self.tools.list_tool_schemas() + [deepcopy(_WRITE_MEMO_SCHEMA)]

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用一个 LLM 工具，并附加当前回合预算状态。"""

        if self.ended:
            return self._error("turn already ended")

        if name != "end_turn" and self.budget.exhausted:
            return self._error("tool call budget exhausted; only end_turn is allowed")

        if name != "end_turn":
            self.budget.consume()

        if name == "write_memo":
            result = self._write_memo(arguments)
        else:
            try:
                result = self.tools.call_tool(
                    name,
                    self._arguments_with_session(name, arguments),
                )
            except (ToolCallError, ValueError) as exc:
                result = {"ok": False, "error": str(exc)}

        if name == "end_turn" and result.get("ok") is True:
            self.ended = True

        return self._with_budget(result)

    def _arguments_with_session(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        values = {} if arguments is None else dict(arguments)
        observation = self.tools.get_observation(self.session_id)["observation"]
        values = resolve_tool_arguments(observation, name, values)
        values["session_id"] = self.session_id
        return values

    def _write_memo(self, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        values = {} if arguments is None else dict(arguments)
        try:
            memo = self.memo_store.write(values.get("content"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "memo": memo}

    def _error(self, message: str) -> dict[str, Any]:
        return self._with_budget({"ok": False, "error": message})

    def _with_budget(self, result: dict[str, Any]) -> dict[str, Any]:
        data = dict(result)
        try:
            observation = self.tools.get_observation(self.session_id)["observation"]
            data["_llm_refs"] = build_numeric_refs(observation)
        except Exception:
            pass
        data["tool_budget"] = self._budget_state()
        return data

    def _budget_state(self) -> dict[str, Any]:
        if self.ended:
            allowed_tools: list[str] = []
        elif self.budget.exhausted:
            allowed_tools = ["end_turn"]
        else:
            allowed_tools = list(self._agent_tool_names)
        return {
            "max_tool_calls": self.budget.max_tool_calls,
            "used": self.budget.used,
            "remaining": self.budget.remaining,
            "end_turn_required": self.budget.exhausted and not self.ended,
            "allowed_tools": allowed_tools,
        }


_WRITE_MEMO_SCHEMA: dict[str, Any] = {
    "name": "write_memo",
    "description": "写入一条跨回合备忘录。下回合开始时，已记录的备忘录会出现在提示词中。",
    "parameters": {
        "type": "object",
        "required": ["content"],
        "properties": {
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "要记录的文字，最多 2000 字符。",
            },
        },
        "additionalProperties": False,
    },
}


def memo_entries_from_tool_steps(turns: Sequence[Any]) -> tuple[str, ...]:
    """从 replay turn steps 中恢复成功写入的备忘录。"""

    store = MemoStore()
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        for step in _sequence(turn.get("steps")):
            if not isinstance(step, Mapping):
                continue
            if step.get("type") != "tool_result" or step.get("name") != "write_memo":
                continue
            content = step.get("content")
            if not (isinstance(content, str) and content.lstrip().startswith("OK ")):
                continue
            arguments = step.get("arguments")
            if isinstance(arguments, Mapping):
                try:
                    store.write(arguments.get("content"))
                except ValueError:
                    continue
    return store.snapshot()


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()
