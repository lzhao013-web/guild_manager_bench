"""升级与配方的事实筛选及模型可见简表，不做推荐或排序。"""
from __future__ import annotations

from typing import Any, Mapping

from guild_manager_bench.bench.llm.formatting import class_restriction_text, modifier_summary, skill_summary_lines
from guild_manager_bench.bench.llm.refs import display_ref
from guild_manager_bench.game.equipment import EQUIPMENT_SLOTS
from guild_manager_bench.game.state import MATERIAL_NAMES

BRANCHES = {"common": "公共", "economy": "经营", "martial": "武备", "survival": "生存", "arcane": "奥术"}
SLOTS = {"main_hand": "主手", "off_hand": "副手", "two_hand": "双手", "boots": "靴子",
         "helmet": "头盔", "armor": "护甲", "accessory": "饰品", "ultimate": "终极槽"}


def catalog_properties(kind: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary",
                   "description": "summary 返回简表，保留精确效果与缺口。full 展开全部属性和前置。默认不筛掉未来目标。"},
        "ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"type": "integer", "minimum": 1},
                "description": "只查询指定数字 ID，筛选不改变原编号。"},
        "name_contains": {"type": "string", "minLength": 1, "description": "按名称或内部 ID 子串筛选。"},
    }
    if kind == "upgrades":
        properties.update({
            "branch": {"type": "string", "enum": list(BRANCHES)},
            "tier": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": ["available", "locked", "purchased"],
                       "description": "available 可购买，locked 未购但条件不足，purchased 已购买。不传则全部返回。"},
        })
    else:
        properties.update({
            "slot": {"type": "string", "enum": sorted(EQUIPMENT_SLOTS)},
            "class_id": {"type": "string", "minLength": 1,
                         "description": "职业 template_id，包含该职业专属和通用装备。"},
            "craftable_only": {"type": "boolean", "default": False,
                               "description": "true 仅显示当前可制作配方。不传则保留资源不足的未来目标。"},
        })
    return properties


def _select(items, id_key, *, detail, ids, name_contains):
    if detail not in ("summary", "full"):
        raise ValueError("detail 必须是 summary 或 full")
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
            raise ValueError("ids 必须是非空 ID 列表")
        if len(set(ids)) != len(ids):
            raise ValueError("ids 不可重复")
        unknown = set(ids) - {item[id_key] for item in items}
        if unknown:
            raise ValueError(f"ID 不存在：{', '.join(sorted(unknown))}")
    if name_contains is not None and (not isinstance(name_contains, str) or not name_contains.strip()):
        raise ValueError("name_contains 必须是非空字符串")
    needle = name_contains.casefold() if name_contains is not None else None
    return [item for item in items
            if (ids is None or item[id_key] in ids)
            and (needle is None or needle in item["name"].casefold() or needle in item[id_key].casefold())]


def filter_upgrades(items, *, detail="full", ids=None, name_contains=None, branch=None, tier=None, status=None):
    selected = _select(items, "upgrade_id", detail=detail, ids=ids, name_contains=name_contains)
    if branch is not None and branch not in BRANCHES:
        raise ValueError("branch 必须是 common、economy、martial、survival 或 arcane")
    if tier is not None and (type(tier) is not int or tier < 1):
        raise ValueError("tier 必须是正整数")
    if status is not None and status not in ("available", "locked", "purchased"):
        raise ValueError("status 必须是 available、locked 或 purchased")
    def state(item):
        return "purchased" if item["unlocked"] else "available" if item["can_purchase"] else "locked"
    return [item for item in selected
            if (branch is None or item.get("branch", "common") == branch)
            and (tier is None or item.get("tier", 1) == tier)
            and (status is None or state(item) == status)]


def filter_recipes(items, *, detail="full", ids=None, name_contains=None, slot=None,
                   class_id=None, craftable_only=False, class_ids: set[str]):
    selected = _select(items, "recipe_id", detail=detail, ids=ids, name_contains=name_contains)
    if slot is not None and slot not in EQUIPMENT_SLOTS:
        raise ValueError("slot 不是有效装备槽位")
    if class_id is not None and (not isinstance(class_id, str) or class_id not in class_ids):
        raise ValueError(f"class_id 不是有效职业 template_id。可选值：{', '.join(sorted(class_ids)) or '无'}")
    if type(craftable_only) is not bool:
        raise ValueError("craftable_only 必须是布尔值")
    return [item for item in selected
            if (slot is None or item["output_slot"] == slot)
            and (class_id is None or not item["output_allowed_classes"] or class_id in item["output_allowed_classes"])
            and (not craftable_only or item["can_craft"])]


