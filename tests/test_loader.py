from contextlib import contextmanager
from pathlib import Path
import shutil
from textwrap import dedent

import pytest

from guild_manager_bench.game.actions import (
    CraftAction,
    EquipAction,
    HuntAction,
    PurchaseUpgradeAction,
    TurnAction,
)
from guild_manager_bench.game.engine import apply_turn, new_game
from guild_manager_bench.game.loader import YamlLoadError, load_game_definition
from guild_manager_bench.game.presets import (
    describe_data_source,
    list_data_presets,
    resolve_data_preset,
    resolve_data_source,
)


def test_load_game_definition_from_yaml_directory() -> None:
    with _data_dir("full_load") as data_dir:
        _write_game_yaml_files(data_dir)

        definition = load_game_definition(data_dir)

    assert definition.rules.max_turns == 2
    assert definition.rules.monster_spawn.count_curve.value_at(2) == 2
    assert definition.rules.recruitment.candidate_count == 2
    assert definition.rules.recruitment.first_turn_candidate_count == 4
    assert definition.rules.recruitment.initial_party_size_limit == 1
    assert definition.rules.recruitment.maximum_party_size_limit == 3
    assert definition.starting_gold == 20
    assert dict(definition.starting_materials) == {"iron_ore": 1}
    assert definition.content.adventurers[0].adventurer_id == "a1"
    assert definition.content.adventurers[0].stat_growth_per_level is not None
    assert definition.content.adventurers[0].stat_growth_per_level.attack == 12
    assert definition.content.adventurers[0].level_skill_unlocks[0].level == 2
    assert definition.content.adventurers[0].level_skill_unlocks[0].skills[0].skill_id == "guard_break"
    assert definition.content.recruitable_adventurers[0].template_id == "guard"
    assert definition.content.recruitable_adventurers[0].recruit_gold == 30
    assert definition.content.recruitable_adventurers[0].stat_growth_per_level.defense == 2
    assert definition.content.monster_archetypes[0].archetype_id == "slime"
    assert definition.content.monster_archetypes[0].spawn_weight == 7
    assert definition.content.monster_archetypes[0].min_turn == 1
    assert definition.content.equipment_templates[0].equipment_id == "iron_sword"
    assert definition.content.crafting_recipes[0].recipe_id == "iron_sword_recipe"
    assert definition.content.global_upgrades[0].upgrade_id == "weapon_training"
    assert definition.content.global_upgrades[0].party_size_bonus == 1
    assert definition.llm_tools.expose_battle_preview is False
    assert definition.scoring.mode == "endgame_arena"
    assert definition.scoring.waves == 256


def test_load_game_definition_reads_llm_tool_switches() -> None:
    with _data_dir("llm_tool_switches") as data_dir:
        _write_game_yaml_files(data_dir)
        game_yaml = (data_dir / "game.yaml").read_text(encoding="utf-8")
        (data_dir / "game.yaml").write_text(
            game_yaml + "\nllm:\n  expose_battle_preview: true\n",
            encoding="utf-8",
        )

        definition = load_game_definition(data_dir)

    assert definition.llm_tools.expose_battle_preview is True


def test_load_game_definition_reads_scoring_rules() -> None:
    with _data_dir("scoring_rules") as data_dir:
        _write_game_yaml_files(data_dir)
        game_yaml = (data_dir / "game.yaml").read_text(encoding="utf-8")
        (data_dir / "game.yaml").write_text(
            game_yaml
            + dedent(
                """
                scoring:
                  mode: endgame_arena
                  seed: 123
                  waves: 7
                  wave_size: 3
                  difficulty_factors: [0, 2, 4]
                  resource_mode: current
                  aggregation: best_assignment
                """
            ),
            encoding="utf-8",
        )

        definition = load_game_definition(data_dir)

    assert definition.scoring.seed == 123
    assert definition.scoring.waves == 7
    assert definition.scoring.wave_size == 3
    assert definition.scoring.difficulty_factors == (0, 2, 4)
    assert definition.scoring.resource_mode == "current"


