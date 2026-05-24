from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from guild_manager_bench.runtime.events import SessionEvent, event_to_dict


def write_events_jsonl(path: str | Path, events: Iterable[SessionEvent]) -> None:
    """把事件序列写成 JSONL 日志。"""

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event_to_dict(event), ensure_ascii=False))
            file.write("\n")


def read_events_jsonl(path: str | Path) -> list[dict]:
    """读取 JSONL 事件日志，供页面或调试工具回放。"""

    events = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                events.append(json.loads(line))
    return events

