from __future__ import annotations

from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.refs import (
    build_numeric_refs,
    display_ref,
)
from guild_manager_bench.game.state import MATERIAL_NAMES

DEFAULT_OBJECTIVE = (
    "最大程度地获取资源、加强冒险者，以使你的队伍的每个队员在回合限制达到上限时，获得最高的终局战力。每个队员的终局战力评分之和，就是你的最终得分。"
)


def build_system_prompt(
    objective: str = DEFAULT_OBJECTIVE,
    max_tool_calls: int = 20,
    max_battle_preview_per_turn: int = 3,
    turn_recovery_rules: Mapping[str, Any] | None = None,
) -> str:
    """构建系统提示词（静态游戏规则，跨回合不变，适合 LLM prompt caching）。"""

    bp_limit_text = (
        f"；preview_battle 最多 {max_battle_preview_per_turn} 次/回合"
        if max_battle_preview_per_turn
        else ""
    )

    rules = turn_recovery_rules if isinstance(turn_recovery_rules, Mapping) else {}
    hp_recovery = int(rules.get("hp", 0))
    mp_recovery = int(rules.get("mp", 0))
    hp_percent = _percent(rules.get("hp_percent"))
    mp_percent = _percent(rules.get("mp_percent"))

    return "\n".join(
        [
            "你正在进行 Guild Manager Bench。",
            f"目标：{objective}",
            "",
            "回合流程：可调用查询工具读取信息，也可调用动作工具执行各种操作；保证只有当你觉得本回合要做的事情都做完了，才通过 end_turn 提交讨伐列表并结束回合。",
            "战斗提示：冒险者讨伐怪物的战斗是完全的1V1自动战斗，无法干预战斗过程，也没有团队协作，但可以通过调整冒险者的装备、技能来影响战斗结果。每位冒险者当回合只能讨伐一个怪物，请谨慎选择。",
            "战斗机制：SPD 决定出手频率，而非仅决定先后手。 例如，SPD 80 vs SPD 20 → 高SPD方每行动约4次，低SPD方才行动1次。普通攻击伤害 = max(1, ATK - DEF)",
            "技能相关：主动技能满足条件时会在角色行动时按优先级触发，会覆盖普通攻击，带有”即时”tag的技能不会覆盖普通攻击；被动技能会在满足条件时持续生效；技能效果可能包括伤害、治疗、状态等，具体信息请参考状态和冒险者信息中的技能描述。",
            "回复机制：每回合战斗结束后全体冒险者回复HP和MP，额外回复等同于其恢复属性（recovery）值的HP，战斗中技能也可提供治疗、百分比治疗、MP恢复和持续回复状态。"
            f"HP回复 = {hp_recovery} + 最大HP×{hp_percent} + 恢复属性；"
            f"MP回复 = {mp_recovery} + 最大MP×{mp_percent} + 回魔属性。",
            "刷新机制：回合结束后，当前的可讨伐的怪物和可招募的冒险者都会刷新成其他的。",
            f"每回合最多允许 {max_tool_calls} 次非 end_turn 工具调用{bp_limit_text}；每一次工具调用，包括查询、战斗预览、实际操作和失败的调用均会消耗使用次数，请考虑工具调用的预算，谨慎决定和规划要使用的工具。",
            "调用工具使用的所有对象 id 都使用列表左侧的数字 id。",
            "工具会返回 成功/失败、预算 和结果摘要。动作工具返回变更摘要；详细信息分散在各个查询工具中。",
        ]
    )


def build_turn_prompt(
    observation: Mapping[str, Any],
    *,
    previous_turn_event: Mapping[str, Any] | None = None,
    memo_entries: Sequence[str] = (),
    endgame_start_turn: int | None = None,
) -> str:
    """构造单个游戏回合开始时给 LLM 的用户提示词（动态信息）。"""

    parts = [
        _previous_turn_summary(previous_turn_event),
        "",
        _memo_summary(memo_entries),
        "",
        _state_summary(observation),
    ]

    if endgame_start_turn is not None and observation["turn"] >= endgame_start_turn:
        turns_remaining = observation["max_turns"] - observation["turn"]
        parts.append("")
        parts.append(_endgame_warning(observation["turn"], turns_remaining, observation["max_turns"]))

    return "\n".join(parts).strip()