def _materials(values: Mapping[str, int]) -> str:
    return "、".join(f"{MATERIAL_NAMES.get(k, k)}{v}" for k, v in values.items() if v)


def _cost(item, *, experience=False):
    parts = [f"{item['gold_cost']}金"]
    if experience and item.get("experience_cost"):
        parts.append(f"{item['experience_cost']}经验")
    materials = _materials(item.get("material_costs", {}))
    if materials:
        parts.append(materials)
    return "、".join(parts)


def _upgrade_names(ids, result, refs):
    return "、".join(f"{display_ref(refs, 'upgrade', uid)}{result['upgrade_names'][uid]}" for uid in ids)


def _missing(item, result, refs):
    parts = []
    for key, value in item["missing"].items():
        if key == "already_unlocked":
            continue
        if key == "required_upgrade_ids":
            parts.append("前置未购 " + _upgrade_names(value, result, refs))
        elif key == "exclusive_upgrade_ids":
            parts.append("已购互斥 " + _upgrade_names(value, result, refs))
        elif key == "materials":
            parts.append(_materials(value))
        elif key == "gold":
            parts.append(f"金币{value}")
        elif key == "experience":
            parts.append(f"经验{value}")
        else:
            # 配方缺口的材料直接以 material_id 为键。
            parts.append(f"{MATERIAL_NAMES.get(key, key)}{value}")
    return "。".join(parts)


def recipe_summary_lines(result: Mapping[str, Any], refs) -> list[str]:
    items = result["crafting_recipes"]
    lines = [f"制作简表：{len(items)}项。成本为当前实际价（已计折扣）。"]
    for item in items:
        tags = [SLOTS[item["output_slot"]], class_restriction_text(
                    item["output_allowed_classes"], item["output_allowed_class_names"]) or "通用",
                "可制作" if item["can_craft"] else "条件不足"]
        lines.append(f"- {display_ref(refs, 'recipe', item['recipe_id'])} {item['output_name']}〔{'·'.join(tags)}〕")
        stats = modifier_summary(item["output_stats"])
        lines.append(f"  {stats + '。' if stats else ''}成本：{_cost(item)}")
        lines.extend(f"  {text}" for text in skill_summary_lines(item["output_skills"], compact=True))
        missing = _missing(item, result, refs)
        if missing:
            lines.append(f"  缺口：{missing}")
    return lines


def upgrade_summary_lines(result: Mapping[str, Any], refs) -> list[str]:
    items = result["global_upgrades"]
    lines = [f"升级简表：{len(items)}项。已满足前置省略，经验加成相加并逐场向下取整，金币加成合并后向下取整。"]
    purchased = [item for item in items if item["unlocked"]]
    if purchased:
        lines.append("已购：" + _upgrade_names([item["upgrade_id"] for item in purchased], result, refs))
    for item in items:
        if item["unlocked"]:
            continue
        tags = [BRANCHES.get(item.get("branch", "common")), f"{item.get('tier', 1)}阶",
                "可购买" if item["can_purchase"] else "条件不足"]
        lines.append(f"- {display_ref(refs, 'upgrade', item['upgrade_id'])} {item['name']}〔{'·'.join(tags)}〕")
        effects = [modifier_summary(item["stats"])]
        if item["party_size_bonus"]:
            effects.append(f"队伍上限+{item['party_size_bonus']}")
        for field, label in (
            ("craft_gold_discount_percent", "后续制作金币费用降低"),
            ("hunt_experience_bonus_percent", "后续胜利讨伐经验增加"),
            ("hunt_gold_bonus_percent", "后续胜利讨伐金币增加"),
            ("elite_boss_gold_bonus_percent", "精英、首领胜利金币额外增加"),
            ("monster_refresh_discount_percent", "刷新基础价降低"),
        ):
            if item.get(field):
                effects.append(f"{label}{item[field]}%")
        effects = [effect for effect in effects if effect]
        if effects:
            lines.append("  效果：" + "、".join(effects))
        if item.get("craft_gold_discount_percent") or item.get("monster_refresh_discount_percent"):
            lines.append("  金币费用向上取整，制作材料不变，刷新折扣合并后再按次数翻倍。")
        lines.extend(f"  {text}" for text in skill_summary_lines(item["skills"], compact=True))
        lines.append(f"  成本：{_cost(item, experience=True)}")
        if item.get("exclusive_upgrade_ids"):
            lines.append("  购买将互斥：" + _upgrade_names(item["exclusive_upgrade_ids"], result, refs))
        missing = _missing(item, result, refs)
        if missing:
            lines.append(f"  缺口：{missing}")
    return lines
