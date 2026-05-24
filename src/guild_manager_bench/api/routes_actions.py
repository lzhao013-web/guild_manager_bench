from __future__ import annotations

from fastapi import APIRouter, HTTPException

from guild_manager_bench.api.schemas import ActionRequest
from guild_manager_bench.api.store import SessionNotFoundError, SessionStore
from guild_manager_bench.api.websocket import SessionHub
from guild_manager_bench.game.engine import GameError
from guild_manager_bench.runtime.action_codec import (
    ActionCodecError,
    decode_end_turn_action,
    decode_preparation_action,
)
from guild_manager_bench.runtime.events import event_to_dict


def actions_router(store: SessionStore, hub: SessionHub) -> APIRouter:
    """创建会话动作路由。"""

    router = APIRouter(prefix="/api/sessions", tags=["actions"])

    @router.post("/{session_id}/actions")
    async def submit_action(session_id: str, request: ActionRequest):
        payload = request.to_payload()
        try:
            session = store.get(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

        try:
            if payload["type"] == "end_turn":
                result, event = session.end_turn(decode_end_turn_action(payload))
                response = {
                    "event": event_to_dict(event),
                    "turn_result": {
                        "battles": event.payload["battles"],
                        "crafted_equipment_ids": list(result.crafted_equipment_ids),
                        "purchased_upgrade_ids": list(result.purchased_upgrade_ids),
                    },
                    "observation": session.observation(),
                }
            else:
                event = session.apply_preparation(decode_preparation_action(payload))
                response = {
                    "event": event_to_dict(event),
                    "observation": session.observation(),
                }
        except (ActionCodecError, GameError, ValueError, TypeError) as exc:
            event = session.reject_action(payload, str(exc))
            response = {
                "event": event_to_dict(event),
                "observation": session.observation(),
            }
            await hub.broadcast(session_id, response)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await hub.broadcast(session_id, response)
        return response

    return router

