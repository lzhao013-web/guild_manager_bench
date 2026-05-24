from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from guild_manager_bench.api.store import SessionNotFoundError, SessionStore


class SessionHub:
    """按会话分组的 WebSocket 广播器。"""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        """接受一个观看连接。"""

        await websocket.accept()
        self._connections[session_id].add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        """移除一个观看连接。"""

        self._connections[session_id].discard(websocket)

    async def broadcast(self, session_id: str, message: dict[str, Any]) -> None:
        """向指定会话的观看者广播消息。"""

        stale_connections: list[WebSocket] = []
        for websocket in list(self._connections.get(session_id, ())):
            try:
                await websocket.send_json(message)
            except RuntimeError:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.disconnect(session_id, websocket)


def websocket_router(store: SessionStore, hub: SessionHub) -> APIRouter:
    """创建 WebSocket 路由。"""

    router = APIRouter()

    @router.websocket("/ws/sessions/{session_id}")
    async def watch_session(websocket: WebSocket, session_id: str) -> None:
        try:
            session = store.get(session_id)
        except SessionNotFoundError:
            await websocket.close(code=4404)
            return

        await hub.connect(session_id, websocket)
        await websocket.send_json({"type": "snapshot", "observation": session.observation()})
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(session_id, websocket)

    return router

