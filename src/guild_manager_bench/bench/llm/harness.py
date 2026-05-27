from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
    ) -> None:
        self.tools = tools
        self.session_id = session_id
        self.budget = ToolBudget(max_tool_calls=max_tool_calls)
        self.ended = False
        self._agent_tool_names = tuple(
            schema["name"]
            for schema in self.tools.list_tool_schemas()
        )

    def tool_schemas(self) -> list[dict[str, Any]]:
        """返回当前回合可注册给 LLM 的工具 schema。"""

        return self.tools.list_tool_schemas()

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

        try:
            result = self.tools.call_tool(name, self._arguments_with_session(arguments))
        except ToolCallError as exc:
            result = {"ok": False, "error": str(exc)}

        if name == "end_turn" and result.get("ok") is True:
            self.ended = True

        return self._with_budget(result)

    def _arguments_with_session(
        self,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        values = {} if arguments is None else dict(arguments)
        values["session_id"] = self.session_id
        return values

    def _error(self, message: str) -> dict[str, Any]:
        return self._with_budget({"ok": False, "error": message})

    def _with_budget(self, result: dict[str, Any]) -> dict[str, Any]:
        data = dict(result)
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
