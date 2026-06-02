from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from guild_manager_bench.game.crafting import CraftingRecipe
from guild_manager_bench.game.equipment import EquipmentTemplate
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.progression import ExperienceRules
from guild_manager_bench.game.skills import (
    Skill,
    SkillCondition,
    SkillEffect,
    StatusDefinition,
)
from guild_manager_bench.game.state import (
    AdventurerState,
    FloatCurve,
    GameContent,
    GameDefinition,
    GameRules,
    IntCurve,
    LevelSkillUnlock,
    LlmToolRules,
    MonsterArchetype,
    MonsterSpawnRules,
    MonsterTierConfig,
    RecruitableAdventurerTemplate,
    RecruitVariationConfig,
    RecruitmentRules,
    RewardBundle,
    ScoringRules,
    SkillTheme,
    StatSuffixMapping,
    TurnRecoveryRules,
)
from guild_manager_bench.game.upgrades import GlobalUpgrade


class YamlLoadError(ValueError):
    """YAML 数据加载失败。"""


def _build_skill_registry(data: Any) -> dict[str, Skill]:
    if not data:
        return {}
    skills = _parse_skills(_list_section(data, "skills"), "skills")
    registry: dict[str, Skill] = {}
    for skill in skills:
        if skill.skill_id in registry:
            raise YamlLoadError(f"duplicate skill id: {skill.skill_id}")
        registry[skill.skill_id] = skill
    return registry


def _resolve_skills(value: Any, registry: dict[str, Skill], path: str) -> tuple[Skill, ...]:
    if not value:
        return ()
    items = _list(value, path)
    if not items:
        return ()
    # If first item is a string, treat as ID references; otherwise parse inline.
    if isinstance(items[0], str):
        resolved: list[Skill] = []
        for index, skill_id in enumerate(items):
            skill = registry.get(skill_id)
            if skill is None:
                raise YamlLoadError(f"{path}[{index}]: unknown skill id '{skill_id}'")
            resolved.append(skill)
        return tuple(resolved)
    return _parse_skills(value, path)


def load_game_definition(data_dir: str | Path) -> GameDefinition:
    """从数据目录加载完整游戏定义。"""

    data_path = Path(data_dir)
    game_data = _load_mapping(data_path / "game.yaml")
    skill_data = _load_yaml(data_path / "skills.yaml")
    adventurer_data = _load_yaml(data_path / "adventurers.yaml")
    monster_data = _load_yaml(data_path / "monsters.yaml")
    equipment_data = _load_yaml(data_path / "equipment.yaml")
    recipe_data = _load_yaml(data_path / "crafting_recipes.yaml")
    upgrade_data = _load_yaml(data_path / "global_upgrades.yaml")
    tier_data = _load_yaml(data_path / "monster_tiers.yaml")

    skill_registry = _build_skill_registry(skill_data)
    tier_configs_data = _parse_tier_configs(tier_data, skill_registry)
    rules = _parse_game_rules(
        _mapping(_required(game_data, "rules"), "rules"),
        tier_configs=tier_configs_data,
    )
    experience_rules = _parse_experience_rules(_mapping(game_data.get("experience", {}), "experience"))
    content = GameContent(
        adventurers=_parse_adventurers(_list_section(adventurer_data, "adventurers"), skill_registry),
        recruitable_adventurers=_parse_recruitable_adventurers(
            _list_section(adventurer_data, "recruitable_adventurers"), skill_registry
        ),
        monster_archetypes=_parse_monsters(_list_section(monster_data, "monsters"), skill_registry),
        equipment_templates=_parse_equipment(_list_section(equipment_data, "equipment"), skill_registry),
        crafting_recipes=_parse_recipes(_list_section(recipe_data, "recipes")),
        global_upgrades=_parse_upgrades(_list_section(upgrade_data, "upgrades"), skill_registry),
        experience_rules=experience_rules,
    )
    starting = _mapping(game_data.get("starting", {}), "starting")
    return GameDefinition(
        content=content,
        rules=rules,
        starting_gold=_int(starting.get("gold", 0), "starting.gold"),
        starting_materials=_int_mapping(starting.get("materials", {}), "starting.materials"),
        llm_tools=_parse_llm_tool_rules(_mapping(game_data.get("llm", {}), "llm")),
        scoring=_parse_scoring_rules(_mapping(game_data.get("scoring", {}), "scoring")),
    )


