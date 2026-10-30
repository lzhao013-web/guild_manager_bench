from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Mapping

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


MAX_MEMO_CHARS = 500


@dataclass(frozen=True, slots=True)
class Memo:
    """单份工作备忘及其最后修改回合。"""

    content: str = ""
    updated_turn: int | None = None


@dataclass(slots=True)
class MemoStore:
    """本局内持续保留的工作备忘，写入时整份覆盖。"""

    memo: Memo = field(default_factory=Memo)

    def write(self, content: Any, *, turn: int) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > MAX_MEMO_CHARS:
            raise ValueError(f"content must be <= {MAX_MEMO_CHARS} characters")

        self.memo = Memo(content=content.strip(), updated_turn=turn)
        return {
            "content": self.memo.content,
            "updated_turn": self.memo.updated_turn,
            "max_chars": MAX_MEMO_CHARS,
        }

    def snapshot(self) -> Memo:
        return self.memo

    def restore(self, memo: Memo) -> None:
        """恢复正文及最后修改回合，用于回滚失败尝试。"""
        self.memo = memo


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
            memo = self.memo_store.write(values.get("content"), turn=self._turn)
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
        "各回合独立对话，备忘录在本局跨回合持续保留，不写则不变。"
        '每次写入整份覆盖旧内容，不追加，content="" 清空。'
        "仅在内容需要更新时于 end_turn 前调用，及时移除已完成或过期的计划。"
    ),
    "parameters": {
        "type": "object",
        "required": ["content"],
        "properties": {
            "content": {
                "type": "string",
                "maxLength": MAX_MEMO_CHARS,
                "description": f"完整备忘内容，最多 {MAX_MEMO_CHARS} 字符。空字符串清空备忘。",
            },
        },
        "additionalProperties": False,
    },
}
