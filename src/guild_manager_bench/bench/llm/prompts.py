from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFAULT_OBJECTIVE = "最大化本局最终表现：尽量赢得战斗、提升队伍、积累有价值资源。"


def build_turn_prompt(
    observation: Mapping[str, Any],
    *,
    objective: str = DEFAULT_OBJECTIVE,
    max_tool_calls: int,
    previous_turn_event: Mapping[str, Any] | None = None,
) -> str:
    """构造单个游戏回合开始时给 LLM 的提示词。"""

    return "\n".join(
        [
            "你正在进行 Guild Manager Bench。",
            f"目标：{objective}",
            "",
            "回合流程：准备阶段可调用查询工具读取信息，也可调用动作工具执行准备操作；回合结束通过 end_turn 提交讨伐列表。",
            f"工具预算：本回合最多允许 {max_tool_calls} 次非 end_turn 工具调用；查询、动作和非法调用均计入预算。预算耗尽后只接受 end_turn。",
            "工具结果：返回 OK/FAIL、budget 和结果摘要。动作工具返回变更摘要，不自动附带完整状态；get_observation 返回当前完整可见状态。",
            "",
            _state_summary(observation),
            "",
            _previous_turn_summary(previous_turn_event),
        ]
    ).strip()


def _state_summary(observation: Mapping[str, Any]) -> str:
    adventurers = observation.get("adventurers", ())
    monsters = observation.get("monsters", ())
    scoring = observation.get("scoring", {})
    if not isinstance(scoring, Mapping):
        scoring = {}
    lines = [
        f"当前回合：{observation['turn']}/{observation['max_turns']}",
        f"随机种子：游戏 {observation.get('seed')}，评分 {scoring.get('seed')}",
        f"资源：金币 {observation['gold']}，经验池 {observation['experience_pool']}，材料 {_mapping_text(observation['materials'])}",
        _turn_overview(observation),
        "冒险者：",
    ]
    for adventurer in adventurers:
        resources = adventurer["resources"]
        stats = adventurer["effective_stats"]
        equipment = [
            item["instance_id"]
            for item in adventurer["equipment"]
        ]
        exp_text = _experience_text(adventurer)
        lines.append(
            "- "
            f"{adventurer['adventurer_id']} {adventurer['name']} "
            f"Lv{adventurer['level']} "
            f"EXP {exp_text} "
            f"HP {resources['current_hp']}/{stats['hp']} "
            f"MP {resources['current_mp']}/{stats['mp']} "
            f"攻击 {stats['attack']} 防御 {stats['defense']} 速度 {stats['speed']} "
            f"装备 {equipment or '无'} "
            f"技能 {_skill_summary_zh(adventurer.get('skills'))}"
        )

    lines.append("当前怪物：")
    for monster in monsters:
        stats = monster["stats"]
        reward = monster["reward"]
        lines.append(
            "- "
            f"{monster['monster_id']} {monster['name']} "
            f"HP {stats['hp']} 攻击 {stats['attack']} 防御 {stats['defense']} 速度 {stats['speed']} "
            f"技能 {_skill_summary_zh(monster.get('skills'))} "
            f"奖励 金币={reward['gold']} 经验={reward['experience']} 材料={_mapping_text(reward['materials'])}"
        )

    return "\n".join(lines)


def _experience_text(adventurer: Mapping[str, Any]) -> str:
    current = adventurer.get("experience")
    next_level = adventurer.get("next_level")
    if not isinstance(next_level, Mapping):
        return str(current)
    if next_level.get("max_level"):
        return f"{current}/MAX"
    required = next_level.get("required")
    return f"{current}/{required}"


def _turn_overview(observation: Mapping[str, Any]) -> str:
    craftable = [
        recipe
        for recipe in _sequence(observation.get("crafting_recipes"))
        if isinstance(recipe, Mapping) and recipe.get("can_craft")
    ]
    purchasable = [
        upgrade
        for upgrade in _sequence(observation.get("global_upgrades"))
        if (
            isinstance(upgrade, Mapping)
            and upgrade.get("can_purchase")
            and not upgrade.get("unlocked")
        )
    ]
    free_equipment = [
        item
        for item in _sequence(observation.get("equipment_inventory"))
        if isinstance(item, Mapping) and not item.get("equipped_by")
    ]
    monsters = [
        monster
        for monster in _sequence(observation.get("monsters"))
        if isinstance(monster, Mapping)
    ]
    return (
        "本回合概览："
        f"可制作配方 {len(craftable)} 个，"
        f"可购买升级 {len(purchasable)} 个，"
        f"空闲装备 {len(free_equipment)} 件，"
        f"当前怪物 {len(monsters)} 个。"
    )