def _memo_summary(memo_entries: Sequence[str]) -> str:
    values = [
        entry.strip()
        for entry in memo_entries
        if isinstance(entry, str) and entry.strip()
    ]
    if not values:
        return "备忘录：无。（提示：本回合的思考不会带到下回合，如有跨回合计划请用 write_memo 记录。备忘录只持续1回合。）"
    lines = ["备忘录："]
    lines.extend(f"- {entry}" for entry in values)
    return "\n".join(lines)


def _endgame_warning(turn: int, turns_remaining: int, max_turns: int) -> str:
    return _endgame_notice(turn, max_turns, turns_remaining=turns_remaining)


def _endgame_notice(
    turn: int,
    max_turns: int,
    *,
    turns_remaining: int | None = None,
) -> str:
    if turns_remaining is None:
        turns_remaining = max_turns - turn
    return (
        f"现在是终局阶段：当前回合 {turn}/{max_turns}，剩余 {turns_remaining} 回合。"
        "游戏即将结束，请在继续运营的同时优先最大化队伍终局战力评分 rank_score；"
        "可使用 preview_team_power 预览当前队伍 rank_score 和每个冒险者的贡献占比。"
    )


def build_endgame_system_prompt(turn: int, max_turns: int) -> str:
    """构建终局阶段专用 system 提示词。"""

    return _endgame_notice(turn, max_turns)


def _state_summary(observation: Mapping[str, Any]) -> str:
    adventurers = observation.get("adventurers", ())
    monsters = observation.get("monsters", ())
    refs = build_numeric_refs(observation)
    lines = [
        f"当前回合：{observation['turn']}/{observation['max_turns']}",
        f"资源：金币 {observation['gold']}，经验池 {observation['experience_pool']}，材料 {_mapping_text(observation['materials'])}",
    ]
    # Experience rules if available
    exp_rules = observation.get("experience_rules")
    if isinstance(exp_rules, Mapping):
        base = exp_rules.get("base_required_experience")
        growth_per = exp_rules.get("required_experience_growth")
        max_level = exp_rules.get("max_level")
        if base is not None and growth_per is not None:
            lines.append(f"升级需求：{base}+{growth_per}/级；最高等级 {max_level}")
    lines.append(_turn_overview(observation))
    lines.append("冒险者：")
    for adventurer in adventurers:
        resources = adventurer["resources"]
        stats = adventurer["effective_stats"]
        exp_text = _experience_text(adventurer)
        growth_text = _stat_modifier_text(adventurer.get("stat_growth_per_level"))
        # Use equipment_slots for slot names if available
        equip_slots = adventurer.get("equipment_slots")
        if equip_slots:
            equip_text = _equipment_slots_text(refs, equip_slots)
        else:
            equipment = adventurer.get("equipment") or ()
            equip_text = _equipment_refs_text(refs, equipment)
        lines.append(
            "- "
            f"{display_ref(refs, 'adventurer', adventurer['adventurer_id'])} "
            f"{adventurer['name']} "
            f"Lv{adventurer['level']} "
            f"EXP {exp_text} "
            f"每级属性成长 {growth_text} "
            f"HP {resources['current_hp']}/{stats['hp']} "
            f"MP {resources['current_mp']}/{stats['mp']} "
            f"攻击 {stats['attack']} 防御 {stats['defense']} 速度 {stats['speed']} "
            f"恢复 {stats.get('recovery', 0)} 回魔 {stats.get('mp_recovery', 0)} "
            f"装备 {equip_text}"
        )
        _append_skill_lines_zh(lines, adventurer.get("skills"))
        level_unlocks = adventurer.get("level_skill_unlocks")
        if level_unlocks:
            lines.append(f"  升级可学会技能 {_level_skill_unlocks_text(level_unlocks)}")

    lines.append("当前怪物：")
    for monster in monsters:
        stats = monster["stats"]
        reward = monster["reward"]
        lines.append(
            "- "
            f"{display_ref(refs, 'monster', monster['monster_id'])} "
            f"{monster['name']} "
            f"HP {stats['hp']} MP {stats.get('mp', 0)} "
            f"攻击 {stats['attack']} 防御 {stats['defense']} 速度 {stats['speed']} "
            f"奖励 金币={reward['gold']} 经验={reward['experience']} 掉落材料={_mapping_text(reward['materials'])}"
        )
        _append_skill_lines_zh(lines, monster.get("skills"))

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
    recruit_candidates = [
        candidate
        for candidate in _sequence(observation.get("recruit_candidates"))
        if isinstance(candidate, Mapping)
    ]
    return (
        "本回合概览："
        f"可制作配方 {len(craftable)} 个，"
        f"可购买升级 {len(purchasable)} 个，"
        f"空闲装备 {len(free_equipment)} 件，"
        f"招募候选 {len(recruit_candidates)} 个，"
        f"当前队伍人数/队伍人数上限 {observation.get('party_size')}/{observation.get('party_size_limit')}，"
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
            f"{_battle_participant_name(battle, 'adventurer')} vs {_battle_participant_name(battle, 'monster')}：{result}，"
            f"奖励 金币={reward['gold']} 经验={reward['experience']} 掉落材料={_mapping_text(reward['materials'])}"
        )
    return "\n".join(lines)


