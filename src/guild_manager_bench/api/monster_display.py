"""只读怪物显示分组。按原型解锁阶段分组，不改动观测、规则或归档。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from guild_manager_bench.game.presets import DataPreset


def monster_stages(preset: DataPreset | None) -> dict[str, str]:
    # 历史任务可能引用本机已移除的 preset。缺少显示元数据不应阻断观战。
    if preset is None:
        return {}
    path = preset.data_dir / "monsters.yaml"
    try:
        stamp = path.stat()
        return _read_stages(path, stamp.st_mtime_ns, stamp.st_size)
    except (OSError, yaml.YAMLError, ValueError):
        # 原型文件已移动或正在编辑时，只显示身份，不推断原型阶级。
        return {}


@lru_cache(maxsize=16)
def _read_stages(path: Path, modified: int, size: int) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("怪物原型数据必须是映射")
    result = {}
    for section in ("monsters", "challenges"):
        items = data.get(section, [])
        if not isinstance(items, list):
            raise ValueError("怪物原型必须是列表")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("怪物原型缺少 ID")
            turn = item.get("min_turn", 1)  # 与原型加载器的默认解锁回合一致。
            if type(turn) is not int or turn < 1:
                raise ValueError("原型解锁回合必须是正整数")
            # 数据表 Early / Mid / Late 分组：1、8、18 回合起解锁。
            # 24、26、28 回合新增的 Late variants 同属 T3。
            result[item["id"]] = "challenge" if section == "challenges" else (
                "t3" if turn >= 18 else "t2" if turn >= 8 else "t1"
            )
    return result