def _parse_adventurers(values: list[Any], registry: dict[str, Skill]) -> tuple[AdventurerState, ...]:
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
                skills=_resolve_skills(data.get("skills", ()), registry, f"adventurers[{index}].skills"),
                level_skill_unlocks=_parse_level_skill_unlocks(
                    data.get("level_skill_unlocks", ()),
                    f"adventurers[{index}].level_skill_unlocks",
                    registry,
                ),
                stat_growth_per_level=_parse_optional_stat_modifier(
                    data.get("stat_growth_per_level"),
                    f"adventurers[{index}].stat_growth_per_level",
                ),
                level=_int(data.get("level", 1), f"adventurers[{index}].level"),
                experience=_int(data.get("experience", 0), f"adventurers[{index}].experience"),
            )
        )
    return tuple(adventurers)


def _parse_recruitable_adventurers(values: list[Any], registry: dict[str, Skill]) -> tuple[RecruitableAdventurerTemplate, ...]:
    adventurers: list[RecruitableAdventurerTemplate] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"recruitable_adventurers[{index}]")
        adventurers.append(
            RecruitableAdventurerTemplate(
                template_id=_str(_id_field(data), f"recruitable_adventurers[{index}].id"),
                name=_str(_required(data, "name"), f"recruitable_adventurers[{index}].name"),
                recruit_gold=_int(
                    data.get("recruit_gold", data.get("gold", 0)),
                    f"recruitable_adventurers[{index}].recruit_gold",
                ),
                base_stats=_parse_combat_stats(
                    _mapping(_required(data, "stats"), f"recruitable_adventurers[{index}].stats")
                ),
                skills=_resolve_skills(
                    data.get("skills", ()),
                    registry,
                    f"recruitable_adventurers[{index}].skills",
                ),
                level_skill_unlocks=_parse_level_skill_unlocks(
                    data.get("level_skill_unlocks", ()),
                    f"recruitable_adventurers[{index}].level_skill_unlocks",
                    registry,
                ),
                stat_growth_per_level=_parse_stat_modifier(
                    _mapping(
                        data.get("stat_growth_per_level", {}),
                        f"recruitable_adventurers[{index}].stat_growth_per_level",
                    )
                ),
            )
        )
    return tuple(adventurers)


def _parse_monsters(values: list[Any], registry: dict[str, Skill]) -> tuple[MonsterArchetype, ...]:
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
                spawn_weight=_int(data.get("spawn_weight", 1), f"monsters[{index}].spawn_weight"),
                min_turn=_int(data.get("min_turn", 1), f"monsters[{index}].min_turn"),
                stat_growth=_parse_stat_modifier(
                    _mapping(data.get("stat_growth", {}), f"monsters[{index}].stat_growth")
                ),
                reward_growth=_parse_reward(
                    _mapping(data.get("reward_growth", {}), f"monsters[{index}].reward_growth")
                ),
                skills=_resolve_skills(data.get("skills", ()), registry, f"monsters[{index}].skills"),
            )
        )
    return tuple(monsters)


def _parse_equipment(values: list[Any], registry: dict[str, Skill]) -> tuple[EquipmentTemplate, ...]:
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
                skills=_resolve_skills(data.get("skills", ()), registry, f"equipment[{index}].skills"),
                allowed_classes=tuple(
                    _str(item, f"equipment[{index}].allowed_classes[{ci}]")
                    for ci, item in enumerate(data.get("allowed_classes", ()))
                ),
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


def _parse_upgrades(values: list[Any], registry: dict[str, Skill]) -> tuple[GlobalUpgrade, ...]:
    upgrades: list[GlobalUpgrade] = []
    for index, value in enumerate(values):
        data = _mapping(value, f"upgrades[{index}]")
        upgrades.append(
            GlobalUpgrade(
                upgrade_id=_str(_id_field(data), f"upgrades[{index}].id"),
                name=_str(_required(data, "name"), f"upgrades[{index}].name"),
                gold_cost=_int(data.get("gold", data.get("gold_cost", 0)), f"upgrades[{index}].gold"),
                description=_opt_str(data.get("description"), f"upgrades[{index}].description"),
                stat_modifier=_parse_stat_modifier(
                    _mapping(_stat_modifier_data(data), f"upgrades[{index}].stats")
                ),
                skills=_resolve_skills(data.get("skills", ()), registry, f"upgrades[{index}].skills"),
                required_upgrade_ids=tuple(
                    _str(item, f"upgrades[{index}].required[{required_index}]")
                    for required_index, item in enumerate(data.get("required", ()))
                ),
                party_size_bonus=_int(
                    data.get("party_size_bonus", 0),
                    f"upgrades[{index}].party_size_bonus",
                ),
            )
        )
    return tuple(upgrades)


