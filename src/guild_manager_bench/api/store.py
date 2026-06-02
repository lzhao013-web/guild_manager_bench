from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from guild_manager_bench.game.state import GameDefinition
from guild_manager_bench.runtime.session import GameSession


class SessionNotFoundError(KeyError):
    """指定会话不存在。"""


@dataclass(slots=True)
class SessionStore:
    """进程内会话存储。"""

    definition: GameDefinition
    _sessions: dict[str, GameSession] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def create(self, session_id: str | None = None) -> GameSession:
        """创建新会话。"""

        with self._lock:
            session = GameSession(
                definition=self.definition,
                session_id=session_id or uuid4().hex,
            )
            if session.session_id in self._sessions:
                raise ValueError(f"duplicate session id: {session.session_id}")
            self._sessions[session.session_id] = session
            return session

    def restore(self, data: dict) -> GameSession:
        """从导出数据恢复会话。"""

        with self._lock:
            session = GameSession.from_export(self.definition, data)
            if session.session_id in self._sessions:
                session.session_id = uuid4().hex
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> GameSession:
        """按 id 读取会话。"""

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session

    def list(self) -> list[GameSession]:
        """列出当前进程内所有会话。"""

        with self._lock:
            return list(self._sessions.values())
