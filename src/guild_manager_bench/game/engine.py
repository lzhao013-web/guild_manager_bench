from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    DismissAction,
    EndTurnAction,
    EquipAction,
    HuntAction,
    PreparationAction,
    PurchaseUpgradeAction,
    RecruitAction,
    TurnAction,
    UnequipAction,
)
from guild_manager_bench.game.combat import CombatResult, Combatant, run_auto_battle
from guild_manager_bench.game.crafting import CraftingInventory, CraftingRecipe, craft_equipment
from guild_manager_bench.game.equipment import (
    EquippedItem,
    EquipmentInstance,
    EquipmentLoadout,
    EquipmentTemplate,
    apply_equipment_stats,
    combine_equipment_skills,
)
from guild_manager_bench.game.models import (
    CombatResources,
    CombatStatModifier,
    CombatStats,
    apply_stat_modifier,
    scale_combat_stats,
    scale_stat_modifier,
)
from guild_manager_bench.game.progression import add_experience, level_stat_modifier
from guild_manager_bench.game.state import (
    AdventurerState,
    GameDefinition,
    GameState,
    MonsterArchetype,
    MonsterSpawnRules,
    MonsterTierConfig,
    RecruitCandidate,
    RecruitableAdventurerTemplate,
    RecruitVariationConfig,
    RewardBundle,
    SkillTheme,
    SpawnedMonster,
)
from guild_manager_bench.game.skills import Skill
from guild_manager_bench.game.upgrades import (
    GlobalUpgrade,
    UpgradeInventory,
    apply_upgrade_stats,
    combine_party_size_bonus,
    combine_upgrade_skills,
    purchase_upgrade,
)


class GameError(ValueError):
    """游戏流程结算失败。"""


@dataclass(frozen=True, slots=True)
class BattleSettlement:
    """一场讨伐的结算结果。"""

    adventurer_id: str
    monster_id: str
    won: bool
    reward: RewardBundle
    combat_result: CombatResult


@dataclass(frozen=True, slots=True)
class TurnResult:
    """一次回合结算结果。"""

    state: GameState
    battles: tuple[BattleSettlement, ...]
    crafted_equipment_ids: tuple[str, ...]
    purchased_upgrade_ids: tuple[str, ...]
    recruited_adventurer_ids: tuple[str, ...]


def new_game(definition: GameDefinition) -> GameState:
    """根据游戏定义创建新游戏，并刷新第 1 回合怪物。"""

    _validate_definition(definition)
    return GameState(
        turn=1,
        max_turns=definition.rules.max_turns,
        seed=definition.rules.seed,
        gold=definition.starting_gold,
        materials=definition.starting_materials,
        experience_pool=0,
        adventurers=definition.content.adventurers,
        equipment_inventory=(),
        unlocked_upgrade_ids=frozenset(),
        current_monsters=spawn_monsters(definition, 1),
        recruit_candidates=spawn_recruit_candidates(definition, 1),
        next_equipment_instance_number=1,
        next_adventurer_number=1,
    )


def is_finished(state: GameState) -> bool:
    """判断游戏是否已经超过最后回合。"""

    _validate_state(state)
    return state.turn > state.max_turns


def apply_turn(
    definition: GameDefinition,
    state: GameState,
    action: TurnAction,
) -> TurnResult:
    """按给定顺序执行回合内操作，然后结束当前回合。"""

    _validate_definition(definition)
    _validate_state(state)
    if not isinstance(action, TurnAction):
        raise TypeError("action must be TurnAction")

    crafted_equipment_ids: list[str] = []
    purchased_upgrade_ids: list[str] = []
    recruited_adventurer_ids: list[str] = []
    for operation in action.operations:
        previous_equipment_ids = {item.instance_id for item in state.equipment_inventory}
        previous_upgrade_ids = set(state.unlocked_upgrade_ids)
        previous_adventurer_ids = {item.adventurer_id for item in state.adventurers}
        state = apply_preparation_action(definition, state, operation)
        crafted_equipment_ids.extend(
            item.instance_id
            for item in state.equipment_inventory
            if item.instance_id not in previous_equipment_ids
        )
        purchased_upgrade_ids.extend(sorted(state.unlocked_upgrade_ids - previous_upgrade_ids))
        recruited_adventurer_ids.extend(
            item.adventurer_id
            for item in state.adventurers
            if item.adventurer_id not in previous_adventurer_ids
        )

    result = end_turn(definition, state, EndTurnAction(hunts=action.hunts))
    return TurnResult(
        state=result.state,
        battles=result.battles,
        crafted_equipment_ids=tuple(crafted_equipment_ids),
        purchased_upgrade_ids=tuple(purchased_upgrade_ids),
        recruited_adventurer_ids=tuple(recruited_adventurer_ids),
    )