def test_load_game_definition_reads_apply_status_effects() -> None:
    with _data_dir("status_effects") as data_dir:
        _write_game_yaml_files(data_dir)
        (data_dir / "adventurers.yaml").write_text(
            dedent(
                """
                adventurers:
                  - adventurer_id: a1
                    name: 先锋
                    stats:
                      hp: 100
                      mp: 10
                      attack: 10
                      defense: 1
                      speed: 10
                      recovery: 0
                    skills:
                      - id: poison_dart
                        name: 毒镖
                        kind: active
                        condition:
                          type: always
                        effects:
                          - type: apply_status
                            target: target
                            status:
                              id: poison
                              name: 中毒
                              duration: 2
                              polarity: negative
                              stack_mode: refresh
                              effects:
                                - type: true_damage
                                  value: 3
                        mp_cost: 1
                        priority: 100
                """
            ),
            encoding="utf-8",
        )

        definition = load_game_definition(data_dir)

    effect = definition.content.adventurers[0].skills[0].effects[0]
    assert effect.effect_type == "apply_status"
    assert effect.status is not None
    assert effect.status.status_id == "poison"
    assert effect.status.polarity == "negative"
    assert effect.status.effects[0].effect_type == "true_damage"


def test_loaded_definition_can_drive_turn_flow() -> None:
    with _data_dir("turn_flow") as data_dir:
        _write_game_yaml_files(data_dir)
        definition = load_game_definition(data_dir)
        state = new_game(definition)

        result = apply_turn(
            definition,
            state,
            TurnAction(
                operations=(
                    CraftAction(recipe_id="iron_sword_recipe"),
                    PurchaseUpgradeAction(upgrade_id="weapon_training"),
                    EquipAction(adventurer_id="a1", equipment_instance_id="eq_0001"),
                ),
                hunts=(HuntAction(adventurer_id="a1", monster_id=state.current_monsters[0].monster_id),),
            ),
        )

    assert result.battles[0].won
    assert result.state.turn == 2
    assert result.state.gold == 10
    assert dict(result.state.materials) == {"iron_ore": 0, "slime_gel": 1}
    assert result.state.experience_pool == 60


def test_load_game_definition_rejects_missing_rules() -> None:
    with _data_dir("missing_rules") as data_dir:
        (data_dir / "game.yaml").write_text("starting:\n  gold: 0\n", encoding="utf-8")

        with pytest.raises(YamlLoadError):
            load_game_definition(data_dir)


def test_load_game_definition_rejects_wrong_scalar_type() -> None:
    with _data_dir("wrong_scalar") as data_dir:
        _write_game_yaml_files(data_dir)
        (data_dir / "game.yaml").write_text(
            dedent(
                """
                rules:
                  max_turns: two
                  seed: 1
                  monster_spawn:
                    count_curve:
                      base: 1
                """
            ),
            encoding="utf-8",
        )

        with pytest.raises(YamlLoadError):
            load_game_definition(data_dir)


def test_resolve_data_preset_uses_named_preset_directory() -> None:
    with _data_dir("preset_named") as data_dir:
        preset_dir = data_dir / "presets" / "tiny"
        preset_dir.mkdir(parents=True)
        _write_game_yaml_files(preset_dir)

        preset = resolve_data_preset(data_dir, "tiny")

    assert preset.name == "tiny"
    assert preset.source == "preset"
    assert preset.data_dir.name == "tiny"
    assert len(preset.data_hash) == 64


def test_resolve_data_preset_uses_default_preset_directory() -> None:
    with _data_dir("preset_default") as data_dir:
        preset_dir = data_dir / "presets" / "default"
        preset_dir.mkdir(parents=True)
        _write_game_yaml_files(preset_dir)

        preset = resolve_data_preset(data_dir, "default")

    assert preset.name == "default"
    assert preset.source == "preset"
    assert preset.data_dir.name == "default"


