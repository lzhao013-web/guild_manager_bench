from __future__ import annotations

from typing import Any

from guild_manager_bench.game.crafting import CraftingInventory, missing_requirements
from guild_manager_bench.game.engine import (
    effective_adventurer_skills,
    effective_adventurer_stats,
    is_finished,
    party_size_limit,
)
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.progression import (
    add_experience,
    required_experience_for_next_level,
)
from guild_manager_bench.game.skills import (
    Skill,
    SkillCondition,
    SkillEffect,
    StatusDefinition,
)
from guild_manager_bench.game.state import (
    AdventurerState,
    GameDefinition,
    GameState,
    RecruitCandidate,
    RewardBundle,
    SpawnedMonster,
)
from guild_manager_bench.game.upgrades import UpgradeInventory, missing_upgrade_requirements


def build_observation(definition: GameDefinition, state: GameState) -> dict[str, Any]:
    """生成当前会话的完整可见状态。"""

    equipment_templates = {
        item.equipment_id: item
        for item in definition.content.equipment_templates
    }
    class_names = {
        t.template_id: t.name
        for t in definition.content.recruitable_adventurers
    }
    equipped_by = _equipped_by_adventurer_id(state)
    size_limit = party_size_limit(definition, state)
    return {
        "turn": state.turn,
        "max_turns": state.max_turns,
        "seed": state.seed,
        "finished": is_finished(state),
        "gold": state.gold,
        "materials": dict(state.materials),
        "experience_pool": state.experience_pool,
        "party_size_limit": size_limit,
        "party_size": len(state.adventurers),
        "experience_rules": _experience_rules_to_dict(definition),
        "turn_recovery_rules": _turn_recovery_rules_to_dict(definition),
        "scoring": _scoring_rules_to_dict(definition),
        "adventurers": [
            _adventurer_to_dict(definition, state, adventurer)
            for adventurer in state.adventurers
        ],
        "monsters": [
            _monster_to_dict(monster)
            for monster in state.current_monsters
        ],
        "equipment_inventory": [
            _equipment_instance_to_dict(item, equipment_templates, equipped_by.get(item.instance_id), class_names)
            for item in state.equipment_inventory
        ],
        "crafting_recipes": [
            _recipe_to_dict(definition, state, recipe, class_names)
            for recipe in definition.content.crafting_recipes
        ],
        "global_upgrades": [
            _upgrade_to_dict(state, upgrade)
            for upgrade in definition.content.global_upgrades
        ],
        "recruit_candidates": [
            _recruit_candidate_to_dict(state, candidate, size_limit)
            for candidate in state.recruit_candidates
        ],
    }


