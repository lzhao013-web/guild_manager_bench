from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.refs import (
    build_numeric_refs,
    resolve_tool_arguments_with_refs,
    update_numeric_refs,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools, ToolCallError


@dataclass(slots=True)
class ToolBudget:
    """单个 LLM 回合的收费工具调用预算。"""

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
        """返回剩余的收费工具调用次数。"""

        return max(0, self.max_tool_calls - self.used)

    @property
    def exhausted(self) -> bool:
        """预算是否已经耗尽。"""

        return self.remaining == 0

    def consume(self) -> None:
        """消耗一次收费工具调用预算。"""

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

    def consume(self) -> tuple[str, ...]:
        """返回所有现有备忘录条目并清空。"""
        entries = tuple(self.entries)
        self.entries.clear()
        return entries

    def restore(self, entries: tuple[str, ...]) -> None:
        """恢复备忘录快照。"""
        self.entries = list(entries)

    def clear(self) -> None:
        self.entries.clear()


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
        max_battle_preview_per_turn: int = 3,
        endgame_start_turn: int | None = None,
        memo_store: MemoStore | None = None,
    ) -> None:
        self.tools = tools
        self.session_id = session_id
        self.memo_store = memo_store or MemoStore()
        self.budget = ToolBudget(max_tool_calls=max_tool_calls)
        self.max_battle_preview_per_turn = max_battle_preview_per_turn
        self.endgame_start_turn = endgame_start_turn
        self._battle_preview_count = 0
        self.ended = False
        # 固化本回合的数字 ID 映射，避免并行动作导致列表变化后序号偏移
        observation = tools.get_observation(session_id)["observation"]
        self._turn = int(observation["turn"])
        self._turn_start_state = tools.get_state(session_id)
        self._turn_refs = build_numeric_refs(observation)
        self._agent_tool_names = tuple(
            schema["name"]
            for schema in self.tool_schemas()
        )

    @cached_property
    def turn_start_rank_score(self) -> float | None:
        """终局回合的固定基线，按需计算，避免恢复历史动作时重复评分。"""

        if self.endgame_start_turn is None or self._turn < self.endgame_start_turn:
            return None
        from guild_manager_bench.bench.metrics import compute_rank_score

        return round(compute_rank_score(self.tools.definition, self._turn_start_state), 2)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """返回当前回合可注册给 LLM 的工具 schema。"""

        schemas = self.tools.list_tool_schemas()
        schemas.append(deepcopy(_WRITE_MEMO_SCHEMA))
        return schemas

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用一个 LLM 工具，并附加当前回合预算状态。"""

        if self.ended:
            return self._error("回合已结束")

        if name not in {"end_turn", "execute_plan"} and self.budget.exhausted:
            return self._error("工具调用次数已用尽；仅允许使用 end_turn")

        if name == "preview_battle" and self._battle_preview_count >= self.max_battle_preview_per_turn:
            return self._error(
                f"preview_battle 次数已达上限（每回合 {self.max_battle_preview_per_turn} 次）；"
                "请仅在关键对战时使用 preview_battle"
            )

        if name not in {"end_turn", "execute_plan"}:
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

        if name == "preview_team_power" and result.get("ok") is True:
            result = {
                **result,
                "remaining_calls": self.budget.remaining,
                "can_execute": bool(result.get("plan_id")) and self.budget.remaining >= result["required_calls"],
            }

        if name == "preview_battle":
            self._battle_preview_count += 1

        if name == "end_turn" and result.get("ok") is True:
            self.ended = True

        # 成功后增量更新 refs（为新增的实体分配序号，已有项序号不变）
        if result.get("ok") is True and not self.ended:
            try:
                self.refresh_refs()
            except Exception:
                pass

        return self._with_budget(result)

    def refresh_refs(self) -> None:
        """只从整批提交后的真实状态分配新编号，恢复时保持相同顺序。"""
        observation = self.tools.get_observation(self.session_id)["observation"]
        update_numeric_refs(self._turn_refs, observation)

    def _arguments_with_session(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        values = {} if arguments is None else dict(arguments)
        values = resolve_tool_arguments_with_refs(self._turn_refs, name, values)
        values["session_id"] = self.session_id
        if name == "execute_plan":
            values["remaining_calls"] = self.budget.remaining
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
        data["_llm_refs"] = self._turn_refs
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
            "battle_preview_used": self._battle_preview_count,
            "battle_preview_limit": self.max_battle_preview_per_turn,
        }


_WRITE_MEMO_SCHEMA: dict[str, Any] = {
    "name": "write_memo",
    "description": (
        "各回合独立对话，跨回合计划通过备忘录保存。同回合多次写入会追加，建议在回合末、调用 end_turn 前汇总写入。"
        "备忘录在下一回合初展示一次后清空。"
    ),
    "parameters": {
        "type": "object",
        "required": ["content"],
        "properties": {
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "备忘内容，最多 500 字符。",
            },
        },
        "additionalProperties": False,
    },
}


def memo_entries_from_tool_steps(turns: Sequence[Any]) -> tuple[str, ...]:
    """从 replay turn steps 中恢复成功写入的备忘录（仅最后一回合）。"""

    store = MemoStore()
    for turn in reversed(list(turns)):
        if not isinstance(turn, Mapping):
            continue
        for step in _sequence(turn.get("steps")):
            if not isinstance(step, Mapping):
                continue
            if step.get("type") != "tool_result" or step.get("name") != "write_memo":
                continue
            content = step.get("content")
            if not (isinstance(content, str) and (content.lstrip().startswith("成功 ") or content.lstrip().startswith("OK "))):
                continue
            arguments = step.get("arguments")
            if isinstance(arguments, Mapping):
                try:
                    store.write(arguments.get("content"))
                except ValueError:
                    continue
        break
    return store.snapshot()


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()