def _parse_game_rules(
    data: Mapping[str, Any],
    tier_configs: tuple[tuple[SkillTheme, ...], dict[str, MonsterTierConfig]],
) -> GameRules:
    themes, tier_dict = tier_configs
    spawn = _mapping(_required(data, "monster_spawn"), "rules.monster_spawn")
    return GameRules(
        max_turns=_int(_required(data, "max_turns"), "rules.max_turns"),
        seed=_int(data.get("seed", 0), "rules.seed"),
        monster_spawn=MonsterSpawnRules(
            count_curve=_parse_float_curve(_mapping(_required(spawn, "count_curve"), "rules.monster_spawn.count_curve")),
            stat_growth_curve=_parse_float_curve(
                _mapping(spawn.get("stat_growth_curve", {}), "rules.monster_spawn.stat_growth_curve")
            ),
            reward_growth_curve=_parse_float_curve(
                _mapping(spawn.get("reward_growth_curve", {}), "rules.monster_spawn.reward_growth_curve")
            ),
            elite=tier_dict.get("elite", MonsterTierConfig()),
            boss=tier_dict.get("boss", MonsterTierConfig()),
            bonus_skill_themes=themes,
        ),
        turn_recovery=_parse_turn_recovery(
            _mapping(data.get("turn_recovery", {}), "rules.turn_recovery")
        ),
        recruitment=_parse_recruitment_rules(
            _mapping(data.get("recruitment", {}), "rules.recruitment")
        ),
    )


def _parse_recruitment_rules(data: Mapping[str, Any]) -> RecruitmentRules:
    return RecruitmentRules(
        candidate_count=_int(data.get("candidate_count", 3), "rules.recruitment.candidate_count"),
        first_turn_candidate_count=(
            None
            if data.get("first_turn_candidate_count") is None
            else _int(
                data.get("first_turn_candidate_count"),
                "rules.recruitment.first_turn_candidate_count",
            )
        ),
        initial_party_size_limit=_int(
            data.get("initial_party_size_limit", 3),
            "rules.recruitment.initial_party_size_limit",
        ),
        maximum_party_size_limit=_int(
            data.get("maximum_party_size_limit", 6),
            "rules.recruitment.maximum_party_size_limit",
        ),
        variation=_parse_variation_config(_mapping(data.get("variation", {}), "rules.recruitment.variation")),
    )


