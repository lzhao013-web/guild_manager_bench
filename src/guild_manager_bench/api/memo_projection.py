"""从结构化事件投影备忘状态，不解析提示词或执行游戏。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

MemoSeed = Callable[[int, bool], dict[str, Any]]


def memo_write(event: dict[str, Any]) -> dict[str, Any] | None:
    if event["type"] != "tool_result" or event.get("name") != "write_memo":
        return None
    result = event.get("result") or {}
    if result.get("ok") is not True:
        return None
    memo = result.get("memo")
    if not isinstance(memo, dict):
        return {"state": "unavailable"}
    # 冻结版是逐条追加且下一回合消费，不能当成开发版的持续覆盖式备忘。
    if "count" in memo:
        return {"state": "legacy"}
    if not isinstance(memo.get("content"), str) or not isinstance(memo.get("updated_turn"), int):
        return {"state": "unavailable"}
    return {"state": "saved", "content": memo["content"], "updated_turn": memo["updated_turn"]} if memo["content"] else {
        "state": "empty", "updated_turn": memo["updated_turn"]}


class MemoProjection:
    def __init__(self, seed: MemoSeed | None = None) -> None:
        self.current: dict[str, Any] = {"state": "unavailable"}
        self.initial = self.current
        self.before = self.current
        self.committed: dict[int, dict[str, Any]] = {}
        self.seed = seed
        self.started = False

    def before_turn(self, turn: int, finished: bool = False) -> dict[str, Any]:
        cutoff = turn + int(finished)
        earlier = [key for key in self.committed if key < cutoff]
        return self.committed[max(earlier)] if earlier else self.initial

    def accept(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        if kind in {"run_started", "turn_started"} and not self.started:
            turn = event.get("turn", (event.get("observation") or {}).get("turn"))
            if turn == 1:
                self.current = self.initial = {"state": "empty", "updated_turn": None}
            self.started = True
        if kind == "run_resumed":
            restored = event.get("restored_observation") or {}
            turn = restored.get("turn")
            if turn is not None:
                finished = restored.get("finished") is True
                if self.seed is not None:
                    self.initial = self.seed(turn, finished)
                    self.seed = None
                self.current = self.before_turn(turn, finished)
                self.committed = {key: value for key, value in self.committed.items() if key < turn + int(finished)}
                self.before = self.current
        elif kind == "turn_started":
            self.before = self.current
        elif kind in {"turn_failed", "turn_retry"}:
            self.current = self.before
        elif kind == "turn_scored":
            self.committed[event["turn"]] = self.current
        else:
            written = memo_write(event)
            if written is not None:
                self.current = written
