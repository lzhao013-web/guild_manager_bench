from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from guild_manager_bench.bench.llm import (
    AnthropicMessagesAgent,
    AnthropicMessagesConfig,
    LlmRunConfig,
    OpenAIChatCompletionsAgent,
    OpenAIChatCompletionsConfig,
    run_llm_game,
)
from guild_manager_bench.bench.llm.prompts import DEFAULT_OBJECTIVE


LLM_ARCHIVE_DIR = Path("runs/llm")
DEFAULT_LLM_DEBUG_TIMEOUT = 180.0
LLM_DEBUG_EVENT_SEND_TIMEOUT = 180.0


def llm_debug_router(
    data_dir: str | Path,
    *,
    data_source: Mapping[str, Any] | None = None,
) -> APIRouter:
    """创建 LLM 调试 WebSocket 路由。"""

    router = APIRouter()

    @router.websocket("/ws/llm/debug")
    async def debug_llm(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            request = await websocket.receive_json()
            if request.get("type") != "start":
                await websocket.send_json(
                    {"type": "debug_error", "error": "first message must be start"}
                )
                return

            loop = asyncio.get_running_loop()

            def emit(event: dict[str, Any]) -> None:
                future = asyncio.run_coroutine_threadsafe(websocket.send_json(event), loop)
                future.result(timeout=LLM_DEBUG_EVENT_SEND_TIMEOUT)

            def run() -> None:
                payload = request.get("payload", {})
                if not isinstance(payload, Mapping):
                    payload = {}
                agent = _debug_agent(payload)
                config = LlmRunConfig(
                    objective=_string_value(payload, "objective", DEFAULT_OBJECTIVE),
                    max_tool_calls_per_turn=_int_value(payload, "max_tool_calls_per_turn", 20),
                    max_empty_responses=_int_value(payload, "max_empty_responses", 2),
                    max_end_turn_attempts=_int_value(payload, "max_end_turn_attempts", 3),
                    max_model_steps_per_turn=_int_value(payload, "max_model_steps_per_turn", 50),
                    max_turn_retries=_int_value(payload, "max_turn_retries", 2),
                    game_seed=_optional_int(payload, "game_seed"),
                    scoring_seed=_optional_int(payload, "scoring_seed"),
                )
                resume_archive_dir = _resume_archive_dir(payload)
                run_llm_game(
                    agent,
                    data_dir=data_dir,
                    session_id=_optional_string(payload, "session_id"),
                    config=config,
                    event_sink=emit,
                    resume_archive_dir=resume_archive_dir,
                    data_source=data_source,
                )

            await asyncio.to_thread(run)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            try:
                await websocket.send_json({"type": "debug_error", "error": str(exc)})
            except RuntimeError:
                return

    return router


def _debug_agent(
    payload: Mapping[str, Any],
) -> OpenAIChatCompletionsAgent | AnthropicMessagesAgent:
    provider = _string_value(payload, "provider", "openai").lower()
    common = {
        "model": _optional_string(payload, "model"),
        "api_key": _optional_string(payload, "api_key"),
        "base_url": _optional_string(payload, "base_url"),
        "timeout": _float_value(payload, "timeout", DEFAULT_LLM_DEBUG_TIMEOUT),
        "temperature": _optional_float(payload, "temperature"),
        "max_tokens": _optional_int(payload, "max_tokens"),
    }
    if provider == "anthropic":
        return AnthropicMessagesAgent(
            AnthropicMessagesConfig.from_env(
                **common,
                thinking=_optional_bool(payload, "thinking"),
                effort=_optional_string(payload, "thinking_effort"),
            )
        )
    if provider == "openai":
        return OpenAIChatCompletionsAgent(
            OpenAIChatCompletionsConfig.from_env(
                **common,
                reasoning_effort=_optional_string(payload, "reasoning_effort"),
            )
        )
    raise ValueError(f"unsupported LLM provider: {provider}")


def _resume_archive_dir(payload: Mapping[str, Any]) -> Path | None:
    run_id = _optional_string(payload, "resume_run_id")
    if run_id is None:
        return None
    if any(char in run_id for char in "\\/"):
        raise ValueError("invalid resume_run_id")
    resolved_base = LLM_ARCHIVE_DIR.resolve()
    resolved_path = (resolved_base / run_id).resolve()
    if resolved_path.parent != resolved_base:
        raise ValueError("invalid resume_run_id")
    replay_path = resolved_path / "replay.json"
    if not replay_path.exists():
        raise ValueError(f"replay not found for run: {run_id}")
    return resolved_path


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_value(payload: Mapping[str, Any], key: str, default: str) -> str:
    return _optional_string(payload, key) or default


def _int_value(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        return int(value)
    return default


def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    return _int_value(payload, key, 0)


def _float_value(payload: Mapping[str, Any], key: str, default: float) -> float:
    value = payload.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return default


def _optional_float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    return _float_value(payload, key, 0.0)


def _optional_bool(payload: Mapping[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    raise ValueError(f"{key} must be a boolean")