def apply_preparation_action(
    definition: GameDefinition,
    state: GameState,
    action: PreparationAction,
) -> GameState:
    """执行一个回合内操作，不推进回合。"""

    _validate_definition(definition)
    _validate_state(state)
    if is_finished(state):
        raise GameError("game is already finished")

    if isinstance(action, CraftAction):
        state, _ = _apply_craft_action(definition, state, action.recipe_id)
        return state

    if isinstance(action, PurchaseUpgradeAction):
        old_stats = _effective_stats_by_adventurer_id(definition, state)
        state = _apply_purchase_action(definition, state, action.upgrade_id)
        return _sync_all_adventurer_resources(definition, state, old_stats)

    if isinstance(action, AllocateExperienceAction):
        return _apply_experience_allocation(definition, state, action)

    if isinstance(action, RecruitAction):
        return _apply_recruit_action(definition, state, action)

    if isinstance(action, DismissAction):
        return _apply_dismiss_action(definition, state, action)

    if isinstance(action, UnequipAction):
        return _apply_unequip_action(definition, state, action)

    if isinstance(action, EquipAction):
        return _apply_equip_action(definition, state, action)

    raise TypeError("action must be a preparation action")


def end_turn(
    definition: GameDefinition,
    state: GameState,
    action: EndTurnAction,
) -> TurnResult:
    """提交交战列表，结算战斗并进入下一回合。"""

    _validate_definition(definition)
    _validate_state(state)
    if not isinstance(action, EndTurnAction):
        raise TypeError("action must be EndTurnAction")
    if is_finished(state):
        raise GameError("game is already finished")

    battles: list[BattleSettlement] = []

    _validate_hunt_actions(action.hunts)
    for hunt_action in action.hunts:
        state, battle = _apply_hunt_action(definition, state, hunt_action)
        battles.append(battle)

    state = _apply_turn_recovery(definition, state)

    next_turn = state.turn + 1
    next_monsters = (
        spawn_monsters(definition, next_turn)
        if next_turn <= state.max_turns
        else ()
    )
    next_candidates = (
        spawn_recruit_candidates(definition, next_turn)
        if next_turn <= state.max_turns
        else ()
    )
    state = replace(
        state,
        turn=next_turn,
        current_monsters=next_monsters,
        recruit_candidates=next_candidates,
    )

    return TurnResult(
        state=state,
        battles=tuple(battles),
        crafted_equipment_ids=(),
        purchased_upgrade_ids=(),
        recruited_adventurer_ids=(),
    )


def preview_battle(
    definition: GameDefinition,
    state: GameState,
    *,
    adventurer_id: str,
    monster_id: str,
) -> BattleSettlement:
    """Preview one 1v1 hunt without changing game state."""

    _validate_definition(definition)
    _validate_state(state)
    if is_finished(state):
        raise GameError("game is already finished")
    _, battle = _apply_hunt_action(
        definition,
        state,
        HuntAction(adventurer_id=adventurer_id, monster_id=monster_id),
    )
    return battle


def spawn_monsters(definition: GameDefinition, turn: int) -> tuple[SpawnedMonster, ...]:
    """刷新指定回合的怪物。"""

    _validate_definition(definition)
    if turn > definition.rules.max_turns:
        return ()
    spawn_rules = definition.rules.monster_spawn
    count = int(spawn_rules.count_curve.value_at(turn))
    if count == 0:
        return ()
    stat_factor = spawn_rules.stat_growth_curve.value_at(turn)
    reward_factor = spawn_rules.reward_growth_curve.value_at(turn)
    rng = random.Random(definition.rules.seed * 1_000_003 + turn)
    eligible_archetypes = _eligible_monster_archetypes(
        definition.content.monster_archetypes,
        turn,
    )
    monsters: list[SpawnedMonster] = []

    for index in range(count):
        archetype = _select_monster_archetype(rng, eligible_archetypes)
        tier = _roll_tier(rng, spawn_rules)
        tc = _tier_config(spawn_rules, tier)
        bonus_skills = _sample_bonus_skills(rng, spawn_rules.bonus_skill_themes, tc.bonus_skill_count if tc else 0)
        monsters.append(_spawn_monster(archetype, turn, index + 1, stat_factor, reward_factor, tier, tc, bonus_skills))
    return tuple(monsters)