def _equipment_slots_text(
    refs: Mapping[str, Mapping[str, int]],
    slots: Any,
) -> str:
    values = []
    for slot_data in _sequence(slots):
        if not isinstance(slot_data, Mapping):
            continue
        item = slot_data.get("item")
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        ref = display_ref(refs, "equipment", item.get("instance_id"))
        slot_name = item.get("slot") or slot_data.get("slot")
        if isinstance(name, str) and name:
            values.append(f"{name}(id={ref}, {slot_name})")
        elif ref:
            values.append(f"id={ref}({slot_name})")
    return ", ".join(values) if values else "无"


def _equipment_refs_text(
    refs: Mapping[str, Mapping[str, int]],
    equipment: Sequence[Mapping[str, Any]],
) -> str:
    values = []
    for item in equipment:
        if not isinstance(item, Mapping):
            continue
        instance_id = item.get("instance_id")
        ref = display_ref(refs, "equipment", instance_id)
        name = item.get("name")
        if isinstance(name, str) and name:
            values.append(f"{name}(id={ref})")
        elif ref:
            values.append(f"id={ref}")
    return ", ".join(values) if values else "无"


def _level_skill_unlocks_text(unlocks: Any) -> str:
    values = [
        unlock
        for unlock in _sequence(unlocks)
        if isinstance(unlock, Mapping) and not unlock.get("unlocked")
    ]
    if not values:
        return "无"
    parts = []
    for unlock in values:
        skills = _skill_summary_zh(unlock.get("skills"))
        parts.append(f"Lv{unlock.get('level')} {skills}")
    return "; ".join(parts)


def _battle_participant_name(battle: Mapping[str, Any], role: str) -> str:
    name = battle.get(f"{role}_name")
    if isinstance(name, str) and name:
        return name
    value = battle.get(f"{role}_id")
    return str(value)


def _skill_summary_zh(skills: Any) -> str:
    values = [
        skill
        for skill in _sequence(skills)
        if isinstance(skill, Mapping)
    ]
    if not values:
        return "无"
    return "；".join(_skill_text_zh(skill) for skill in values)