def _parse_variation_config(data: Mapping[str, Any]) -> RecruitVariationConfig:
    raw_range = data.get("price_factor_range", [0.85, 1.15])
    if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
        raise ValueError("variation.price_factor_range must be a 2-element list")
    suffix_data = data.get("suffix_mapping", {})
    suffix_mapping: dict[str, StatSuffixMapping] = {}
    if isinstance(suffix_data, Mapping):
        for stat_key, entry in suffix_data.items():
            if isinstance(entry, Mapping):
                suffix_mapping[stat_key] = StatSuffixMapping(
                    positive=str(entry.get("positive", "")),
                    negative=str(entry.get("negative", "")),
                )
    return RecruitVariationConfig(
        price_factor_range=(float(raw_range[0]), float(raw_range[1])),
        stats_to_vary=_int(data.get("stats_to_vary", 2), "rules.recruitment.variation.stats_to_vary"),
        stat_variation_ratio=float(data.get("stat_variation_ratio", 0.12)),
        hp_variation_ratio=float(data.get("hp_variation_ratio", 0.08)),
        hp_min_variation=_int(data.get("hp_min_variation", 4), "rules.recruitment.variation.hp_min_variation"),
        stat_min_variation=_int(data.get("stat_min_variation", 1), "rules.recruitment.variation.stat_min_variation"),
        growth_to_vary=_int(data.get("growth_to_vary", 1), "rules.recruitment.variation.growth_to_vary"),
        growth_variation_amount=_int(data.get("growth_variation_amount", 1), "rules.recruitment.variation.growth_variation_amount"),
        price_stat_adjustment_ratio=float(data.get("price_stat_adjustment_ratio", 0.0)),
        suffix_mapping=suffix_mapping,
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


def _parse_llm_tool_rules(data: Mapping[str, Any]) -> LlmToolRules:
    return LlmToolRules(
        expose_battle_preview=_bool(
            data.get("expose_battle_preview", False),
            "llm.expose_battle_preview",
        ),
        max_battle_preview_per_turn=_int(
            data.get("max_battle_preview_per_turn", 3),
            "llm.max_battle_preview_per_turn",
        ),
    )


def _parse_scoring_rules(data: Mapping[str, Any]) -> ScoringRules:
    difficulty_values = data.get("difficulty_factors", (8, 10, 12, 14))
    return ScoringRules(
        mode=_str(data.get("mode", "endgame_arena"), "scoring.mode"),
        seed=_int(data.get("seed", 20260526), "scoring.seed"),
        waves=_int(data.get("waves", 256), "scoring.waves"),
        wave_size=_int(data.get("wave_size", 6), "scoring.wave_size"),
        difficulty_factors=tuple(
            _int(value, f"scoring.difficulty_factors[{index}]")
            for index, value in enumerate(_list(difficulty_values, "scoring.difficulty_factors"))
        ),
        resource_mode=_str(data.get("resource_mode", "full"), "scoring.resource_mode"),
        aggregation=_str(data.get("aggregation", "best_assignment"), "scoring.aggregation"),
        elite_chance=float(data.get("elite_chance", 0.0)),
        elite_stat_multiplier=float(data.get("elite_stat_multiplier", 1.0)),
        boss_chance=float(data.get("boss_chance", 0.0)),
        boss_stat_multiplier=float(data.get("boss_stat_multiplier", 1.0)),
        rank_min_diff=_int(data.get("rank_min_diff", 1), "scoring.rank_min_diff"),
        rank_max_diff=_int(data.get("rank_max_diff", 300), "scoring.rank_max_diff"),
        rank_step=_int(data.get("rank_step", 5), "scoring.rank_step"),
        rank_waves=_int(data.get("rank_waves", 50), "scoring.rank_waves"),
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


def _parse_float_curve(data: Mapping[str, Any]) -> FloatCurve:
    return FloatCurve(
        base=float(data.get("base", 0.0)),
        per_turn=float(data.get("per_turn", 0.0)),
        minimum=float(data.get("minimum", 0.0)),
        maximum=None if data.get("maximum") is None else float(data.get("maximum")),
    )


def _parse_tier_configs(
    data: Any, registry: dict[str, Skill]
) -> tuple[tuple[SkillTheme, ...], dict[str, MonsterTierConfig]]:
    if not data:
        return (), {}
    mapping = _mapping(data, "monster_tiers")
    themes = _parse_skill_themes(mapping.get("bonus_skill_themes", ()), registry, "monster_tiers.bonus_skill_themes")
    tiers_data = _mapping(mapping.get("tiers", {}), "monster_tiers.tiers")
    result: dict[str, MonsterTierConfig] = {}
    for tier_name in ("elite", "boss"):
        tier_data = tiers_data.get(tier_name)
        if tier_data is None:
            continue
        td = _mapping(tier_data, f"monster_tiers.tiers.{tier_name}")
        result[tier_name] = _parse_tier_config(td, f"monster_tiers.tiers.{tier_name}")
    return themes, result


def _parse_skill_themes(
    value: Any, registry: dict[str, Skill], path: str
) -> tuple[SkillTheme, ...]:
    themes: list[SkillTheme] = []
    for index, item in enumerate(_list(value, path)):
        data = _mapping(item, f"{path}[{index}]")
        themes.append(
            SkillTheme(
                theme_id=_str(_id_field(data), f"{path}[{index}].id"),
                name=_str(_required(data, "name"), f"{path}[{index}].name"),
                skills=_resolve_skills(data.get("skills", ()), registry, f"{path}[{index}].skills"),
            )
        )
    return tuple(themes)


def _parse_tier_config(data: Mapping[str, Any], path: str) -> MonsterTierConfig:
    bonus_growth_data = data.get("bonus_reward_growth")
    bonus_reward_growth = (
        _parse_reward(_mapping(bonus_growth_data, f"{path}.bonus_reward_growth"))
        if bonus_growth_data is not None
        else RewardBundle()
    )
    return MonsterTierConfig(
        chance=float(data.get("chance", 0.0)),
        stat_multiplier=float(data.get("stat_multiplier", 1.0)),
        reward_multiplier=float(data.get("reward_multiplier", 1.0)),
        bonus_reward_growth=bonus_reward_growth,
        name_prefix=str(data.get("name_prefix", "")),
        bonus_skill_count=_int(data.get("bonus_skill_count", 0), f"{path}.bonus_skill_count"),
    )


def _parse_combat_stats(data: Mapping[str, Any]) -> CombatStats:
    return CombatStats(
        hp=_int(_required(data, "hp"), "stats.hp"),
        mp=_int(data.get("mp", 0), "stats.mp"),
        attack=_int(_required(data, "attack"), "stats.attack"),
        defense=_int(_required(data, "defense"), "stats.defense"),
        speed=_int(_required(data, "speed"), "stats.speed"),
        recovery=_int(data.get("recovery", 0), "stats.recovery"),
        mp_recovery=_int(data.get("mp_recovery", 0), "stats.mp_recovery"),
    )


def _parse_stat_modifier(data: Mapping[str, Any]) -> CombatStatModifier:
    return CombatStatModifier(
        hp=_number(data.get("hp", 0), "stat_modifier.hp"),
        mp=_number(data.get("mp", 0), "stat_modifier.mp"),
        attack=_number(data.get("attack", 0), "stat_modifier.attack"),
        defense=_number(data.get("defense", 0), "stat_modifier.defense"),
        speed=_number(data.get("speed", 0), "stat_modifier.speed"),
        recovery=_number(data.get("recovery", 0), "stat_modifier.recovery"),
        mp_recovery=_number(data.get("mp_recovery", 0), "stat_modifier.mp_recovery"),
    )


def _parse_optional_stat_modifier(value: Any, path: str) -> CombatStatModifier | None:
    if value is None:
        return None
    return _parse_stat_modifier(_mapping(value, path))


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
                free=_bool(data.get("free", False), f"{path}[{index}].free"),
            )
        )
    return tuple(skills)