def _previous_turn_summary(previous_turn_event: Mapping[str, Any] | None) -> str:
    if previous_turn_event is None:
        return "上一回合：无，这是本局第一个回合。"

    payload = previous_turn_event.get("payload", previous_turn_event)
    lines = [f"上一回合：{payload['summary']}"]
    for battle in payload.get("battles", ()):
        result = "胜" if battle["won"] else "负"
        reward = battle["reward"]
        lines.append(
            "- "
            f"{battle['adventurer_id']} vs {battle['monster_id']}：{result}，"
            f"奖励 金币={reward['gold']} 经验={reward['experience']} 材料={_mapping_text(reward['materials'])}"
        )
    return "\n".join(lines)


def _skill_summary_zh(skills: Any) -> str:
    values = [
        skill
        for skill in _sequence(skills)
        if isinstance(skill, Mapping)
    ]
    if not values:
        return "无"
    return "；".join(_skill_text_zh(skill) for skill in values)


def _skill_text_zh(skill: Mapping[str, Any]) -> str:
    parts = [
        str(skill.get("skill_id") or skill.get("name") or "skill"),
        _skill_kind_text(skill.get("kind")),
    ]
    mp_cost = skill.get("mp_cost")
    if isinstance(mp_cost, int | float) and mp_cost:
        parts.append(f"MP消耗 {mp_cost}")
    if skill.get("once_per_battle"):
        parts.append("每场一次")
    priority = skill.get("priority")
    if isinstance(priority, int | float) and priority:
        parts.append(f"优先级 {priority}")
    condition = _condition_text_zh(skill.get("condition"))
    if condition:
        parts.append(f"条件 {condition}")
    effects = [
        _effect_text_zh(effect)
        for effect in _sequence(skill.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effects = [effect for effect in effects if effect]
    if effects:
        parts.append("效果 " + "，".join(effects))
    return " ".join(parts)


def _skill_kind_text(value: Any) -> str:
    labels = {
        "active": "主动",
        "passive": "被动",
    }
    return labels.get(value, str(value or "技能"))


def _condition_text_zh(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    condition_type = value.get("type")
    if condition_type in (None, "always"):
        return "总是"
    if condition_type in {"all", "any"}:
        joiner = "且" if condition_type == "all" else "或"
        children = [
            _condition_text_zh(child)
            for child in _sequence(value.get("conditions"))
            if isinstance(child, Mapping)
        ]
        children = [child for child in children if child and child != "总是"]
        return joiner.join(children) if children else "总是"
    if condition_type == "self_hp_pct_lte":
        return f"自身HP<={_percent(value.get('value'))}"
    if condition_type == "self_hp_pct_gte":
        return f"自身HP>={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_lte":
        return f"目标HP<={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_gte":
        return f"目标HP>={_percent(value.get('value'))}"
    condition_value = value.get("value")
    raw = str(condition_type)
    return raw if condition_value is None else f"{raw}:{condition_value}"


def _effect_text_zh(effect: Mapping[str, Any]) -> str:
    effect_type = effect.get("type")
    value = effect.get("value")
    stat = effect.get("stat")
    target = effect.get("target")
    target_text = _effect_target_text(target)
    stat_text = _stat_text(stat)
    if effect_type == "damage_multiplier":
        return f"伤害倍率 {_number(value)}"
    if effect_type == "heal":
        return f"治疗 {_number(value)}"
    if effect_type == "stat_bonus":
        return f"{target_text}{stat_text}+{_number(value)}"
    if effect_type == "stat_multiplier":
        return f"{target_text}{stat_text}倍率 {_number(value)}"
    if stat is not None:
        return f"{effect_type}:{stat_text}:{_number(value)}"
    return f"{effect_type}:{_number(value)}"


def _effect_target_text(value: Any) -> str:
    labels = {
        None: "",
        "target": "",
        "self": "自身",
    }
    if value in labels:
        return labels[value]
    return f"{value}."


def _stat_text(value: Any) -> str:
    labels = {
        "hp": "HP",
        "mp": "MP",
        "attack": "攻击",
        "defense": "防御",
        "speed": "速度",
        "recovery": "恢复",
    }
    return labels.get(value, str(value))


def _mapping_text(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "{}"
    return "{" + ", ".join(f"{key}: {value}" for key, value in value.items()) + "}"


def _percent(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{round(value * 100)}%"
    return str(value)


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()
