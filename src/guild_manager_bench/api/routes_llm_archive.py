from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


def llm_archive_router(base_dir: str | Path = "runs/llm") -> APIRouter:
    """创建 LLM run 归档读取路由。"""

    router = APIRouter(prefix="/api/llm/runs", tags=["llm"])
    archive_dir = Path(base_dir)

    @router.get("")
    async def list_runs() -> dict[str, Any]:
        if not archive_dir.exists():
            return {"runs": []}
        runs = []
        for directory in sorted(
            (path for path in archive_dir.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        ):
            replay_path = directory / "replay.json"
            if not replay_path.exists():
                continue
            replay = _read_json(replay_path)
            if not isinstance(replay, dict):
                continue
            data = replay.get("data")
            data = data if isinstance(data, dict) else {}
            runs.append(
                {
                    "run_id": directory.name,
                    "created_at": replay.get("created_at"),
                    "session_id": replay.get("session_id"),
                    "status": replay.get("status"),
                    "failure_reason": replay.get("failure_reason"),
                    "turns": len(replay.get("turns", [])),
                    "preset": data.get("preset"),
                    "data_hash": data.get("data_hash"),
                }
            )
        return {"runs": runs}

    @router.get("/{run_id}/replay")
    async def get_replay(run_id: str) -> dict[str, Any]:
        replay_path = _run_directory(archive_dir, run_id) / "replay.json"
        if not replay_path.exists():
            raise HTTPException(status_code=404, detail="replay not found")
        replay = _read_json(replay_path)
        if not isinstance(replay, dict):
            raise HTTPException(status_code=500, detail="replay must be a JSON object")
        return replay

    return router


def _run_directory(base_dir: Path, run_id: str) -> Path:
    if not run_id or any(char in run_id for char in "\\/"):
        raise HTTPException(status_code=400, detail="invalid run id")
    resolved_base = base_dir.resolve()
    resolved_path = (resolved_base / run_id).resolve()
    if resolved_path.parent != resolved_base:
        raise HTTPException(status_code=400, detail="invalid run id")
    return resolved_path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid JSON: {path.name}") from exc
