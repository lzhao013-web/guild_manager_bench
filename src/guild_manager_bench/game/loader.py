from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from guild_manager_bench.game.crafting import CraftingRecipe
from guild_manager_bench.game.equipment import EquipmentTemplate
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.progression import ExperienceRules
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.game.state import (
    AdventurerState,
    GameContent,
    GameDefinition,
    GameRules,
    IntCurve,
    MonsterArchetype,
    MonsterSpawnRules,
    RewardBundle,
    TurnRecoveryRules,
)
from guild_manager_bench.game.upgrades import GlobalUpgrade


class YamlLoadError(ValueError):
    """YAML 数据加载失败。"""


def load_game_definition(data_dir: str | Path) -> GameDefinition:
    """从数据目录加载完整游戏定义。"""

    data_path = Path(data_dir)
    game_data = _load_mapping(data_path / "game.yaml")
    adventurer_data = _load_yaml(data_path / "adventurers.yaml")
    monster_data = _load_yaml(data_path / "monsters.yaml")
    equipment_data = _load_yaml(data_path / "equipment.yaml")
    recipe_data = _load_yaml(data_path / "crafting_recipes.yaml")
    upgrade_data = _load_yaml(data_path / "global_upgrades.yaml")

    rules = _parse_game_rules(_mapping(_required(game_data, "rules"), "rules"))
    experience_rules = _parse_experience_rules(_mapping(game_data.get("experience", {}), "experience"))
    content = GameContent(
        adventurers=_parse_adventurers(_list_section(adventurer_data, "adventurers")),
        monster_archetypes=_parse_monsters(_list_section(monster_data, "monsters")),
        equipment_templates=_parse_equipment(_list_section(equipment_data, "equipment")),
        crafting_recipes=_parse_recipes(_list_section(recipe_data, "recipes")),
        global_upgrades=_parse_upgrades(_list_section(upgrade_data, "upgrades")),
        experience_rules=experience_rules,
    )
    starting = _mapping(game_data.get("starting", {}), "starting")
    return GameDefinition(
        content=content,
        rules=rules,
        starting_gold=_int(starting.get("gold", 0), "starting.gold"),
        starting_materials=_int_mapping(starting.get("materials", {}), "starting.materials"),
    )


def _parse_adventurers(values: list[Any]) -> tuple[AdventurerState, ...]:
    adventurers: list[AdventurerState] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"adventurers[{index}]")
        stats = _parse_combat_stats(_mapping(_required(data, "stats"), f"adventurers[{index}].stats"))
        adventurers.append(
            AdventurerState(
                adventurer_id=_str(_id_field(data), f"adventurers[{index}].id"),
                name=_str(_required(data, "name"), f"adventurers[{index}].name"),
                base_stats=stats,
                resources=CombatResources.full(stats),
                skills=_parse_skills(data.get("skills", ()), f"adventurers[{index}].skills"),
                level=_int(data.get("level", 1), f"adventurers[{index}].level"),
                experience=_int(data.get("experience", 0), f"adventurers[{index}].experience"),
            )
        )
    return tuple(adventurers)


def _parse_monsters(values: list[Any]) -> tuple[MonsterArchetype, ...]:
    monsters: list[MonsterArchetype] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"monsters[{index}]")
        monsters.append(
            MonsterArchetype(
                archetype_id=_str(_id_field(data), f"monsters[{index}].id"),
                name=_str(_required(data, "name"), f"monsters[{index}].name"),
                base_stats=_parse_combat_stats(
                    _mapping(_required(data, "base_stats"), f"monsters[{index}].base_stats")
                ),
                base_reward=_parse_reward(
                    _mapping(_required(data, "base_reward"), f"monsters[{index}].base_reward")
                ),
                stat_growth=_parse_stat_modifier(
                    _mapping(data.get("stat_growth", {}), f"monsters[{index}].stat_growth")
                ),
                reward_growth=_parse_reward(
                    _mapping(data.get("reward_growth", {}), f"monsters[{index}].reward_growth")
                ),
                skills=_parse_skills(data.get("skills", ()), f"monsters[{index}].skills"),
            )
        )
    return tuple(monsters)


def _parse_equipment(values: list[Any]) -> tuple[EquipmentTemplate, ...]:
    equipment: list[EquipmentTemplate] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"equipment[{index}]")
        equipment.append(
            EquipmentTemplate(
                equipment_id=_str(_id_field(data), f"equipment[{index}].id"),
                name=_str(_required(data, "name"), f"equipment[{index}].name"),
                slot=_str(_required(data, "slot"), f"equipment[{index}].slot"),
                stat_modifier=_parse_stat_modifier(
                    _mapping(_stat_modifier_data(data), f"equipment[{index}].stats")
                ),
                skills=_parse_skills(data.get("skills", ()), f"equipment[{index}].skills"),
            )
        )
    return tuple(equipment)