def spawn_recruit_candidates(definition: GameDefinition, turn: int) -> tuple[RecruitCandidate, ...]:
    """刷新指定回合的招募候选。"""

    _validate_definition(definition)
    if turn > definition.rules.max_turns:
        return ()
    count = _recruit_candidate_count(definition, turn)
    templates = definition.content.recruitable_adventurers
    if count <= 0 or not templates:
        return ()

    rng = random.Random(definition.rules.seed * 1_000_033 + turn * 97_409)
    indexes = list(range(len(templates)))
    if count <= len(indexes):
        rng.shuffle(indexes)
        selected = indexes[:count]
    else:
        selected = [rng.randrange(len(templates)) for _ in range(count)]

    candidates: list[RecruitCandidate] = []
    config = definition.rules.recruitment.variation
    for index, template_index in enumerate(selected, start=1):
        template = templates[template_index]
        candidates.append(_spawn_recruit_candidate(template, turn, index, rng, config))
    return tuple(candidates)


def _recruit_candidate_count(definition: GameDefinition, turn: int) -> int:
    rules = definition.rules.recruitment
    if turn == 1 and rules.first_turn_candidate_count is not None:
        return rules.first_turn_candidate_count
    return rules.candidate_count


def _eligible_monster_archetypes(
    archetypes: tuple[MonsterArchetype, ...],
    turn: int,
) -> tuple[MonsterArchetype, ...]:
    unlocked = tuple(archetype for archetype in archetypes if archetype.min_turn <= turn)
    if not unlocked:
        raise ValueError(f"no monster archetypes unlocked on turn {turn}")
    eligible = tuple(archetype for archetype in unlocked if archetype.spawn_weight > 0)
    if not eligible:
        raise ValueError(f"no monster archetypes with positive spawn weight on turn {turn}")
    return eligible


def _select_monster_archetype(
    rng: random.Random,
    archetypes: tuple[MonsterArchetype, ...],
) -> MonsterArchetype:
    return rng.choices(
        archetypes,
        weights=[archetype.spawn_weight for archetype in archetypes],
        k=1,
    )[0]


