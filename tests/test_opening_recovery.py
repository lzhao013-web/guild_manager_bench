from dataclasses import asdict, replace
from pathlib import Path
import shutil

import pytest
import yaml

from guild_manager_bench.bench.llm.prompts import build_system_prompt
from guild_manager_bench.game.actions import (
    CraftAction, EndTurnAction, EquipAction, HuntAction, PurchaseUpgradeAction, RecruitAction,
)
from guild_manager_bench.game.engine import (
    apply_preparation_action, end_turn, new_game, preview_battle,
)
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.state import (
    AdventurerState, FloatCurve, GameContent, GameDefinition, GameRules,
    MonsterArchetype, MonsterSpawnRules, RewardBundle, TurnRecoveryRules,
)
from guild_manager_bench.game.upgrades import UpgradeError
from guild_manager_bench.runtime.observation import build_observation


PRESETS = Path(__file__).resolve().parents[1] / "data/presets"


def recovery_game(*, hp=0, opening_turns=3):
    adventurer = AdventurerState(
        adventurer_id="a1", name="恢复测试角色",
        base_stats=CombatStats(hp=101, mp=23, attack=10, defense=5, speed=10,
                               recovery=7, mp_recovery=3),
        resources=CombatResources(current_hp=hp, current_mp=2),
    )
    return GameDefinition(
        content=GameContent(adventurers=(adventurer,), monster_archetypes=(
            MonsterArchetype(archetype_id="m1", name="测试怪物", base_stats=adventurer.base_stats,
                             base_reward=RewardBundle()),
        )),
        rules=GameRules(
            max_turns=5, seed=42,
            monster_spawn=MonsterSpawnRules(count_curve=FloatCurve(base=0)),
            turn_recovery=TurnRecoveryRules(
                hp=5, mp=4, hp_percent=0.08, mp_percent=0.15,
                opening_turns=opening_turns, opening_hp_floor_percent=0.5,
            ),
        ),
    )


@pytest.mark.parametrize("turn,expected_hp", [(1, 50), (3, 50), (4, 20)])
def test_opening_hp_floor_uses_ending_turn_and_leaves_mp_recovery_unchanged(turn, expected_hp):
    definition = recovery_game()
    state = replace(new_game(definition), turn=turn)

    result = end_turn(definition, state, EndTurnAction())

    assert result.state.turn == turn + 1
    assert result.state.adventurers[0].resources == CombatResources(
        current_hp=expected_hp, current_mp=12,
    )


@pytest.mark.parametrize("hp,expected_hp", [(29, 50), (30, 50), (49, 69), (95, 101)])
def test_opening_recovery_is_a_floor_not_an_additive_heal(hp, expected_hp):
    definition = recovery_game(hp=hp)

    result = end_turn(definition, new_game(definition), EndTurnAction())

    assert result.state.adventurers[0].resources.current_hp == expected_hp


def test_disabled_opening_recovery_keeps_original_formula():
    definition = recovery_game(opening_turns=0)

    result = end_turn(definition, new_game(definition), EndTurnAction())

    assert result.state.adventurers[0].resources == CombatResources(current_hp=20, current_mp=12)


def recruit(definition, indexes):
    state = new_game(definition)
    for index in indexes:
        state = apply_preparation_action(definition, state, RecruitAction(f"turn_1_recruit_{index}"))
    return state


def test_advanced_failed_opening_recovers_without_refunding_the_loss():
    definition = load_game_definition(PRESETS / "advanced")
    state = recruit(definition, [4, 9, 2])
    hunts = tuple(HuntAction(f"recruit_{i:04d}", f"turn_1_monster_{m}")
                  for i, m in enumerate([5, 3, 2], 1))
    preview = preview_battle(definition, state, adventurer_id="recruit_0001", monster_id="turn_1_monster_5")

    result = end_turn(definition, state, EndTurnAction(hunts))

    assert preview.combat_result == result.battles[0].combat_result
    assert [b.won for b in result.battles] == [False, True, True]
    assert result.state.experience_pool == 85
    assert result.state.gold == 75
    assert [(a.resources.current_hp, a.resources.current_mp) for a in result.state.adventurers] == [
        (42, 9), (85, 0), (64, 9),
    ]
    boosted = apply_preparation_action(definition, result.state, PurchaseUpgradeAction("training_grounds"))
    assert boosted.gold == 10
    assert "training_grounds" in boosted.unlocked_upgrade_ids


def test_advanced_good_opening_keeps_its_reward_and_health_advantage():
    definition = load_game_definition(PRESETS / "advanced")
    state = recruit(definition, [5, 6, 1])
    assert state.gold == 61
    with pytest.raises(UpgradeError, match="'gold': 4"):
        apply_preparation_action(definition, state, PurchaseUpgradeAction("training_grounds"))
    for action in (
        CraftAction("recipe_t1_heavy_sword"), CraftAction("recipe_t1_light_helmet"),
        EquipAction("recruit_0003", "eq_0001"), EquipAction("recruit_0002", "eq_0002"),
    ):
        state = apply_preparation_action(definition, state, action)
    hunts = tuple(HuntAction(f"recruit_{i:04d}", f"turn_1_monster_{m}")
                  for i, m in enumerate([3, 5, 1], 1))

    result = end_turn(definition, state, EndTurnAction(hunts))

    assert all(b.won for b in result.battles)
    assert result.state.experience_pool == 187
    assert result.state.gold == 102
    assert [a.resources.current_hp for a in result.state.adventurers] == [76, 47, 109]


def test_loaded_opening_rules_are_disclosed_to_the_model():
    definition = load_game_definition(PRESETS / "advanced")
    observation = build_observation(definition, new_game(definition))
    rules = observation["turn_recovery_rules"]

    assert observation["gold"] == 190
    assert rules["opening_turns"] == 3
    assert rules["opening_hp_floor_percent"] == 0.5
    prompt = build_system_prompt(turn_recovery_rules=rules)
    assert "仅在T1至T3回合末，正常恢复后" in prompt
    assert "最大HP×50%（向下取整）" in prompt
    assert "已超过该值的不额外回血，不额外回复MP" in prompt
    assert "T4起仅按普通回合末恢复公式结算" in prompt


@pytest.mark.parametrize("preset", ["default", "full"])
def test_other_presets_do_not_enable_opening_recovery(preset):
    definition = load_game_definition(PRESETS / preset)
    rules = definition.rules.turn_recovery

    assert rules.opening_turns == 0
    assert rules.opening_hp_floor_percent == 0
    assert "开局恢复保底" not in build_system_prompt(turn_recovery_rules=asdict(rules))


@pytest.mark.parametrize("field,value", [
    ("opening_turns", -1), ("opening_turns", 1.5),
    ("opening_hp_floor_percent", -0.1), ("opening_hp_floor_percent", 1.1),
])
def test_loader_rejects_invalid_opening_recovery_settings(tmp_path, field, value):
    data_dir = tmp_path / "data"
    shutil.copytree(PRESETS / "advanced", data_dir)
    game_path = data_dir / "game.yaml"
    game = yaml.safe_load(game_path.read_text(encoding="utf-8"))
    game["rules"]["turn_recovery"][field] = value
    game_path.write_text(yaml.safe_dump(game, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_game_definition(data_dir)