def test_resolve_data_preset_defaults_to_default_preset() -> None:
    with _data_dir("preset_implicit_default") as data_dir:
        preset_dir = data_dir / "presets" / "default"
        preset_dir.mkdir(parents=True)
        _write_game_yaml_files(preset_dir)

        preset = resolve_data_preset(data_dir)

    assert preset.name == "default"
    assert preset.source == "preset"
    assert preset.data_dir.name == "default"


def test_resolve_data_source_accepts_root_or_direct_preset_path() -> None:
    with _data_dir("data_source_preset") as data_dir:
        default_dir = data_dir / "presets" / "default"
        full_dir = data_dir / "presets" / "full"
        default_dir.mkdir(parents=True)
        full_dir.mkdir(parents=True)
        _write_game_yaml_files(default_dir)
        _write_game_yaml_files(full_dir)

        implicit = resolve_data_source(data_dir)
        explicit = resolve_data_source(data_dir, "full")
        direct = resolve_data_source(full_dir)
        described = describe_data_source(full_dir)

    assert implicit.name == "default"
    assert explicit.name == "full"
    assert direct.name == "full"
    assert direct.source == "preset"
    assert described["preset"] == "full"
    assert described["source"] == "preset"


def test_list_data_presets_returns_complete_presets_only() -> None:
    with _data_dir("preset_list") as data_dir:
        good = data_dir / "presets" / "good"
        bad = data_dir / "presets" / "bad"
        good.mkdir(parents=True)
        bad.mkdir(parents=True)
        _write_game_yaml_files(good)
        (bad / "game.yaml").write_text("rules: {}\n", encoding="utf-8")

        presets = list_data_presets(data_dir)

    assert [preset.name for preset in presets] == ["good"]


def test_load_game_definition_parses_bonus_skill_themes() -> None:
    with _data_dir("skill_themes") as data_dir:
        _write_game_yaml_files(data_dir)
        (data_dir / "skills.yaml").write_text(
            dedent(
                """
                skills:
                  - id: venom_spit
                    name: 剧毒喷吐
                    kind: active
                    condition:
                      type: always
                    effects:
                      - type: true_damage
                        value: 3
                  - id: iron_shell
                    name: 坚甲
                    kind: passive
                    condition:
                      type: always
                    effects:
                      - type: stat_bonus
                        stat: defense
                        value: 5
                        target: self
                  - id: blood_fang
                    name: 嗜血
                    kind: active
                    condition:
                      type: always
                    effects:
                      - type: damage_multiplier
                        value: 1.5
                """
            ),
            encoding="utf-8",
        )
        (data_dir / "monster_tiers.yaml").write_text(
            dedent(
                """
                bonus_skill_themes:
                  - id: venom
                    name: 毒素
                    skills: [venom_spit, iron_shell]
                  - id: predator
                    name: 掠食
                    skills: [blood_fang]
                tiers:
                  elite:
                    chance: 0.2
                    stat_multiplier: 1.3
                    bonus_skill_count: 1
                  boss:
                    chance: 0.1
                    stat_multiplier: 2.0
                    bonus_skill_count: 2
                """
            ),
            encoding="utf-8",
        )

        definition = load_game_definition(data_dir)

    themes = definition.rules.monster_spawn.bonus_skill_themes
    assert len(themes) == 2
    assert themes[0].theme_id == "venom"
    assert themes[0].name == "毒素"
    assert [s.skill_id for s in themes[0].skills] == ["venom_spit", "iron_shell"]
    assert themes[1].theme_id == "predator"
    assert [s.skill_id for s in themes[1].skills] == ["blood_fang"]