def _adventurer_to_dict(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> dict[str, Any]:
    effective_stats = effective_adventurer_stats(definition, state, adventurer)
    effective_skills = effective_adventurer_skills(definition, state, adventurer)
    next_level = _next_level_info(definition, state, adventurer)
    return {
        "adventurer_id": adventurer.adventurer_id,
        "name": adventurer.name,
        "template_id": adventurer.template_id,
        "level": adventurer.level,
        "experience": adventurer.experience,
        "stat_growth_per_level": _stat_modifier_to_dict(
            _adventurer_level_growth(definition, adventurer)
        ),
        "base_stats": _stats_to_dict(adventurer.base_stats),
        "effective_stats": _stats_to_dict(effective_stats),
        "resources": _resources_to_dict(adventurer.resources),
        "skills": [_skill_to_dict(skill) for skill in effective_skills],
        "level_skill_unlocks": _level_skill_unlocks_to_dict(adventurer),
        "equipment": [
            {"slot": item.slot, "instance_id": item.instance_id}
            for item in adventurer.equipment.items
        ],
        "equipment_slots": _equipment_slots_to_dict(definition, state, adventurer),
        "next_level": next_level,
    }


def _monster_to_dict(monster: SpawnedMonster) -> dict[str, Any]:
    return {
        "monster_id": monster.monster_id,
        "archetype_id": monster.archetype_id,
        "name": monster.name,
        "tier": monster.tier,
        "stats": _stats_to_dict(monster.stats),
        "reward": _reward_to_dict(monster.reward),
        "skills": [_skill_to_dict(skill) for skill in monster.skills],
    }


def _recruit_candidate_to_dict(
    state: GameState,
    candidate: RecruitCandidate,
    size_limit: int,
) -> dict[str, Any]:
    missing: dict[str, Any] = {}
    if state.gold < candidate.recruit_gold:
        missing["gold"] = candidate.recruit_gold - state.gold
    if len(state.adventurers) >= size_limit:
        missing["party_size_limit"] = {
            "current": len(state.adventurers),
            "limit": size_limit,
        }
    return {
        "candidate_id": candidate.candidate_id,
        "template_id": candidate.template_id,
        "name": candidate.name,
        "recruit_gold": candidate.recruit_gold,
        "base_stats": _stats_to_dict(candidate.base_stats),
        "stat_growth_per_level": _stat_modifier_to_dict(candidate.stat_growth_per_level),
        "skills": [_skill_to_dict(skill) for skill in candidate.skills],
        "level_skill_unlocks": [
            _level_skill_unlock_to_dict(unlock, 1)
            for unlock in candidate.level_skill_unlocks
        ],
        "can_recruit": not missing,
        "missing": missing,
    }


def _equipment_instance_to_dict(item, equipment_templates, equipped_by: str | None, class_names: dict[str, str] | None = None) -> dict[str, Any]:
    template = equipment_templates[item.template_id]
    allowed_classes = list(template.allowed_classes)
    if class_names is None:
        class_names = {}
    return {
        "instance_id": item.instance_id,
        "template_id": item.template_id,
        "name": template.name,
        "slot": template.slot,
        "stats": _stat_modifier_to_dict(template.stat_modifier),
        "skills": [_skill_to_dict(skill) for skill in template.skills],
        "allowed_classes": allowed_classes,
        "allowed_class_names": [class_names.get(cid, cid) for cid in allowed_classes],
        "equipped_by": equipped_by,
    }


def _recipe_to_dict(definition, state, recipe, class_names: dict[str, str] | None = None) -> dict[str, Any]:
    inventory = CraftingInventory(
        gold=state.gold,
        materials=state.materials,
        equipment=state.equipment_inventory,
    )
    missing = missing_requirements(recipe, inventory)
    template = _equipment_template_by_id(definition, recipe.output_template_id)
    allowed_classes = list(template.allowed_classes)
    if class_names is None:
        class_names = {}
    return {
        "recipe_id": recipe.recipe_id,
        "name": recipe.name,
        "output_template_id": recipe.output_template_id,
        "output_name": template.name,
        "output_slot": template.slot,
        "output_stats": _stat_modifier_to_dict(template.stat_modifier),
        "output_skills": [_skill_to_dict(skill) for skill in template.skills],
        "output_allowed_classes": allowed_classes,
        "output_allowed_class_names": [class_names.get(cid, cid) for cid in allowed_classes],
        "gold_cost": recipe.gold_cost,
        "material_costs": {
            cost.material_id: cost.quantity
            for cost in recipe.material_costs
        },
        "can_craft": not missing,
        "missing": missing,
    }


def _upgrade_to_dict(state, upgrade) -> dict[str, Any]:
    inventory = UpgradeInventory(
        gold=state.gold,
        unlocked_upgrade_ids=state.unlocked_upgrade_ids,
    )
    missing = missing_upgrade_requirements(upgrade, inventory)
    return {
        "upgrade_id": upgrade.upgrade_id,
        "name": upgrade.name,
        "description": upgrade.description,
        "gold_cost": upgrade.gold_cost,
        "stats": _stat_modifier_to_dict(upgrade.stat_modifier),
        "party_size_bonus": upgrade.party_size_bonus,
        "skills": [_skill_to_dict(skill) for skill in upgrade.skills],
        "required_upgrade_ids": list(upgrade.required_upgrade_ids),
        "unlocked": upgrade.upgrade_id in state.unlocked_upgrade_ids,
        "can_purchase": not missing,
        "missing": _json_friendly_mapping(missing),
    }


def _skill_to_dict(skill: Skill) -> dict[str, Any]:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "kind": skill.kind,
        "condition": _condition_to_dict(skill.condition),
        "effects": [_effect_to_dict(effect) for effect in skill.effects],
        "mp_cost": skill.mp_cost,
        "priority": skill.priority,
        "once_per_battle": skill.once_per_battle,
        "free": skill.free,
    }


