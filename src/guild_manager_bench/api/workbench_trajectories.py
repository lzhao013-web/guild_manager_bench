"""整局轨迹读取。归档日志跨续跑追加；缺失时只报告现存执行阶段的覆盖范围。"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from guild_manager_bench.api.workbench_watch import WatchJournal


class TrajectoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._journals: OrderedDict[Path, WatchJournal] = OrderedDict()

    def raw(self, path: Path, *, archived: bool, after: int) -> dict[str, Any]:
        if after >= path.stat().st_size:
            raise ValueError("事件位置超出当前日志范围")
        with path.open("rb") as stream:
            stream.seek(after)
            line = stream.readline()
            cursor = stream.tell()
        # 解析前释放句柄，避免阻挡 Windows 下归档写者的替换操作。
        record = json.loads(line)
        if archived and record.get("record_type") != "event":
            raise ValueError("所选位置不是运行事件")
        return {"event": record["event"], "at": record["created_at"] if archived else record["at"], "cursor": cursor}

    def read(self, path: Path, *, archived: bool, attempt: int | None = None) -> dict[str, Any]:
        with self._lock:
            journal = self._journals.get(path)
            if journal is None:
                journal = WatchJournal(path, archived=archived)
                self._journals[path] = journal
            self._journals.move_to_end(path)
            while len(self._journals) > 4:
                self._journals.popitem(last=False)
            return journal.history() if attempt is None else journal.read(attempt)
