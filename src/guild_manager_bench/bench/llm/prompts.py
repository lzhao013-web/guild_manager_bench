from __future__ import annotations

from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.formatting import skill_summary_lines, tactics_summary_lines
from guild_manager_bench.bench.llm.refs import (
    build_numeric_refs,
    display_ref,
)
from guild_manager_bench.game.state import MATERIAL_NAMES

DEFAULT_OBJECTIVE = (
    "在有限回合内最大化获取资源、培养队伍，使全队在最后一回合结束时的终局评测中获得最高的终局战力评分（rank_score）。"
    "终局评测会让全队以满HP/MP迎战多波不同特性的高难度敌人，并自动选择最佳1v1配对计算总分。"
)


def build_system_prompt(
    objective: str = DEFAULT_OBJECTIVE,
    max_tool_calls: int = 20,
    max_battle_preview_per_turn: int = 3,
    turn_recovery_rules: Mapping[str, Any] | None = None,
    talent_tree_enabled: bool = False,
    tactics_enabled: bool = False,
    battle_log_enabled: bool = False,
    forecast_enabled: bool = False,
    challenge_rules: Mapping[str, Any] | None = None,
    monster_refresh_rules: Mapping[str, Any] | None = None,
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

    lines = [
            "你正在进行 Guild Manager Bench。",
            f"目标：{objective}",
            "",
            "回合流程：每回合可调用查询工具获取详情，或调用准备工具调整队伍；确认本回合所有准备操作完成后，调用 end_turn 提交讨伐列表并结束回合。",
            "战斗形式：战斗是确定性的自动1V1，没有队员间协作。经营讨伐使用角色当前HP/MP（当前HP为0的角色无法出战），每位角色每回合最多讨伐一个怪物，每个怪物每回合也最多被讨伐一次。",
            "行动与速度：双方行动条从0开始，按速度（SPD）累积，达到100即可行动，行动后扣除100。因此速度（SPD）影响出手频率，不只是先后手。双方同时达到行动条件时，依次比较当前行动条、速度（SPD），仍相同则冒险者先动。",
            "行动结算：确定行动者后，先结算其身上的持续伤害、治疗和回魔。若仍存活，再检查主动技能。技能触发条件基于此时的HP/MP判定，判定通过后才扣除本次技能的MP消耗。",
            "技能选择：每次行动按列表顺序检查主动技能，仅释放首个满足触发条件、MP充足且次数未耗尽的技能；无可用技能则进行普攻。普通主动技能会替代本次普攻，即使技能只有治疗或增益效果。“即时”主动技能释放后会立即追加一次普攻，不会继续释放其他主动技能，也不免除其MP消耗。被动技能不参与排序，满足条件时动态生效。",
            "属性与伤害：角色面板属性已包含等级、装备和公会升级的直接加成，不包含战斗中的被动与状态加成。升级、穿脱装备或购买公会升级改变最大HP/MP时，保持已损失的HP/MP差值不变（即上限增加多少，当前值同步增加多少，不会直接回满）。战斗中的攻击、防御、速度按“（面板属性＋固定加成之和）×各倍率之积”计算并向下取整。普攻伤害为 max(1, 攻击－防御)，按普攻伤害倍率结算的技能在扣防后乘算（向下取整且最低1点），无视防御伤害另按技能描述计算。",
            "计数与持续时间：技能条件中的“行动序号”是单场战斗双方累计行动次数；状态持续时间则按状态持有者自己的行动次数计算，两者不同。双方累计达到1000次行动仍未分胜负，判为平局。",
            "战后处理：经营讨伐只有胜利才获得奖励，战斗后的剩余HP/MP保留。战败不会永久失去角色。回合末全体角色按恢复规则恢复，包括未出战和战败者。战斗状态及“每场一次”的技能计数不跨场保留。",
            "回合末恢复公式（不超过各自最大HP/MP）："
            f"HP回复 = {hp_recovery} + 最大HP×{hp_percent} + 战后回血属性；"
            f"MP回复 = {mp_recovery} + 最大MP×{mp_percent} + 战后回魔属性。",
            "刷新机制：每回合结束时，当前未讨伐的怪物和未招募的候选冒险者均不保留，下回合会刷新全新列表。",
            f"调用预算：每回合最多允许 {max_tool_calls} 次非 end_turn 工具调用{bp_limit_text}；所有查询、战斗预览、准备动作以及失败的调用均会消耗预算，请合理规划工具调用。",
            "对象编号：角色、怪物、配方、升级、候选和装备使用列表左侧的数字 id（同回合已有编号固定，新对象编号见工具返回）。技能配置使用技能字符串 ID，战斗日志使用结果中的 battle_id。",
            "工具返回：每次调用均返回执行状态（成功/失败）、剩余预算及结果摘要。准备工具仅返回变更摘要，完整列表详情需通过对应的查询工具查看。",
    ]
    if talent_tree_enabled:
        lines = [line.replace("若仍存活，再检查主动技能。", "若仍存活，先结算自动回复，再检查普通主动技能。") for line in lines]
        lines.extend([
            "自动回复：自身行动开始、持续状态结算后检查，每场一次，不占普通主动选择、不额外普攻。可停用，不能排序。",
            "全局天赋：可通过 get_upgrades 查询并由 purchase_upgrade 解锁天赋节点。购买天赋消耗金币、材料和经验池经验（材料与制作装备共用，经验池与角色升级共用）。节点需满足全部前置且与互斥节点不可兼得，购买后不可退款或洗点。经营类天赋仅对购买后的制作或讨伐生效，当前查询显示的配方费用与怪物奖励均已计入已生效的加成。",
        ])
    if tactics_enabled:
        lines.append(
            "技能配置：可通过 configure_tactics 调整角色主动技能的释放优先级或停用指定主动技能。"
            "配置跨回合生效，并同时作用于日常讨伐、战斗预览与终局评测；被动技能及技能本身的触发条件、"
            "MP消耗和次数限制不受影响。"
        )
    if battle_log_enabled:
        lines.append(
            "战斗记录：按预览或实战结算返回的 battle_id 调用 get_battle_log，可查当轮预览和上轮实战。"
            "查询不重新模拟，只占普通工具预算。记录不随后续调整改变。"
        )
    if forecast_enabled:
        lines.append(
            "未来刷新：未来各回合刷新的怪物和招募候选由随机种子与回合数唯一确定，不受玩家操作影响。"
            "可通过 get_forecast 提前查询未来经营回合的刷新列表（怪物展示基础奖励，未计经营天赋加成；"
            "该查询不包含终局评测敌人或评分种子）。"
        )
    if challenge_rules is not None:
        start = challenge_rules["start_turn"]
        last_hunt = challenge_rules["last_hunt_turn"]
        lines.append(
            f"固定挑战：T{start}起额外出现三个带【挑战】标识的单位，最多可挑战至T{last_hunt}。"
            "普通怪物和候选仍照常刷新，但未击败的挑战跨回合保留，属性不变。"
            "挑战失败后敌方下次恢复满HP/MP，己方保留实际消耗。挑战占正常讨伐名额，"
            "胜利后永久移除，只奖励一份对应核心，不奖励金币或经验。可提前用 get_forecast 的 tier=challenge 查询属性与基础奖励，"
            "未来存在以此前未击败为条件。"
            "特殊核心可通过 get_crafting 查看全部职业印记配方，再用 craft_equipment 制作、equip_item 装入 ultimate 槽，"
            "从而获得终极技能。每份核心只能制作一个印记，每人最多一个。"
            "制作后不能拆回核心，但印记可卸下、转移给符合职业限制的队友，解雇也不会销毁印记。"
            f"T{last_hunt + 1}是最终整备回合，不发生讨伐，可使用上一回合奖励制作、培养、穿装和配置技能，最后调用 end_turn(hunts=[]) 完成评测。"
        )
        lines = [line.replace(
            "当前未讨伐的怪物和未招募的候选冒险者均不保留",
            "当前未讨伐的普通怪物和未招募的候选冒险者均不保留",
        ).replace("未来各回合刷新的怪物和招募候选", "未来各回合刷新的普通怪物和招募候选") for line in lines]
    if monster_refresh_rules is not None:
        prices = "、".join(f"T{p['from_turn']}起{p['gold']}金币" for p in monster_refresh_rules["base_prices"])
        lines.extend([
            "手动刷新：preview_monster_refresh 只预览，refresh_monsters 扣费刷新。",
            f"首刷基础价：{prices}。先计天赋折扣，再按当回合实刷次数翻倍。新回合次数重置。",
            "锁定普通怪物不收费，不跨回合。固定挑战自动保留。手动刷新不影响后续自然刷新和招募，不增加讨伐次数。",
            "预览和实际刷新各占一次普通调用，不占战斗预览额度。最终整备回合不能刷新。",
        ])
        lines = [line.replace("未来各回合刷新的", "未来各回合自然刷新的") for line in lines]
    return "\n".join(lines)


def build_endgame_system_prompt(turn: int, max_turns: int) -> str:
    """构建终局阶段提示词。"""

    return _endgame_notice(turn, max_turns)


def build_turn_prompt(
    observation: Mapping[str, Any],
    *,
    previous_turn_event: Mapping[str, Any] | None = None,
    memo_entries: Sequence[str] = (),
    endgame_start_turn: int | None = None,
    turn_start_rank_score: float | None = None,
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
        if turn_start_rank_score is not None:
            parts.append(f"当前终局评分：{turn_start_rank_score:.2f}（本回合开始时的 rank_score）。")

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
        "终局评测会评估全队对多类高难度敌人的战斗表现（每场以满HP/MP开始，并自动选择最佳一一配对）。"
        "单场得分以胜负为主，同时计入敌方损失HP的比例和己方剩余HP的比例——"
        "同样获胜也可能得到不同分数，战败或平局也可能获得部分分数；终局战力评分（rank_score）汇总不同难度下的表现。"
        "请兼顾剩余回合的经营与终局阵容强化。你可使用 preview_team_power 试算一组准备动作对 rank_score 的影响"
        "（分差相对调用前的真实状态计算，不是本回合开始时的状态；传入 operations=[] 只查询当前 rank_score；"
        "试算不会实际执行方案，采纳后仍需逐个调用真实工具）。"
    )


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
    if observation.get("final_preparation_only") and observation["turn"] == observation["max_turns"]:
        lines.append("当前为最终整备：没有讨伐，完成准备后提交 end_turn(hunts=[])。")
    if "defeated_challenge_ids" in observation:
        lines.append(f"固定挑战已击败 {len(observation['defeated_challenge_ids'])}/3，详细属性与奖励可按需查询。")
    refresh = observation.get("monster_refresh")
    if isinstance(refresh, Mapping):
        if refresh["available"]:
            lines.append(f"刷新：本回合{refresh['refreshes_used']}次，下次{refresh['next_gold_cost']}金币。")
        else:
            lines.append("刷新：本回合不可用。")
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
            f"战后回血 {stats.get('recovery', 0)} 战后回魔 {stats.get('mp_recovery', 0)} "
            f"装备 {equip_text}"
        )
        _append_skill_lines_zh(lines, adventurer.get("skills"))
        lines.extend(f"  {line}" for line in tactics_summary_lines(adventurer.get("tactics")))
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
            + (f"，battle_id={battle['battle_id']}" if "battle_id" in battle else "")
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
    values = skill_summary_lines(skills)
    if not values:
        return "无"
    return "；".join(values)


def _append_skill_lines_zh(lines: list[str], skills: Any) -> None:
    values = skill_summary_lines(skills)
    if not values:
        lines.append("  技能: 无")
        return
    lines.append("  技能:")
    for value in values:
        lines.append(f"    - {value}")


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
        ("recovery", "战后回血"),
        ("mp_recovery", "战后回魔"),
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
