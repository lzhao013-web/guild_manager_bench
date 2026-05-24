from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    EndTurnAction,
    EquipAction,
    HuntAction,
    PreparationAction,
    PurchaseUpgradeAction,
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
    CombatStats,
    apply_stat_modifier,
    scale_stat_modifier,
)
from guild_manager_bench.game.progression import add_experience, level_stat_modifier
from guild_manager_bench.game.state import (
    AdventurerState,
    GameDefinition,
    GameState,
    MonsterArchetype,
    RewardBundle,
    SpawnedMonster,
)
from guild_manager_bench.game.skills import Skill
from guild_manager_bench.game.upgrades import (
    GlobalUpgrade,
    UpgradeInventory,
    apply_upgrade_stats,
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
        next_equipment_instance_number=1,
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
    for operation in action.operations:
        previous_equipment_ids = {item.instance_id for item in state.equipment_inventory}
        previous_upgrade_ids = set(state.unlocked_upgrade_ids)
        state = apply_preparation_action(definition, state, operation)
        crafted_equipment_ids.extend(
            item.instance_id
            for item in state.equipment_inventory
            if item.instance_id not in previous_equipment_ids
        )
        purchased_upgrade_ids.extend(sorted(state.unlocked_upgrade_ids - previous_upgrade_ids))

    result = end_turn(definition, state, EndTurnAction(hunts=action.hunts))
    return TurnResult(
        state=result.state,
        battles=result.battles,
        crafted_equipment_ids=tuple(crafted_equipment_ids),
        purchased_upgrade_ids=tuple(purchased_upgrade_ids),
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
    state = replace(state, turn=next_turn, current_monsters=next_monsters)

    return TurnResult(
        state=state,
        battles=tuple(battles),
        crafted_equipment_ids=(),
        purchased_upgrade_ids=(),
    )


def spawn_monsters(definition: GameDefinition, turn: int) -> tuple[SpawnedMonster, ...]:
    """刷新指定回合的怪物。"""

    _validate_definition(definition)
    if turn > definition.rules.max_turns:
        return ()
    count = definition.rules.monster_spawn.count_curve.value_at(turn)
    stat_factor = definition.rules.monster_spawn.stat_growth_curve.value_at(turn)
    reward_factor = definition.rules.monster_spawn.reward_growth_curve.value_at(turn)
    rng = random.Random(definition.rules.seed * 1_000_003 + turn)
    monsters: list[SpawnedMonster] = []

    for index in range(count):
        archetype = definition.content.monster_archetypes[
            rng.randrange(len(definition.content.monster_archetypes))
        ]
        monsters.append(_spawn_monster(archetype, turn, index + 1, stat_factor, reward_factor))
    return tuple(monsters)


def effective_adventurer_stats(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> CombatStats:
    """计算冒险者当前完整战斗属性。"""

    stats = apply_stat_modifier(
        adventurer.base_stats,
        level_stat_modifier(adventurer.level, definition.content.experience_rules),
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
        adventurer.skills,
        _equipped_templates(definition, state, adventurer),
    )
    return combine_upgrade_skills(skills, _unlocked_upgrades(definition, state))


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


def _apply_equip_action(
    definition: GameDefinition,
    state: GameState,
    equip_action: EquipAction,
) -> GameState:
    instance = _equipment_instance_by_id(state, equip_action.equipment_instance_id)
    template = _equipment_template_by_id(definition, instance.template_id)
    _adventurer_by_id(state, equip_action.adventurer_id)
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
        raise GameError(f"equipment slot is empty: {unequip_action.slot}")

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


def _spawn_monster(
    archetype: MonsterArchetype,
    turn: int,
    index: int,
    stat_factor: int,
    reward_factor: int,
) -> SpawnedMonster:
    stats = apply_stat_modifier(
        archetype.base_stats,
        scale_stat_modifier(archetype.stat_growth, stat_factor),
    )
    reward = archetype.base_reward + _scale_reward(archetype.reward_growth, reward_factor)
    return SpawnedMonster(
        monster_id=f"turn_{turn}_monster_{index}",
        archetype_id=archetype.archetype_id,
        name=archetype.name,
        stats=stats,
        reward=reward,
        skills=archetype.skills,
    )


def _scale_reward(reward: RewardBundle, factor: int) -> RewardBundle:
    materials = {
        material_id: quantity * factor
        for material_id, quantity in reward.materials.items()
    }
    return RewardBundle(
        gold=reward.gold * factor,
        experience=reward.experience * factor,
        materials=materials,
    )


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
        mp_recovery = definition.rules.turn_recovery.mp + int(
            stats.mp * definition.rules.turn_recovery.mp_percent
        )
        adventurers.append(
            replace(
                adventurer,
                resources=CombatResources(
                    current_hp=min(stats.hp, adventurer.resources.current_hp + hp_recovery),
                    current_mp=min(
                        stats.mp,
                        adventurer.resources.current_mp + mp_recovery,
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


def _validate_definition(definition: GameDefinition) -> None:
    if not isinstance(definition, GameDefinition):
        raise TypeError("definition must be GameDefinition")


def _validate_state(state: GameState) -> None:
    if not isinstance(state, GameState):
        raise TypeError("state must be GameState")