def _condition_to_dict(condition: SkillCondition) -> dict[str, Any]:
    return {
        "type": condition.condition_type,
        "value": condition.value,
        "conditions": [
            _condition_to_dict(child)
            for child in condition.conditions
        ],
    }


def _effect_to_dict(effect: SkillEffect) -> dict[str, Any]:
    return {
        "type": effect.effect_type,
        "value": effect.value,
        "stat": effect.stat,
        "target": effect.target,
        "status": (
            _status_to_dict(effect.status)
            if effect.status is not None
            else None
        ),
    }


def _status_to_dict(status: StatusDefinition) -> dict[str, Any]:
    return {
        "status_id": status.status_id,
        "name": status.name,
        "duration": status.duration,
        "polarity": status.polarity,
        "stack_mode": status.stack_mode,
        "effects": [_effect_to_dict(effect) for effect in status.effects],
    }


def _stats_to_dict(stats: CombatStats) -> dict[str, int]:
    return {
        "hp": stats.hp,
        "mp": stats.mp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed,
        "recovery": stats.recovery,
        "mp_recovery": stats.mp_recovery,
    }


def _stat_modifier_to_dict(stats: CombatStatModifier) -> dict[str, int]:
    return {
        "hp": stats.hp,
        "mp": stats.mp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed,
        "recovery": stats.recovery,
        "mp_recovery": stats.mp_recovery,
    }


def _resources_to_dict(resources: CombatResources) -> dict[str, int]:
    return {
        "current_hp": resources.current_hp,
        "current_mp": resources.current_mp,
    }


def _experience_rules_to_dict(definition: GameDefinition) -> dict[str, Any]:
    rules = definition.content.experience_rules
    return {
        "base_required_experience": rules.base_required_experience,
        "required_experience_growth": rules.required_experience_growth,
        "max_level": rules.max_level,
        "stat_growth_per_level": _stat_modifier_to_dict(rules.stat_growth_per_level),
    }


def _turn_recovery_rules_to_dict(definition: GameDefinition) -> dict[str, Any]:
    rules = definition.rules.turn_recovery
    return {
        "hp": rules.hp,
        "mp": rules.mp,
        "hp_percent": rules.hp_percent,
        "mp_percent": rules.mp_percent,
        "use_recovery_stat": rules.use_recovery_stat,
    }


def _scoring_rules_to_dict(definition: GameDefinition) -> dict[str, Any]:
    rules = definition.scoring
    return {
        "mode": rules.mode,
        "seed": rules.seed,
        "waves": rules.waves,
        "wave_size": rules.wave_size,
        "difficulty_factors": list(rules.difficulty_factors),
        "resource_mode": rules.resource_mode,
        "aggregation": rules.aggregation,
    }