def _append_skill_lines_zh(lines: list[str], skills: Any) -> None:
    values = [
        skill
        for skill in _sequence(skills)
        if isinstance(skill, Mapping)
    ]
    if not values:
        lines.append("  技能: 无")
        return
    lines.append("  技能:")
    for skill in values:
        lines.append(f"    - {_skill_text_zh(skill)}")


def _skill_text_zh(skill: Mapping[str, Any]) -> str:
    parts = [
        str(skill.get("name") or skill.get("skill_id") or "技能"),
        _skill_kind_text(skill.get("kind")),
    ]
    if skill.get("free"):
        parts.append("即时")
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
    if condition_type == "self_mp_pct_lte":
        return f"自身MP<={_percent(value.get('value'))}"
    if condition_type == "self_mp_pct_gte":
        return f"自身MP>={_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_lte":
        return f"目标MP<={_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_gte":
        return f"目标MP>={_percent(value.get('value'))}"
    if condition_type == "action_index_lte":
        return f"行动序号<={_number(value.get('value'))}"
    if condition_type == "action_index_gte":
        return f"行动序号>={_number(value.get('value'))}"
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
    if effect_type == "heal_percent":
        return f"治疗 {_percent(value)}最大HP"
    if effect_type == "mp_restore":
        return f"{target_text}恢复MP {_number(value)}"
    if effect_type == "damage_bonus":
        return f"伤害+{_number(value)}"
    if effect_type == "true_damage":
        return f"真实伤害 {_number(value)}"
    if effect_type == "self_damage":
        return f"自身受伤 {_number(value)}"
    if effect_type == "apply_status":
        status = effect.get("status")
        if isinstance(status, Mapping):
            return f"施加状态 {_status_text_zh(status)}"
        return "施加状态"
    if effect_type == "stat_bonus":
        return f"{target_text}{stat_text}+{_number(value)}"
    if effect_type == "stat_multiplier":
        return f"{target_text}{stat_text}倍率 {_number(value)}"
    if stat is not None:
        return f"{effect_type}:{stat_text}:{_number(value)}"
    return f"{effect_type}:{_number(value)}"


def _status_text_zh(status: Mapping[str, Any]) -> str:
    name = str(status.get("name") or status.get("status_id") or "状态")
    duration = status.get("duration")
    polarity = _status_polarity_text(status.get("polarity"))
    effects = [
        _effect_text_zh(effect)
        for effect in _sequence(status.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effect_text = "，".join(effect for effect in effects if effect)
    parts = [name]
    if isinstance(duration, int | float):
        parts.append(f"{_number(duration)}行动")
    if polarity:
        parts.append(polarity)
    if effect_text:
        parts.append(effect_text)
    return " ".join(parts)


def _status_polarity_text(value: Any) -> str:
    labels = {
        "positive": "正面",
        "negative": "负面",
        "neutral": "",
    }
    return labels.get(value, str(value or ""))


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
        "mp_recovery": "回魔",
    }
    return labels.get(value, str(value))


def _mapping_text(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "{}"
    return "{" + ", ".join(f"{MATERIAL_NAMES.get(key, key)}: {value}" for key, value in value.items()) + "}"


def _stat_modifier_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "无"
    parts = []
    for key, label in (
        ("hp", "HP"),
        ("mp", "MP"),
        ("attack", "攻击"),
        ("defense", "防御"),
        ("speed", "速度"),
        ("recovery", "恢复"),
        ("mp_recovery", "回魔"),
    ):
        amount = value.get(key, 0)
        if isinstance(amount, int | float) and amount:
            parts.append(f"{label}+{_number(amount)}")
    return " ".join(parts) if parts else "无"


def _turn_recovery_hp(observation: Mapping[str, Any]) -> int:
    rules = observation.get("turn_recovery_rules", {})
    if isinstance(rules, Mapping):
        return int(rules.get("hp", 0))
    return 0


def _turn_recovery_mp(observation: Mapping[str, Any]) -> int:
    rules = observation.get("turn_recovery_rules", {})
    if isinstance(rules, Mapping):
        return int(rules.get("mp", 0))
    return 0


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
