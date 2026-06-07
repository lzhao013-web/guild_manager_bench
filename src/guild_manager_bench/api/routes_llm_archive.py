from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from guild_manager_bench.bench.llm.runner import rebuild_replay_observations
from guild_manager_bench.bench.replay_scoring import (
    with_rank_score_curve,
    with_rank_score_from_final_observation,
)


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
            score = replay.get("score")
            score = score if isinstance(score, dict) else {}
            stats = replay.get("stats")
            stats = stats if isinstance(stats, dict) else {}
            game_actions = stats.get("game_actions")
            game_actions = game_actions if isinstance(game_actions, dict) else {}
            agent = replay.get("agent")
            agent_config = (
                agent.get("config")
                if isinstance(agent, dict)
                else None
            ) or {}
            runs.append(
                {
                    "run_id": directory.name,
                    "created_at": replay.get("created_at"),
                    "session_id": replay.get("session_id"),
                    "status": replay.get("status"),
                    "failure_reason": replay.get("failure_reason"),
                    "turns": len(replay.get("turns", [])),
                    "preset": data.get("preset")
                    or _preset_from_data_dir(data.get("data_dir")),
                    "model": agent_config.get("model"),
                    "data_hash": data.get("data_hash"),
                    "score": score.get("score"),
                    "rank_score": score.get("rank_score"),
                    "adventurer_stats": game_actions.get("adventurer_stats") or [],
                    "has_observations": _replay_has_observations(replay),
                }
            )
        return {"runs": runs}

    @router.get("/{run_id}/replay")
    async def get_replay(run_id: str) -> dict[str, Any]:
        """读取 replay 文件，不做重建、重算或写回。"""

        replay_path = _replay_path(archive_dir, run_id)
        return _read_replay(replay_path)

    @router.post("/{run_id}/rescore")
    async def rescore_replay(run_id: str) -> dict[str, Any]:
        """显式补全 replay 的终局和回合 rank_score，并写回文件。"""

        replay_path = _replay_path(archive_dir, run_id)
        replay = _read_replay(replay_path)
        return _with_scores(replay, save_path=replay_path)

    @router.post("/{run_id}/rebuild")
    async def rebuild_observations(
        run_id: str,
        preset: str | None = Query(None, description="Preset name (default, full, etc.)"),
    ) -> dict[str, Any]:
        """为旧 replay 重建 observation 快照（仅缺少快照时需要）。"""
        replay_path = _replay_path(archive_dir, run_id)
        replay = _read_replay(replay_path)
        data_dir = _replay_data_dir(replay, preset)
        try:
            result = rebuild_replay_observations(replay, data_dir=data_dir)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _with_scores(result, save_path=replay_path)

    return router


def _replay_path(base_dir: Path, run_id: str) -> Path:
    replay_path = _run_directory(base_dir, run_id) / "replay.json"
    if not replay_path.exists():
        raise HTTPException(status_code=404, detail="找不到 replay，可能归档已移动或不存在")
    return replay_path


def _run_directory(base_dir: Path, run_id: str) -> Path:
    if not run_id or any(char in run_id for char in "\\/"):
        raise HTTPException(status_code=400, detail="无效的 run id")
    resolved_base = base_dir.resolve()
    resolved_path = (resolved_base / run_id).resolve()
    if resolved_path.parent != resolved_base:
        raise HTTPException(status_code=400, detail="无效的 run id")
    return resolved_path


def _replay_has_observations(replay: dict[str, Any]) -> bool:
    turns = replay.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    first = turns[0]
    return isinstance(first, dict) and first.get("observation_before") is not None


def _replay_data_dir(replay: dict[str, Any], preset: str | None) -> Path:
    if preset is not None:
        return _preset_data_dir(preset)

    data = replay.get("data")
    data = data if isinstance(data, dict) else {}
    archived_dir = data.get("data_dir")
    if isinstance(archived_dir, str) and archived_dir.strip():
        return Path(archived_dir)

    preset_name = data.get("preset")
    if not isinstance(preset_name, str) or not preset_name.strip():
        preset_name = "default"
    return _preset_data_dir(preset_name)


def _preset_data_dir(preset: str) -> Path:
    name = preset.strip()
    if not name or any(char in name for char in "\\/") or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid preset")
    return Path("data") / "presets" / name


def _preset_from_data_dir(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.parent.name != "presets":
        return None
    name = path.name.strip()
    if not name or any(char in name for char in "\\/") or name in {".", ".."}:
        return None
    return name


def _read_replay(path: Path) -> dict[str, Any]:
    replay = _read_json(path)
    if not isinstance(replay, dict):
        raise HTTPException(status_code=500, detail="replay must be a JSON object")
    return replay


def _with_scores(replay: dict[str, Any], *, save_path: Path) -> dict[str, Any]:
    try:
        scored = with_rank_score_from_final_observation(replay, save_path=save_path)
        return with_rank_score_curve(scored, save_path=save_path)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"无法补全 replay 分数: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"补全 replay 分数失败: {exc}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid JSON: {path.name}") from exc
