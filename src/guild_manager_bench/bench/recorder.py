from __future__ import annotations

from pathlib import Path

from guild_manager_bench.runtime.replay import write_events_jsonl
from guild_manager_bench.runtime.session import GameSession


def record_session(path: str | Path, session: GameSession) -> None:
    """记录一个会话的事件日志。"""

    write_events_jsonl(path, session.events)

