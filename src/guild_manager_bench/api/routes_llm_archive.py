from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from guild_manager_bench.bench.llm.runner import rebuild_replay_observations


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
                    "has_observations": _replay_has_observations(replay),
                }
            )
        return {"runs": runs}

    @router.get("/{run_id}/replay")
    async def get_replay(
        run_id: str,
        rebuild: bool = Query(False, description="Rebuild observation snapshots for legacy replays"),
    ) -> dict[str, Any]:
        replay_path = _run_directory(archive_dir, run_id) / "replay.json"
        if not replay_path.exists():
            raise HTTPException(status_code=404, detail="replay not found")
        replay = _read_json(replay_path)
        if not isinstance(replay, dict):
            raise HTTPException(status_code=500, detail="replay must be a JSON object")
        if rebuild:
            data = replay.get("data")
            preset = data.get("preset", "default") if isinstance(data, dict) else "default"
            data_dir = f"data/presets/{preset}"
            replay = rebuild_replay_observations(replay, data_dir=data_dir)
        return replay

    @router.post("/{run_id}/rebuild")
    async def rebuild_observations(
        run_id: str,
        preset: str | None = Query(None, description="Preset name (default, full, etc.)"),
    ) -> dict[str, Any]:
        """为旧 replay 重建 observation 快照（仅缺少快照时需要）。"""
        replay_path = _run_directory(archive_dir, run_id) / "replay.json"
        if not replay_path.exists():
            raise HTTPException(status_code=404, detail="replay not found")
        replay = _read_json(replay_path)
        if not isinstance(replay, dict):
            raise HTTPException(status_code=500, detail="replay must be a JSON object")
        data = replay.get("data")
        preset_name = preset or (
            data.get("preset", "default") if isinstance(data, dict) else "default"
        )
        data_dir = f"data/presets/{preset_name}"
        try:
            result = rebuild_replay_observations(replay, data_dir=data_dir)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result

    return router


def _run_directory(base_dir: Path, run_id: str) -> Path:
    if not run_id or any(char in run_id for char in "\\/"):
        raise HTTPException(status_code=400, detail="invalid run id")
    resolved_base = base_dir.resolve()
    resolved_path = (resolved_base / run_id).resolve()
    if resolved_path.parent != resolved_base:
        raise HTTPException(status_code=400, detail="invalid run id")
    return resolved_path


def _replay_has_observations(replay: dict[str, Any]) -> bool:
    turns = replay.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    first = turns[0]
    return isinstance(first, dict) and first.get("observation_before") is not None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid JSON: {path.name}") from exc
