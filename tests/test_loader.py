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


def test_load_game_definition_from_yaml_directory() -> None:
    with _data_dir("full_load") as data_dir:
        _write_game_yaml_files(data_dir)

        definition = load_game_definition(data_dir)

    assert definition.rules.max_turns == 2
    assert definition.rules.monster_spawn.count_curve.value_at(2) == 2
    assert definition.starting_gold == 20
    assert dict(definition.starting_materials) == {"iron_ore": 1}
    assert definition.content.adventurers[0].adventurer_id == "a1"
    assert definition.content.monster_archetypes[0].archetype_id == "slime"
    assert definition.content.equipment_templates[0].equipment_id == "iron_sword"
    assert definition.content.crafting_recipes[0].recipe_id == "iron_sword_recipe"
    assert definition.content.global_upgrades[0].upgrade_id == "weapon_training"


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
                stats:
                  attack: 5
            """
        ),
        encoding="utf-8",
    )