@contextmanager
def _data_dir(name: str):
    root = Path(__file__).parent / "_tmp_loader" / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_game_yaml_files(path) -> None:
    (path / "game.yaml").write_text(
        dedent(
            """
            rules:
              max_turns: 2
              seed: 1
              monster_spawn:
                count_curve:
                  base: 1
                  per_turn: 1
                stat_growth_curve:
                  base: 0
                  per_turn: 1
                reward_growth_curve:
                  base: 0
                  per_turn: 1
              recruitment:
                candidate_count: 2
                first_turn_candidate_count: 4
                initial_party_size_limit: 1
                maximum_party_size_limit: 3
            starting:
              gold: 20
              materials:
                iron_ore: 1
            experience:
              base_required_experience: 50
              required_experience_growth: 0
              stat_growth_per_level:
                hp: 10
                attack: 10
            """
        ),
        encoding="utf-8",
    )
    (path / "adventurers.yaml").write_text(
        dedent(
            """
            adventurers:
              - adventurer_id: a1
                name: 先锋
                stats:
                  hp: 100
                  mp: 10
                  attack: 10
                  defense: 1
                  speed: 10
                  recovery: 0
                stat_growth_per_level:
                  hp: 8
                  attack: 12
                skills:
                  - id: power_strike
                    name: 强力打击
                    kind: active
                    condition:
                      type: always
                    effects:
                      - type: damage_multiplier
                        value: 2.0
                    mp_cost: 1
                    priority: 100
                level_skill_unlocks:
                  - level: 2
                    skills:
                      - id: guard_break
                        name: 破防训练
                        kind: passive
                        condition:
                          type: always
                        effects:
                          - type: stat_bonus
                            stat: attack
                            value: 3
                            target: self
            recruitable_adventurers:
              - id: guard
                name: 卫士
                recruit_gold: 30
                stats:
                  hp: 80
                  mp: 8
                  attack: 8
                  defense: 5
                  speed: 6
                  recovery: 2
                stat_growth_per_level:
                  hp: 8
                  attack: 2
                  defense: 2
                skills:
                  - id: guard_stance
                    name: 守卫姿态
                    kind: passive
                    condition:
                      type: always
                    effects:
                      - type: stat_bonus
                        stat: defense
                        value: 2
                        target: self
            """
        ),
        encoding="utf-8",
    )
    (path / "monsters.yaml").write_text(
        dedent(
            """
            - id: slime
              name: 史莱姆
              base_stats:
                hp: 20
                mp: 0
                attack: 1
                defense: 0
                speed: 1
                recovery: 0
              base_reward:
                gold: 5
                experience: 60
                materials:
                  slime_gel: 1
              spawn_weight: 7
              min_turn: 1
              stat_growth:
                hp: 10
                attack: 1
              reward_growth:
                gold: 1
                experience: 10
                materials:
                  slime_gel: 1
            """
        ),
        encoding="utf-8",
    )
    (path / "equipment.yaml").write_text(
        dedent(
            """
            equipment:
              - equipment_id: iron_sword
                name: 铁剑
                slot: main_hand
                stats:
                  attack: 20
                skills:
                  - id: blade_focus
                    name: 剑术专注
                    kind: passive
                    condition:
                      type: always
                    effects:
                      - type: stat_bonus
                        stat: attack
                        value: 2
                        target: self
            """
        ),
        encoding="utf-8",
    )
    (path / "crafting_recipes.yaml").write_text(
        dedent(
            """
            recipes:
              - recipe_id: iron_sword_recipe
                name: 铁剑配方
                output: iron_sword
                gold: 5
                materials:
                  iron_ore: 1
            """
        ),
        encoding="utf-8",
    )
    (path / "global_upgrades.yaml").write_text(
        dedent(
            """
            upgrades:
              - upgrade_id: weapon_training
                name: 武器训练
                gold: 10
                party_size_bonus: 1
                stats:
                  attack: 5
            """
        ),
        encoding="utf-8",
    )