def _next_level_info(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> dict[str, Any]:
    rules = definition.content.experience_rules
    if adventurer.level >= rules.max_level:
        return {
            "max_level": True,
            "required": 0,
            "current": 0,
            "remaining": 0,
            "preview_level": adventurer.level,
            "preview_experience": 0,
            "preview_stats": _stats_to_dict(
                effective_adventurer_stats(definition, state, adventurer)
            ),
            "preview_skills": [
                _skill_to_dict(skill)
                for skill in effective_adventurer_skills(definition, state, adventurer)
            ],
            "preview_level_skill_unlocks": [],
        }

    required = required_experience_for_next_level(adventurer.level, rules)
    preview_level, preview_experience = add_experience(
        level=adventurer.level,
        experience=adventurer.experience,
        amount=state.experience_pool,
        rules=rules,
    )
    preview_adventurer = AdventurerState(
        adventurer_id=adventurer.adventurer_id,
        name=adventurer.name,
        base_stats=adventurer.base_stats,
        resources=adventurer.resources,
        skills=adventurer.skills,
        level_skill_unlocks=adventurer.level_skill_unlocks,
        stat_growth_per_level=adventurer.stat_growth_per_level,
        level=preview_level,
        experience=preview_experience,
        equipment=adventurer.equipment,
    )
    return {
        "max_level": False,
        "required": required,
        "current": adventurer.experience,
        "remaining": max(0, required - adventurer.experience),
        "preview_level": preview_level,
        "preview_experience": preview_experience,
        "preview_stats": _stats_to_dict(
            effective_adventurer_stats(definition, state, preview_adventurer)
        ),
        "preview_skills": [
            _skill_to_dict(skill)
            for skill in effective_adventurer_skills(definition, state, preview_adventurer)
        ],
        "preview_level_skill_unlocks": [
            _level_skill_unlock_to_dict(unlock, adventurer.level)
            for unlock in adventurer.level_skill_unlocks
            if adventurer.level < unlock.level <= preview_level
        ],
    }


def _level_skill_unlocks_to_dict(adventurer: AdventurerState) -> list[dict[str, Any]]:
    return [
        _level_skill_unlock_to_dict(unlock, adventurer.level)
        for unlock in adventurer.level_skill_unlocks
    ]


def _level_skill_unlock_to_dict(unlock, current_level: int) -> dict[str, Any]:
    return {
        "level": unlock.level,
        "unlocked": unlock.level <= current_level,
        "skills": [_skill_to_dict(skill) for skill in unlock.skills],
    }


def _equipment_slots_to_dict(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
) -> list[dict[str, Any]]:
    instances = {
        item.instance_id: item
        for item in state.equipment_inventory
    }
    templates = {
        item.equipment_id: item
        for item in definition.content.equipment_templates
    }
    equipped = {
        item.slot: item.instance_id
        for item in adventurer.equipment.items
    }
    blocked_by = _blocked_equipment_slots(equipped)
    slots = []
    for slot in (
        "main_hand",
        "off_hand",
        "two_hand",
        "boots",
        "helmet",
        "armor",
        "accessory",
    ):
        instance_id = equipped.get(slot)
        item_data = None
        if instance_id is not None:
            instance = instances[instance_id]
            template = templates[instance.template_id]
            item_data = {
                "instance_id": instance.instance_id,
                "template_id": instance.template_id,
                "name": template.name,
                "slot": template.slot,
                "stats": _stat_modifier_to_dict(template.stat_modifier),
                "skills": [_skill_to_dict(skill) for skill in template.skills],
            }
        slots.append(
            {
                "slot": slot,
                "item": item_data,
                "blocked_by": blocked_by.get(slot),
            }
        )
    return slots


def _adventurer_level_growth(
    definition: GameDefinition,
    adventurer: AdventurerState,
) -> CombatStatModifier:
    return (
        adventurer.stat_growth_per_level
        if adventurer.stat_growth_per_level is not None
        else definition.content.experience_rules.stat_growth_per_level
    )


def _blocked_equipment_slots(equipped: dict[str, str]) -> dict[str, str]:
    if "two_hand" in equipped:
        return {
            "main_hand": "two_hand",
            "off_hand": "two_hand",
        }
    if "main_hand" in equipped or "off_hand" in equipped:
        return {"two_hand": "hand"}
    return {}


def _reward_to_dict(reward: RewardBundle) -> dict[str, Any]:
    return {
        "gold": reward.gold,
        "experience": reward.experience,
        "materials": dict(reward.materials),
    }


def _equipped_by_adventurer_id(state: GameState) -> dict[str, str]:
    equipped_by = {}
    for adventurer in state.adventurers:
        for item in adventurer.equipment.items:
            equipped_by[item.instance_id] = adventurer.adventurer_id
    return equipped_by


def _equipment_template_by_id(definition: GameDefinition, template_id: str):
    for template in definition.content.equipment_templates:
        if template.equipment_id == template_id:
            return template
    raise ValueError(f"unknown equipment template: {template_id}")


def _json_friendly_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in values.items()
    }
