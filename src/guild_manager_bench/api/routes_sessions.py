from __future__ import annotations

from fastapi import APIRouter, HTTPException

from guild_manager_bench.api.schemas import CreateSessionRequest
from guild_manager_bench.api.store import SessionNotFoundError, SessionStore
from guild_manager_bench.runtime.events import event_to_dict


def sessions_router(store: SessionStore) -> APIRouter:
    """创建会话读取和创建路由。"""

    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    @router.post("")
    async def create_session(request: CreateSessionRequest | None = None):
        try:
            session = store.create(None if request is None else request.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "session_id": session.session_id,
            "observation": session.observation(),
            "events": [event_to_dict(event) for event in session.events],
        }

    @router.get("")
    async def list_sessions():
        return {
            "sessions": [
                {
                    "session_id": session.session_id,
                    "turn": session.observation()["turn"],
                    "finished": session.observation()["finished"],
                }
                for session in store.list()
            ]
        }

    @router.get("/{session_id}")
    async def get_session(session_id: str):
        try:
            session = store.get(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return {
            "session_id": session.session_id,
            "observation": session.observation(),
            "events": [event_to_dict(event) for event in session.events],
        }

    @router.get("/{session_id}/events")
    async def get_session_events(session_id: str):
        try:
            session = store.get(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return {"events": [event_to_dict(event) for event in session.events]}

    return router