def effective_adventurer_stats(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> CombatStats:
    """计算冒险者当前完整战斗属性。"""

    stats = apply_stat_modifier(
        adventurer.base_stats,
        level_stat_modifier(
            adventurer.level,
            definition.content.experience_rules,
            stat_growth_per_level=_adventurer_level_growth(definition, adventurer),
        ),
    )
    stats = apply_equipment_stats(stats, _equipped_templates(definition, state, adventurer))
    return apply_upgrade_stats(stats, _unlocked_upgrades(definition, state))


def effective_adventurer_skills(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> tuple[Skill, ...]:
    """合并冒险者当前可用技能。"""

    skills = combine_equipment_skills(
        adventurer.skills + _unlocked_level_skills(adventurer),
        _equipped_templates(definition, state, adventurer),
    )
    return combine_upgrade_skills(skills, _unlocked_upgrades(definition, state))


def party_size_limit(definition: GameDefinition, state: GameState) -> int:
    """计算当前队伍人数上限。"""

    _validate_definition(definition)
    _validate_state(state)
    rules = definition.rules.recruitment
    return min(
        rules.maximum_party_size_limit,
        rules.initial_party_size_limit
        + combine_party_size_bonus(_unlocked_upgrades(definition, state)),
    )


def _apply_craft_action(
    definition: GameDefinition,
    state: GameState,
    recipe_id: str,
) -> tuple[GameState, str]:
    recipe = _recipe_by_id(definition, recipe_id)
    instance_id = f"eq_{state.next_equipment_instance_number:04d}"
    result = craft_equipment(
        recipe,
        CraftingInventory(
            gold=state.gold,
            materials=state.materials,
            equipment=state.equipment_inventory,
        ),
        instance_id=instance_id,
    )
    return (
        replace(
            state,
            gold=result.inventory.gold,
            materials=result.inventory.materials,
            equipment_inventory=result.inventory.equipment,
            next_equipment_instance_number=state.next_equipment_instance_number + 1,
        ),
        result.equipment.instance_id,
    )


def _apply_purchase_action(
    definition: GameDefinition,
    state: GameState,
    upgrade_id: str,
) -> GameState:
    upgrade = _upgrade_by_id(definition, upgrade_id)
    result = purchase_upgrade(
        upgrade,
        UpgradeInventory(
            gold=state.gold,
            unlocked_upgrade_ids=state.unlocked_upgrade_ids,
        ),
    )
    return replace(
        state,
        gold=result.inventory.gold,
        unlocked_upgrade_ids=result.inventory.unlocked_upgrade_ids,
    )


def _apply_experience_allocation(
    definition: GameDefinition,
    state: GameState,
    allocation: AllocateExperienceAction,
) -> GameState:
    if allocation.amount > state.experience_pool:
        raise GameError("not enough experience in pool")
    adventurer = _adventurer_by_id(state, allocation.adventurer_id)
    old_stats = effective_adventurer_stats(definition, state, adventurer)
    level, experience = add_experience(
        level=adventurer.level,
        experience=adventurer.experience,
        amount=allocation.amount,
        rules=definition.content.experience_rules,
    )
    updated = replace(adventurer, level=level, experience=experience)
    temp_state = _replace_adventurer(
        replace(state, experience_pool=state.experience_pool - allocation.amount),
        updated,
    )
    new_stats = effective_adventurer_stats(definition, temp_state, updated)
    updated = replace(
        updated,
        resources=_sync_resources(adventurer.resources, old_stats, new_stats),
    )
    return _replace_adventurer(temp_state, updated)


def _apply_recruit_action(
    definition: GameDefinition,
    state: GameState,
    action: RecruitAction,
) -> GameState:
    candidate = _recruit_candidate_by_id(state, action.candidate_id)
    limit = party_size_limit(definition, state)
    if len(state.adventurers) >= limit:
        raise GameError(f"party size limit reached: {len(state.adventurers)}/{limit}")
    if state.gold < candidate.recruit_gold:
        raise GameError("not enough gold")

    adventurer_id, next_number = _next_recruited_adventurer_id(state)
    adventurer = AdventurerState(
        adventurer_id=adventurer_id,
        name=candidate.name,
        base_stats=candidate.base_stats,
        resources=CombatResources.full(candidate.base_stats),
        skills=candidate.skills,
        level_skill_unlocks=candidate.level_skill_unlocks,
        stat_growth_per_level=candidate.stat_growth_per_level,
        template_id=candidate.template_id,
    )
    updated_state = replace(
        state,
        gold=state.gold - candidate.recruit_gold,
        adventurers=state.adventurers + (adventurer,),
        recruit_candidates=tuple(
            item
            for item in state.recruit_candidates
            if item.candidate_id != candidate.candidate_id
        ),
        next_adventurer_number=next_number,
    )
    effective_stats = effective_adventurer_stats(definition, updated_state, adventurer)
    return _replace_adventurer(
        updated_state,
        replace(adventurer, resources=CombatResources.full(effective_stats)),
    )


def _apply_dismiss_action(
    definition: GameDefinition,
    state: GameState,
    action: DismissAction,
) -> GameState:
    """解散冒险者：移出队伍，装备归还库存。"""

    adventurer = _adventurer_by_id(state, action.adventurer_id)

    # 收集该冒险者身上的装备，归还到库存
    equipped_items = [
        instance
        for instance in state.equipment_inventory
        if instance.equipped_by == adventurer.adventurer_id
    ]
    returned_inventory = list(state.equipment_inventory)
    for instance in equipped_items:
        idx = returned_inventory.index(instance)
        returned_inventory[idx] = replace(instance, equipped_by=None)

    return replace(
        state,
        adventurers=tuple(
            a for a in state.adventurers if a.adventurer_id != adventurer.adventurer_id
        ),
        equipment_inventory=tuple(returned_inventory),
    )


def _apply_equip_action(
    definition: GameDefinition,
    state: GameState,
    equip_action: EquipAction,
) -> GameState:
    instance = _equipment_instance_by_id(state, equip_action.equipment_instance_id)
    template = _equipment_template_by_id(definition, instance.template_id)
    adventurer = _adventurer_by_id(state, equip_action.adventurer_id)
    if template.allowed_classes and adventurer.template_id not in template.allowed_classes:
        raise GameError(
            f"冒险者职业 '{adventurer.template_id}' 无法装备 "
            f"'{template.name}'（需要职业: {', '.join(template.allowed_classes)}）"
        )
    old_stats = _effective_stats_by_adventurer_id(definition, state)

    updated_adventurers: list[AdventurerState] = []
    for adventurer in state.adventurers:
        loadout = _remove_equipped_instance(adventurer.equipment, instance.instance_id)
        if adventurer.adventurer_id == equip_action.adventurer_id:
            loadout = _equip_instance(loadout, template, instance.instance_id)
        updated_adventurers.append(replace(adventurer, equipment=loadout))

    state = replace(state, adventurers=tuple(updated_adventurers))
    return _sync_all_adventurer_resources(definition, state, old_stats)


def _apply_unequip_action(
    definition: GameDefinition,
    state: GameState,
    unequip_action: UnequipAction,
) -> GameState:
    adventurer = _adventurer_by_id(state, unequip_action.adventurer_id)
    if not any(item.slot == unequip_action.slot for item in adventurer.equipment.items):
        raise GameError(f"装备槽位为空: {unequip_action.slot}")

    old_stats = _effective_stats_by_adventurer_id(definition, state)
    updated = replace(
        adventurer,
        equipment=_unequip_slot(adventurer.equipment, unequip_action.slot),
    )
    state = _replace_adventurer(state, updated)
    return _sync_all_adventurer_resources(definition, state, old_stats)


def _apply_hunt_action(
    definition: GameDefinition,
    state: GameState,
    hunt_action: HuntAction,
) -> tuple[GameState, BattleSettlement]:
    adventurer = _adventurer_by_id(state, hunt_action.adventurer_id)
    if not adventurer.resources.is_alive:
        raise GameError(f"adventurer is not alive: {adventurer.adventurer_id}")
    monster = _monster_by_id(state, hunt_action.monster_id)

    combat_result = run_auto_battle(
        Combatant(
            combatant_id=adventurer.adventurer_id,
            stats=effective_adventurer_stats(definition, state, adventurer),
            resources=adventurer.resources,
            skills=effective_adventurer_skills(definition, state, adventurer),
        ),
        Combatant(
            combatant_id=monster.monster_id,
            stats=monster.stats,
            resources=CombatResources.full(monster.stats),
            skills=monster.skills,
        ),
    )
    won = combat_result.outcome == "left_win"
    reward = monster.reward if won else RewardBundle()
    updated_adventurer = replace(adventurer, resources=combat_result.left_resources)
    state = _replace_adventurer(state, updated_adventurer)
    if won:
        state = _apply_reward(state, reward)

    return (
        state,
        BattleSettlement(
            adventurer_id=adventurer.adventurer_id,
            monster_id=monster.monster_id,
            won=won,
            reward=reward,
            combat_result=combat_result,
        ),
    )


def _roll_tier(rng: random.Random, spawn_rules: MonsterSpawnRules) -> str:
    boss = spawn_rules.boss
    if boss.chance > 0 and rng.random() < boss.chance:
        return "boss"
    elite = spawn_rules.elite
    if elite.chance > 0 and rng.random() < elite.chance:
        return "elite"
    return "normal"


def _tier_config(spawn_rules: MonsterSpawnRules, tier: str) -> MonsterTierConfig | None:
    if tier == "boss":
        return spawn_rules.boss
    if tier == "elite":
        return spawn_rules.elite
    return None


def _sample_bonus_skills(
    rng: random.Random, themes: tuple[SkillTheme, ...], count: int
) -> tuple[Skill, ...]:
    if not themes or count <= 0:
        return ()
    theme = rng.choice(themes)
    available = list(theme.skills)
    return tuple(rng.sample(available, min(count, len(available))))


def _scale_reward_bundle(reward: RewardBundle, multiplier: float) -> RewardBundle:
    return RewardBundle(
        gold=max(0, int(reward.gold * multiplier)),
        experience=max(0, int(reward.experience * multiplier)),
        materials={k: max(0, int(v * multiplier)) for k, v in reward.materials.items()},
    )


def _spawn_monster(
    archetype: MonsterArchetype,
    turn: int,
    index: int,
    stat_factor: int,
    reward_factor: float,
    tier: str = "normal",
    tc: MonsterTierConfig | None = None,
    bonus_skills: tuple[Skill, ...] = (),
) -> SpawnedMonster:
    stats = apply_stat_modifier(
        archetype.base_stats,
        scale_stat_modifier(archetype.stat_growth, stat_factor),
    )
    reward = archetype.base_reward + _scale_reward(archetype.reward_growth, reward_factor)

    if tc is not None:
        stats = scale_combat_stats(stats, tc.stat_multiplier)
        reward = _scale_reward_bundle(reward, tc.reward_multiplier) + _scale_reward(tc.bonus_reward_growth, reward_factor)
        name = f"{tc.name_prefix}·{archetype.name}" if tc.name_prefix else archetype.name
        skills = archetype.skills + bonus_skills
    else:
        name = archetype.name
        skills = archetype.skills

    return SpawnedMonster(
        monster_id=f"turn_{turn}_monster_{index}",
        archetype_id=archetype.archetype_id,
        name=name,
        stats=stats,
        reward=reward,
        tier=tier,
        skills=skills,
    )


def _spawn_recruit_candidate(
    template: RecruitableAdventurerTemplate,
    turn: int,
    index: int,
    rng: random.Random,
    config: RecruitVariationConfig,
) -> RecruitCandidate:
    varied_stats = _vary_combat_stats(template.base_stats, rng, config)
    varied_growth, growth_key, growth_delta = _vary_stat_modifier(
        template.stat_growth_per_level, rng, config,
    )

    # Price: base → stat adjustment → random factor
    template_total = sum(_stat_values(template.base_stats).values())
    varied_total = sum(_stat_values(varied_stats).values())
    stat_adjustment = (varied_total - template_total) / max(1, template_total) * config.price_stat_adjustment_ratio
    adjusted_gold = template.recruit_gold * (1 + stat_adjustment)
    lo, hi = config.price_factor_range
    price_factor = rng.uniform(lo, hi)
    recruit_gold = max(0, int(round(adjusted_gold * price_factor)))

    # Suffix from growth variation
    suffix = ""
    if growth_key and growth_delta != 0 and growth_key in config.suffix_mapping:
        mapping = config.suffix_mapping[growth_key]
        suffix = mapping.positive if growth_delta > 0 else mapping.negative
    name = f"{template.name}{suffix}"

    return RecruitCandidate(
        candidate_id=f"turn_{turn}_recruit_{index}",
        template_id=template.template_id,
        name=name,
        recruit_gold=recruit_gold,
        base_stats=varied_stats,
        stat_growth_per_level=varied_growth,
        skills=template.skills,
        level_skill_unlocks=template.level_skill_unlocks,
    )


def _stat_values(stats: CombatStats) -> dict[str, int]:
    return {
        "hp": stats.hp,
        "mp": stats.mp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed,
        "recovery": stats.recovery,
    }


def _vary_combat_stats(
    stats: CombatStats, rng: random.Random, config: RecruitVariationConfig,
) -> CombatStats:
    values = _stat_values(stats)
    keys = list(values)
    rng.shuffle(keys)
    for key in keys[: config.stats_to_vary]:
        magnitude = _stat_variation_amount(key, values[key], config)
        values[key] = _clamp_stat_value(key, values[key] + rng.randint(-magnitude, magnitude))
    return CombatStats(**values)


def _vary_stat_modifier(
    modifier: CombatStatModifier, rng: random.Random, config: RecruitVariationConfig,
) -> tuple[CombatStatModifier, str, int]:
    """Return (varied_modifier, varied_key, delta). delta is -1/0/+1."""
    values = {
        "hp": modifier.hp,
        "mp": modifier.mp,
        "attack": modifier.attack,
        "defense": modifier.defense,
        "speed": modifier.speed,
        "recovery": modifier.recovery,
    }
    keys = list(values)
    rng.shuffle(keys)
    varied_key = keys[0]
    delta = rng.randint(-config.growth_variation_amount, config.growth_variation_amount)
    values[varied_key] = max(0, values[varied_key] + delta)
    return CombatStatModifier(**values), varied_key, delta


def _stat_variation_amount(key: str, value: int, config: RecruitVariationConfig) -> int:
    if key == "hp":
        return max(config.hp_min_variation, round(value * config.hp_variation_ratio))
    return max(config.stat_min_variation, round(max(value, 1) * config.stat_variation_ratio))


def _clamp_stat_value(key: str, value: int) -> int:
    minimum = 1 if key == "hp" else 0
    return max(minimum, value)


def _scale_reward(reward: RewardBundle, factor: float) -> RewardBundle:
    materials = {
        material_id: max(0, int(quantity * factor))
        for material_id, quantity in reward.materials.items()
    }
    return RewardBundle(
        gold=max(0, int(reward.gold * factor)),
        experience=max(0, int(reward.experience * factor)),
        materials=materials,
    )


def _adventurer_level_growth(
    definition: GameDefinition,
    adventurer: AdventurerState,
) -> CombatStatModifier:
    return (
        adventurer.stat_growth_per_level
        if adventurer.stat_growth_per_level is not None
        else definition.content.experience_rules.stat_growth_per_level
    )


def _unlocked_level_skills(adventurer: AdventurerState) -> tuple[Skill, ...]:
    skills: list[Skill] = []
    for unlock in adventurer.level_skill_unlocks:
        if unlock.level <= adventurer.level:
            skills.extend(unlock.skills)
    return tuple(skills)


def _apply_reward(state: GameState, reward: RewardBundle) -> GameState:
    materials = dict(state.materials)
    for material_id, quantity in reward.materials.items():
        materials[material_id] = materials.get(material_id, 0) + quantity
    return replace(
        state,
        gold=state.gold + reward.gold,
        materials=materials,
        experience_pool=state.experience_pool + reward.experience,
    )


def _apply_turn_recovery(definition: GameDefinition, state: GameState) -> GameState:
    adventurers: list[AdventurerState] = []
    for adventurer in state.adventurers:
        stats = effective_adventurer_stats(definition, state, adventurer)
        hp_recovery = definition.rules.turn_recovery.hp + int(
            stats.hp * definition.rules.turn_recovery.hp_percent
        )
        if definition.rules.turn_recovery.use_recovery_stat:
            hp_recovery += stats.recovery
        mp_recovery_amount = definition.rules.turn_recovery.mp + int(
            stats.mp * definition.rules.turn_recovery.mp_percent
        )
        if definition.rules.turn_recovery.use_recovery_stat:
            mp_recovery_amount += stats.mp_recovery
        adventurers.append(
            replace(
                adventurer,
                resources=CombatResources(
                    current_hp=min(stats.hp, adventurer.resources.current_hp + hp_recovery),
                    current_mp=min(
                        stats.mp,
                        adventurer.resources.current_mp + mp_recovery_amount,
                    ),
                ),
            )
        )
    return replace(state, adventurers=tuple(adventurers))


def _sync_all_adventurer_resources(
    definition: GameDefinition,
    state: GameState,
    old_stats_by_id: Mapping[str, CombatStats],
) -> GameState:
    adventurers: list[AdventurerState] = []
    for adventurer in state.adventurers:
        old_stats = old_stats_by_id[adventurer.adventurer_id]
        new_stats = effective_adventurer_stats(definition, state, adventurer)
        adventurers.append(
            replace(
                adventurer,
                resources=_sync_resources(adventurer.resources, old_stats, new_stats),
            )
        )
    return replace(state, adventurers=tuple(adventurers))


def _sync_resources(
    resources: CombatResources,
    old_stats: CombatStats,
    new_stats: CombatStats,
) -> CombatResources:
    missing_hp = max(0, old_stats.hp - min(resources.current_hp, old_stats.hp))
    missing_mp = max(0, old_stats.mp - min(resources.current_mp, old_stats.mp))
    return CombatResources(
        current_hp=max(0, min(new_stats.hp, new_stats.hp - missing_hp)),
        current_mp=max(0, min(new_stats.mp, new_stats.mp - missing_mp)),
    )


def _effective_stats_by_adventurer_id(
    definition: GameDefinition,
    state: GameState,
) -> dict[str, CombatStats]:
    return {
        adventurer.adventurer_id: effective_adventurer_stats(definition, state, adventurer)
        for adventurer in state.adventurers
    }


def _equip_instance(
    loadout: EquipmentLoadout,
    template: EquipmentTemplate,
    instance_id: str,
) -> EquipmentLoadout:
    blocked_slots = _blocked_slots_for(template)
    items = tuple(item for item in loadout.items if item.slot not in blocked_slots)
    return EquipmentLoadout(items=items + (EquippedItem(slot=template.slot, instance_id=instance_id),))


def _remove_equipped_instance(
    loadout: EquipmentLoadout,
    instance_id: str,
) -> EquipmentLoadout:
    return EquipmentLoadout(
        items=tuple(item for item in loadout.items if item.instance_id != instance_id)
    )


def _unequip_slot(
    loadout: EquipmentLoadout,
    slot: str,
) -> EquipmentLoadout:
    return EquipmentLoadout(
        items=tuple(item for item in loadout.items if item.slot != slot)
    )


def _blocked_slots_for(template: EquipmentTemplate) -> set[str]:
    if template.slot == "two_hand":
        return {"main_hand", "off_hand", "two_hand"}
    if template.slot in {"main_hand", "off_hand"}:
        return {template.slot, "two_hand"}
    return {template.slot}


def _equipped_templates(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> tuple[EquipmentTemplate, ...]:
    instances = {
        item.instance_id: item
        for item in state.equipment_inventory
    }
    templates: list[EquipmentTemplate] = []
    for equipped in adventurer.equipment.items:
        instance = instances.get(equipped.instance_id)
        if instance is None:
            raise GameError(f"missing equipment instance: {equipped.instance_id}")
        templates.append(_equipment_template_by_id(definition, instance.template_id))
    return tuple(templates)


def _unlocked_upgrades(
    definition: GameDefinition,
    state: GameState,
) -> tuple[GlobalUpgrade, ...]:
    upgrades_by_id = {
        upgrade.upgrade_id: upgrade
        for upgrade in definition.content.global_upgrades
    }
    upgrades = []
    for upgrade_id in state.unlocked_upgrade_ids:
        if upgrade_id not in upgrades_by_id:
            raise GameError(f"unknown unlocked upgrade: {upgrade_id}")
        upgrades.append(upgrades_by_id[upgrade_id])
    return tuple(upgrades)


def _validate_hunt_actions(hunts: Iterable[HuntAction]) -> None:
    adventurer_ids: set[str] = set()
    monster_ids: set[str] = set()
    for hunt in hunts:
        if hunt.adventurer_id in adventurer_ids:
            raise GameError(f"duplicate adventurer hunt: {hunt.adventurer_id}")
        if hunt.monster_id in monster_ids:
            raise GameError(f"duplicate monster hunt: {hunt.monster_id}")
        adventurer_ids.add(hunt.adventurer_id)
        monster_ids.add(hunt.monster_id)


def _replace_adventurer(state: GameState, adventurer: AdventurerState) -> GameState:
    return replace(
        state,
        adventurers=tuple(
            adventurer if item.adventurer_id == adventurer.adventurer_id else item
            for item in state.adventurers
        ),
    )


def _adventurer_by_id(state: GameState, adventurer_id: str) -> AdventurerState:
    for adventurer in state.adventurers:
        if adventurer.adventurer_id == adventurer_id:
            return adventurer
    raise GameError(f"unknown adventurer: {adventurer_id}")


def _monster_by_id(state: GameState, monster_id: str) -> SpawnedMonster:
    for monster in state.current_monsters:
        if monster.monster_id == monster_id:
            return monster
    raise GameError(f"unknown monster: {monster_id}")


def _equipment_instance_by_id(state: GameState, instance_id: str) -> EquipmentInstance:
    for equipment in state.equipment_inventory:
        if equipment.instance_id == instance_id:
            return equipment
    raise GameError(f"unknown equipment instance: {instance_id}")


def _equipment_template_by_id(
    definition: GameDefinition,
    template_id: str,
) -> EquipmentTemplate:
    for template in definition.content.equipment_templates:
        if template.equipment_id == template_id:
            return template
    raise GameError(f"unknown equipment template: {template_id}")


def _recipe_by_id(definition: GameDefinition, recipe_id: str) -> CraftingRecipe:
    for recipe in definition.content.crafting_recipes:
        if recipe.recipe_id == recipe_id:
            return recipe
    raise GameError(f"unknown recipe: {recipe_id}")


def _upgrade_by_id(definition: GameDefinition, upgrade_id: str) -> GlobalUpgrade:
    for upgrade in definition.content.global_upgrades:
        if upgrade.upgrade_id == upgrade_id:
            return upgrade
    raise GameError(f"unknown upgrade: {upgrade_id}")


def _recruit_candidate_by_id(state: GameState, candidate_id: str) -> RecruitCandidate:
    for candidate in state.recruit_candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise GameError(f"unknown recruit candidate: {candidate_id}")


def _next_recruited_adventurer_id(state: GameState) -> tuple[str, int]:
    existing_ids = {
        adventurer.adventurer_id
        for adventurer in state.adventurers
    }
    number = state.next_adventurer_number
    while True:
        adventurer_id = f"recruit_{number:04d}"
        number += 1
        if adventurer_id not in existing_ids:
            return adventurer_id, number


def _validate_definition(definition: GameDefinition) -> None:
    if not isinstance(definition, GameDefinition):
        raise TypeError("definition must be GameDefinition")


def _validate_state(state: GameState) -> None:
    if not isinstance(state, GameState):
        raise TypeError("state must be GameState")
