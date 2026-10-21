"""曲线只读投影。沿用 CLI 的结算口径，不重建局面、不评分、不回写存档。"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from guild_manager_bench.batch_curves import extract_curves
from guild_manager_bench.api.archive_io import read_archive_json
from guild_manager_bench.api.curve_milestones import extract_milestones


class CurveStore:
    def __init__(self) -> None:
        self._lock = Lock()
        # 只保存轻量投影。最多保留近期四组四路比较，不缓存原始回放。
        self._cache: OrderedDict[Path, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()

    def read(self, path: Path) -> dict[str, Any]:
        with self._lock:
            cached = self._cache.get(path)
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if cached and cached[0] == signature:
                    self._cache.move_to_end(path)
                    return dict(cached[1])
                replay = read_archive_json(path)
                if not isinstance(replay, dict):
                    raise ValueError("曲线要求回放对象")
                curve = extract_curves(replay, path.parent.name)
                if not isinstance(curve.max_turns, int) or isinstance(curve.max_turns, bool) or curve.max_turns < 1:
                    raise ValueError("回合上限必须是正整数")
                if any(not isinstance(point.turn, int) or isinstance(point.turn, bool) or not 1 <= point.turn <= curve.max_turns for point in curve.points):
                    raise ValueError("曲线回合必须位于已记录上限内")
                data = replay.get("data")
                data = data if isinstance(data, dict) else {}
                config = replay.get("config")
                config = config if isinstance(config, dict) else {}
                payload = {
                    "run_id": path.parent.name,
                    "status": curve.status,
                    "max_turns": curve.max_turns,
                    "points": [asdict(point) for point in curve.points],
                    "milestones": extract_milestones(replay, {point.turn for point in curve.points}),
                    "conditions": {**{key: data.get(key) for key in (
                        "preset", "data_hash", "benchmark_version", "protocol_hash", "settings_hash",
                    )}, **{key: config.get(key) for key in ("game_seed", "scoring_seed")}},
                    "updated_at": replay.get("updated_at"),
                    "warning": "跨版本执行：历史与当前规则不完全一致，不作同版本标准比较" if data.get("protocol_status") == "mixed" else None,
                }
                self._cache[path] = (signature, payload)
                self._cache.move_to_end(path)
                while len(self._cache) > 16:
                    self._cache.popitem(last=False)
                return dict(payload)
            except (OSError, ValueError, KeyError, TypeError):
                # 原子更新可能遇到 Windows 共享冲突，旧回放也可能不符合提取契约。
                # 已成功读取的数据可继续查看，但必须明确表示已过时。
                if cached:
                    return {**cached[1], "warning": (cached[1]["warning"] + "。" if cached[1]["warning"] else "") + "当前回放暂不可读或格式不符，保留上次成功读取的曲线"}
                raise


def empty_curves(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": job.get("archive_id"), "status": job["status"], "max_turns": job.get("max_turns"),
        "points": [], "milestones": [], "conditions": {}, "updated_at": None,
        "warning": "尚未生成回放存档，等待首个可读取的结算记录" if job["status"] in {"queued", "starting", "running", "stopping"} else "该 run 未生成回放存档，无法提取曲线",
    }