def _parse_recipes(values: list[Any]) -> tuple[CraftingRecipe, ...]:
    recipes: list[CraftingRecipe] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"recipes[{index}]")
        recipes.append(
            CraftingRecipe.from_mapping(
                recipe_id=_str(_id_field(data), f"recipes[{index}].id"),
                name=_str(_required(data, "name"), f"recipes[{index}].name"),
                output_template_id=_str(_required(data, "output"), f"recipes[{index}].output"),
                material_costs=_int_mapping(data.get("materials", {}), f"recipes[{index}].materials"),
                gold_cost=_int(data.get("gold", 0), f"recipes[{index}].gold"),
            )
        )
    return tuple(recipes)


def _parse_upgrades(values: list[Any]) -> tuple[GlobalUpgrade, ...]:
    upgrades: list[GlobalUpgrade] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"upgrades[{index}]")
        upgrades.append(
            GlobalUpgrade(
                upgrade_id=_str(_id_field(data), f"upgrades[{index}].id"),
                name=_str(_required(data, "name"), f"upgrades[{index}].name"),
                gold_cost=_int(data.get("gold", data.get("gold_cost", 0)), f"upgrades[{index}].gold"),
                stat_modifier=_parse_stat_modifier(
                    _mapping(_stat_modifier_data(data), f"upgrades[{index}].stats")
                ),
                skills=_parse_skills(data.get("skills", ()), f"upgrades[{index}].skills"),
                required_upgrade_ids=tuple(
                    _str(item, f"upgrades[{index}].required[{required_index}]")
                    for required_index, item in enumerate(data.get("required", ()))
                ),
            )
        )
    return tuple(upgrades)


def _parse_game_rules(data: Mapping[str, Any]) -> GameRules:
    spawn = _mapping(_required(data, "monster_spawn"), "rules.monster_spawn")
    return GameRules(
        max_turns=_int(_required(data, "max_turns"), "rules.max_turns"),
        seed=_int(data.get("seed", 0), "rules.seed"),
        monster_spawn=MonsterSpawnRules(
            count_curve=_parse_int_curve(_mapping(_required(spawn, "count_curve"), "rules.monster_spawn.count_curve")),
            stat_growth_curve=_parse_int_curve(
                _mapping(spawn.get("stat_growth_curve", {}), "rules.monster_spawn.stat_growth_curve")
            ),
            reward_growth_curve=_parse_int_curve(
                _mapping(spawn.get("reward_growth_curve", {}), "rules.monster_spawn.reward_growth_curve")
            ),
        ),
        turn_recovery=_parse_turn_recovery(
            _mapping(data.get("turn_recovery", {}), "rules.turn_recovery")
        ),
    )


def _parse_turn_recovery(data: Mapping[str, Any]) -> TurnRecoveryRules:
    return TurnRecoveryRules(
        hp=_int(data.get("hp", 0), "rules.turn_recovery.hp"),
        mp=_int(data.get("mp", 0), "rules.turn_recovery.mp"),
        hp_percent=_number(data.get("hp_percent", 0.0), "rules.turn_recovery.hp_percent"),
        mp_percent=_number(data.get("mp_percent", 0.0), "rules.turn_recovery.mp_percent"),
        use_recovery_stat=_bool(
            data.get("use_recovery_stat", True),
            "rules.turn_recovery.use_recovery_stat",
        ),
    )


def _parse_experience_rules(data: Mapping[str, Any]) -> ExperienceRules:
    return ExperienceRules(
        base_required_experience=_int(data.get("base_required_experience", 100), "experience.base_required_experience"),
        required_experience_growth=_int(data.get("required_experience_growth", 50), "experience.required_experience_growth"),
        max_level=_int(data.get("max_level", 99), "experience.max_level"),
        stat_growth_per_level=_parse_stat_modifier(
            _mapping(data.get("stat_growth_per_level", {}), "experience.stat_growth_per_level")
        ),
    )


def _parse_int_curve(data: Mapping[str, Any]) -> IntCurve:
    return IntCurve(
        base=_int(data.get("base", 0), "curve.base"),
        per_turn=_int(data.get("per_turn", 0), "curve.per_turn"),
        minimum=_int(data.get("minimum", 0), "curve.minimum"),
        maximum=None if data.get("maximum") is None else _int(data.get("maximum"), "curve.maximum"),
    )


def _parse_combat_stats(data: Mapping[str, Any]) -> CombatStats:
    return CombatStats(
        hp=_int(_required(data, "hp"), "stats.hp"),
        mp=_int(data.get("mp", 0), "stats.mp"),
        attack=_int(_required(data, "attack"), "stats.attack"),
        defense=_int(_required(data, "defense"), "stats.defense"),
        speed=_int(_required(data, "speed"), "stats.speed"),
        recovery=_int(data.get("recovery", 0), "stats.recovery"),
    )


