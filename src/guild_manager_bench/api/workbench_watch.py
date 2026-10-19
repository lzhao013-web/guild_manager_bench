"""只读观战投影：增量索引回合边界，按需读取单次尝试，不执行游戏逻辑。"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from guild_manager_bench.api.trajectory_index import TrajectoryIndex


class WatchJournal:
    def __init__(self, path: Path, *, archived: bool = False) -> None:
        self.path = path
        self.archived = archived
        self.trajectory = TrajectoryIndex()
        self.cursor = 0
        self._lock = Lock()
        self.attempts: list[dict[str, Any]] = []
        # 只缓存最近查看的四次尝试，不能把所有回合的完整状态长期留在内存。
        self._pages: OrderedDict[int, tuple[int, list[dict[str, Any]]]] = OrderedDict()

    def history(self) -> dict[str, Any]:
        with self._lock:
            self._index()
            return {"cursor": self.cursor, "entries": [dict(row) for row in self.trajectory.rows]}

    def _event(self, record: dict[str, Any]) -> dict[str, Any] | None:
        if self.archived:
            return record["event"] if record.get("record_type") == "event" else None
        return record["event"]

    def _at(self, record: dict[str, Any]) -> str:
        return record["created_at"] if self.archived else record["at"]

    def read(self, attempt: int | None = None) -> dict[str, Any]:
        with self._lock:
            return self._read(attempt)

    def _read(self, attempt: int | None) -> dict[str, Any]:
        self._index()
        selected = next((a for a in self.attempts if a["id"] == attempt), None) if attempt is not None else (self.attempts[-1] if self.attempts else None)
        if attempt is not None and selected is None:
            raise ValueError("找不到该回合尝试")
        records = self._records(selected) if selected else []
        return {
            "cursor": self.cursor,
            "attempts": [{k: v for k, v in a.items() if k not in {"start", "end"}} for a in self.attempts],
            "selected": selected["id"] if selected else None,
            "records": records,
        }

    def _index(self) -> None:
        if not self.path.exists():
            return  # 排队中的任务尚未创建事件文件。
        with self.path.open("rb") as stream:
            stream.seek(self.cursor)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break  # 工作进程正在追加这一行，下次从完整边界重读。
                record = json.loads(line)
                e = self._event(record)
                self.cursor = stream.tell()
                if e is None:
                    continue
                kind = e["type"]
                at = self._at(record)
                # agent 初始化也可能直接失败，保留回合开始之前的诊断事件。
                if kind == "turn_started" or not self.attempts:
                    turn = e.get("turn") if kind == "turn_started" else None
                    ordinal = 1 + sum(a["turn"] == turn for a in self.attempts)
                    if self.attempts and self.attempts[-1]["turn"] is None:
                        self.attempts[-1]["status"] = "completed"
                    self.attempts.append({
                        "id": self.cursor, "turn": turn, "attempt": ordinal,
                        "status": "running", "score": None, "actions": 0,
                        "at": at, "updated_at": at,
                        "start": start, "end": self.cursor,
                    })
                current = self.attempts[-1]
                current.update(end=self.cursor, updated_at=at)
                self.trajectory.accept(e, attempt=current["id"], cursor=self.cursor, at=at)
                if kind == "run_resumed":
                    for old in self.attempts:
                        if self.trajectory.attempt_states.get(old["id"]) == "rolled_back":
                            old["status"] = "rolled_back"
                    current["score"] = e.get("last_rank_score")
                elif kind == "tool_result":
                    current["actions"] += 1
                elif kind == "turn_completed":
                    current["status"] = "scoring"
                elif kind == "turn_scored":
                    current.update(status="completed", score=e["rank_score"])
                elif kind in {"turn_failed", "worker_failed", "run_failed"} and current["status"] != "completed":
                    current["status"] = "failed"
                elif kind == "turn_retry":
                    current["status"] = "rolled_back"
                elif kind == "worker_stopped" and current["status"] != "completed":
                    current["status"] = "interrupted"

    def _records(self, selected: dict[str, Any]) -> list[dict[str, Any]]:
        key, end = selected["id"], selected["end"]
        cached = self._pages.get(key)
        if cached is not None and cached[0] == end:
            self._pages.move_to_end(key)
            return cached[1]
        records: list[dict[str, Any]] = []
        # 流式碎片折叠为每次调用的两条文本记录，避免成千上万的 DOM 节点。
        deltas: dict[str, dict[str, Any]] = {}
        with self.path.open("rb") as stream:
            stream.seek(selected["start"])
            while stream.tell() < end:
                after = stream.tell()
                raw = json.loads(stream.readline())
                event = self._event(raw)
                if event is None:
                    continue
                kind = event["type"]
                record = {"at": self._at(raw), "after": after, "cursor": stream.tell()}
                if kind == "model_request":
                    deltas = {}
                    event = {k: event[k] for k in ("type", "turn", "step", "tool_names", "message_count") if k in event}
                elif kind in {"model_delta", "model_reasoning_delta"}:
                    if kind in deltas:
                        item = deltas[kind]
                        item["event"]["text"] += event.get("text", "")
                        item.update(cursor=record["cursor"], at=record["at"])
                        continue
                    event = {"type": kind, "text": event.get("text", "")}
                    record["event"] = event
                    deltas[kind] = record
                elif kind == "tool_call_delta":
                    continue  # 最终工具参数位于 tool_call / model_response。
                elif kind == "turn_completed":
                    event = {"type": kind, "turn": selected["turn"]}
                elif kind in {"run_completed", "run_failed"}:
                    event = {"type": kind, "failure_reason": event["run"].get("failure_reason"), "score": event["run"].get("score")}
                else:
                    event = {k: v for k, v in event.items() if k not in {"raw", "config", "data"}}
                    if kind == "model_response":
                        deltas = {}
                records.append({**record, "event": event} if kind not in {"model_delta", "model_reasoning_delta"} else record)
        self._pages[key] = (end, records)
        self._pages.move_to_end(key)
        while len(self._pages) > 4:
            self._pages.popitem(last=False)
        return records
