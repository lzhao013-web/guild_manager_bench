"""读取由种子和回合确定的经营刷新，不模拟玩家决策或终局评测。"""

from dataclasses import asdict
from typing import Any

from guild_manager_bench.game.engine import spawn_monsters, spawn_recruit_candidates
from guild_manager_bench.game.state import MATERIAL_NAMES, GameDefinition, GameState
from guild_manager_bench.runtime.observation import _skill_to_dict


FORECAST_SCHEMA = {
    "name": "get_forecast",
    "description": (
        "只读查询未来回合自然刷新的怪物（含未击败的固定挑战）或招募候选，不包含终局评测敌人。"
        "怪物显示未计经营天赋的基础奖励，返回的 future: 引用不可用于操作。"
    ),
    "parameters": {
        "type": "object", "required": ["kind", "start_turn"],
        "properties": {
            "kind": {"type": "string", "enum": ["monsters", "recruits"],
                     "description": "查询类别：monsters（怪物）或 recruits（招募候选）。"},
            "start_turn": {"type": "integer", "minimum": 1,
                           "description": "起始回合（必须大于当前回合）。"},
            "end_turn": {"type": "integer", "minimum": 1,
                         "description": "结束回合（默认等于 start_turn）。"},
            "name_contains": {"type": "string", "minLength": 1,
                              "description": "按显示名称或原型/职业 ID 子串筛选。"},
            "material": {"type": "string", "minLength": 1,
                         "description": "仅 monsters：按掉落材料中文名或 ID 筛选（如秘银碎片或 mithril_shard）。"},
            "tier": {"type": "string", "enum": ["normal", "elite", "boss", "challenge"],
                     "description": "仅 monsters：按普通/精英/首领/固定挑战筛选。"},
            "entry_index": {"type": "integer", "minimum": 1,
                            "description": "单回合刷新列表序号（从1开始，仅 start_turn=end_turn 时可用）。"},
            "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary",
                       "description": "summary 返回简表（最多10条/页），full 返回完整属性与技能（最多3条/页）。"},
            "offset": {"type": "integer", "minimum": 0, "default": 0,
                       "description": "分页偏移量，翻页时保持筛选条件不变并传入返回的 next_offset。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3,
                      "description": "每页条数上限（summary 最多10，full 最多3）。"},
        },
        "additionalProperties": False,
    },
}


def query_forecast(
    definition: GameDefinition, state: GameState, *, kind: str, start_turn: int,
    end_turn: int | None = None, name_contains: str | None = None,
    material: str | None = None, tier: str | None = None, entry_index: int | None = None,
    detail: str = "summary", offset: int = 0, limit: int = 3,
) -> dict[str, Any]:
    if kind not in ("monsters", "recruits"):
        raise ValueError("kind 必须是 monsters 或 recruits")
    if detail not in ("summary", "full"):
        raise ValueError("detail 必须是 summary 或 full")
    _integer(start_turn, "start_turn", state.turn + 1, state.max_turns)
    end_turn = start_turn if end_turn is None else end_turn
    _integer(end_turn, "end_turn", start_turn, state.max_turns)
    _integer(offset, "offset", 0)
    _integer(limit, "limit", 1, 3 if detail == "full" else 10)
    if entry_index is not None:
        _integer(entry_index, "entry_index", 1)
        if start_turn != end_turn:
            raise ValueError("entry_index 只能用于单回合查询")
    if name_contains is not None and (not isinstance(name_contains, str) or not name_contains.strip()):
        raise ValueError("name_contains 必须是非空字符串")
    if kind != "monsters" and (material is not None or tier is not None):
        raise ValueError("material 和 tier 仅用于怪物查询")
    if tier is not None and tier not in ("normal", "elite", "boss", "challenge"):
        raise ValueError("tier 必须是 normal、elite、boss 或 challenge")
    if material is not None:
        if not isinstance(material, str):
            raise ValueError("material 必须是材料名称或 ID")
        material = {name: key for key, name in MATERIAL_NAMES.items()}.get(material, material)
        if material not in MATERIAL_NAMES:
            raise ValueError("未知材料，请使用材料中文名称或 ID")

    matches = []
    needle = name_contains.strip().casefold() if name_contains is not None else None
    for turn in range(start_turn, end_turn + 1):
        entries = (spawn_monsters(definition, turn, defeated_challenge_ids=state.defeated_challenge_ids) if kind == "monsters"
                   else spawn_recruit_candidates(definition, turn))
        for index, entry in enumerate(entries, 1):
            if entry_index is not None and index != entry_index:
                continue
            template_id = entry.archetype_id if kind == "monsters" else entry.template_id
            if needle is not None and needle not in entry.name.casefold() and needle not in template_id.casefold():
                continue
            if kind == "monsters":
                if tier is not None and entry.tier != tier:
                    continue
                if material is not None and entry.reward.materials.get(material, 0) <= 0:
                    continue
            matches.append((turn, index, entry))

    page = matches[offset:offset + limit]
    items = []
    for turn, index, entry in page:
        item = {
            "forecast_ref": f"future:T{turn}:{kind}:{index}",
            "turn": turn, "entry_index": index, "name": entry.name,
        }
        if kind == "monsters":
            item.update({
                "archetype_id": entry.archetype_id, "tier": entry.tier,
                "base_reward": {"gold": entry.reward.gold, "experience": entry.reward.experience,
                                "materials": dict(entry.reward.materials)},
            })
            if entry.tier == "challenge":
                item["availability_condition"] = "仅在此前尚未击败该挑战时存在，属性固定，奖励只能领取一次"
            if detail == "full":
                item.update({"stats": asdict(entry.stats),
                             "skills": [_skill_to_dict(skill) for skill in entry.skills]})
        else:
            item.update({"template_id": entry.template_id, "recruit_gold": entry.recruit_gold})
            if detail == "full":
                item.update({
                    "base_stats": asdict(entry.base_stats),
                    "stat_growth_per_level": asdict(entry.stat_growth_per_level),
                    "skills": [_skill_to_dict(skill) for skill in entry.skills],
                    "level_skill_unlocks": [
                        {"level": unlock.level, "skills": [_skill_to_dict(skill) for skill in unlock.skills]}
                        for unlock in entry.level_skill_unlocks
                    ],
                })
        items.append(item)
    return {
        "ok": True, "forecast_only": True, "current_turn": state.turn,
        "kind": kind, "start_turn": start_turn, "end_turn": end_turn, "detail": detail,
        "items": items, "total_matches": len(matches), "offset": offset, "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
    }


def _integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} 必须是大于等于 {minimum} 的整数")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} 不能超过 {maximum}")