def _parse_level_skill_unlocks(value: Any, path: str, registry: dict[str, Skill]) -> tuple[LevelSkillUnlock, ...]:
    unlocks = []
    for index, item in enumerate(_list(value, path)):
        data = _mapping(item, f"{path}[{index}]")
        unlocks.append(
            LevelSkillUnlock(
                level=_int(_required(data, "level"), f"{path}[{index}].level"),
                skills=_resolve_skills(_required(data, "skills"), registry, f"{path}[{index}].skills"),
            )
        )
    return tuple(unlocks)


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
        effect_type = _str(data.get("type", data.get("effect_type")), f"effects[{index}].type")
        effects.append(
            SkillEffect(
                effect_type=effect_type,
                value=(
                    0
                    if effect_type == "apply_status" and data.get("value") is None
                    else _number(_required(data, "value"), f"effects[{index}].value")
                ),
                stat=None if data.get("stat") is None else _str(data.get("stat"), f"effects[{index}].stat"),
                target=_str(data.get("target", "target"), f"effects[{index}].target"),
                status=(
                    _parse_status(_mapping(_required(data, "status"), f"effects[{index}].status"))
                    if effect_type == "apply_status"
                    else None
                ),
            )
        )
    return tuple(effects)


def _parse_status(data: Mapping[str, Any]) -> StatusDefinition:
    status_id = data.get("id", data.get("status_id"))
    return StatusDefinition(
        status_id=_str(status_id, "status.id"),
        name=_str(_required(data, "name"), "status.name"),
        duration=_int(_required(data, "duration"), "status.duration"),
        effects=_parse_effects(_list(_required(data, "effects"), "status.effects")),
        polarity=_str(data.get("polarity", "neutral"), "status.polarity"),
        stack_mode=_str(data.get("stack_mode", "refresh"), "status.stack_mode"),
    )


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


def _opt_str(value: Any, path: str) -> str:
    """可选字符串字段，允许空字符串或 None。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise YamlLoadError(f"{path} must be a string")
    return value


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