def _parse_stat_modifier(data: Mapping[str, Any]) -> CombatStatModifier:
    return CombatStatModifier(
        hp=_int(data.get("hp", 0), "stat_modifier.hp"),
        mp=_int(data.get("mp", 0), "stat_modifier.mp"),
        attack=_int(data.get("attack", 0), "stat_modifier.attack"),
        defense=_int(data.get("defense", 0), "stat_modifier.defense"),
        speed=_int(data.get("speed", 0), "stat_modifier.speed"),
        recovery=_int(data.get("recovery", 0), "stat_modifier.recovery"),
    )


def _parse_reward(data: Mapping[str, Any]) -> RewardBundle:
    return RewardBundle(
        gold=_int(data.get("gold", 0), "reward.gold"),
        experience=_int(data.get("experience", 0), "reward.experience"),
        materials=_int_mapping(data.get("materials", {}), "reward.materials"),
    )


def _parse_skills(value: Any, path: str) -> tuple[Skill, ...]:
    skills = []
    for index, item in enumerate(_list(value, path)):
        data = _mapping(item, f"{path}[{index}]")
        skills.append(
            Skill(
                skill_id=_str(_id_field(data), f"{path}[{index}].id"),
                name=_str(_required(data, "name"), f"{path}[{index}].name"),
                kind=_str(_required(data, "kind"), f"{path}[{index}].kind"),
                condition=_parse_condition(_mapping(_required(data, "condition"), f"{path}[{index}].condition")),
                effects=_parse_effects(_list(_required(data, "effects"), f"{path}[{index}].effects")),
                mp_cost=_int(data.get("mp_cost", 0), f"{path}[{index}].mp_cost"),
                priority=_int(data.get("priority", 0), f"{path}[{index}].priority"),
                once_per_battle=_bool(data.get("once_per_battle", False), f"{path}[{index}].once_per_battle"),
            )
        )
    return tuple(skills)


def _parse_condition(data: Mapping[str, Any]) -> SkillCondition:
    condition_type = _str(data.get("type", data.get("condition_type")), "condition.type")
    if condition_type in {"all", "any"}:
        return SkillCondition(
            condition_type=condition_type,
            conditions=tuple(
                _parse_condition(_mapping(item, f"condition.conditions[{index}]"))
                for index, item in enumerate(_list(_required(data, "conditions"), "condition.conditions"))
            ),
        )
    return SkillCondition(
        condition_type=condition_type,
        value=None if data.get("value") is None else _number(data.get("value"), "condition.value"),
    )


def _parse_effects(values: list[Any]) -> tuple[SkillEffect, ...]:
    effects = []
    for index, item in enumerate(values):
        data = _mapping(item, f"effects[{index}]")
        effects.append(
            SkillEffect(
                effect_type=_str(data.get("type", data.get("effect_type")), f"effects[{index}].type"),
                value=_number(_required(data, "value"), f"effects[{index}].value"),
                stat=None if data.get("stat") is None else _str(data.get("stat"), f"effects[{index}].stat"),
                target=_str(data.get("target", "target"), f"effects[{index}].target"),
            )
        )
    return tuple(effects)


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        value = yaml.safe_load(file)
    return {} if value is None else value


def _load_mapping(path: Path) -> Mapping[str, Any]:
    return _mapping(_load_yaml(path), str(path))


def _list_section(value: Any, key: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        return _list(value.get(key, ()), key)
    if value is None:
        return []
    raise YamlLoadError(f"{key} must be a list or mapping")


def _stat_modifier_data(data: Mapping[str, Any]) -> Any:
    return data.get("stats", data.get("stat_modifier", {}))


def _id_field(data: Mapping[str, Any]) -> Any:
    if "id" in data:
        return data["id"]
    id_keys = sorted(key for key in data if isinstance(key, str) and key.endswith("_id"))
    if len(id_keys) == 1:
        return data[id_keys[0]]
    return None


def _required(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise YamlLoadError(f"missing required field: {key}")
    return data[key]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise YamlLoadError(f"{path} must be a mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise YamlLoadError(f"{path} must be a list")
    return list(value)


def _int_mapping(value: Any, path: str) -> dict[str, int]:
    data = _mapping(value, path)
    return {
        _str(key, f"{path}.key"): _int(item, f"{path}.{key}")
        for key, item in data.items()
    }


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise YamlLoadError(f"{path} must be a non-empty string")
    return value


def _int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise YamlLoadError(f"{path} must be an integer")
    return value


def _number(value: Any, path: str) -> int | float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise YamlLoadError(f"{path} must be a number")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise YamlLoadError(f"{path} must be a bool")
    return value
