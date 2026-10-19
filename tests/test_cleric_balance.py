import pytest

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats


@pytest.fixture(scope="module")
def divine_guard():
    definition = load_game_definition("data/presets/advanced")
    cleric = next(a for a in definition.content.recruitable_adventurers if a.template_id == "cleric")
    unlock = next(u for u in cleric.level_skill_unlocks if any(s.skill_id == "divine_guard" for s in u.skills))
    assert unlock.level == 9
    return next(s for s in unlock.skills if s.skill_id == "divine_guard")


def test_divine_guard_reduces_defense_and_preserves_trigger_and_offense(divine_guard):
    assert divine_guard.kind == "passive"
    assert divine_guard.condition.condition_type == "self_hp_pct_lte"
    assert divine_guard.condition.value == 0.8
    assert [(e.effect_type, e.stat, e.value, e.target) for e in divine_guard.effects] == [
        ("stat_multiplier", "defense", 1.5, "self"),
        ("stat_multiplier", "attack", 1.5, "self"),
        ("stat_multiplier", "speed", 1.2, "self"),
    ]


@pytest.mark.parametrize("hp,expected_damage", [(80, 30), (81, 20)])
def test_divine_guard_attack_bonus_at_hp_boundary(divine_guard, hp, expected_damage):
    cleric = Combatant(
        "cleric",
        CombatStats(hp=100, mp=0, attack=20, defense=10, speed=10, recovery=0),
        CombatResources(hp, 0),
        (divine_guard,),
    )
    foe = Combatant("foe", CombatStats(hp=1000, mp=0, attack=40, defense=0, speed=1, recovery=0))

    result = run_auto_battle(cleric, foe, max_actions=1)

    assert result.events[0].actor_id == "cleric"
    assert result.events[0].damage == expected_damage


@pytest.mark.parametrize("hp,expected_hp", [(80, 55), (81, 51)])
def test_divine_guard_defense_multiplier_at_hp_boundary(divine_guard, hp, expected_hp):
    cleric = Combatant(
        "cleric",
        CombatStats(hp=100, mp=0, attack=20, defense=10, speed=10, recovery=0),
        CombatResources(hp, 0),
        (divine_guard,),
    )
    foe = Combatant("foe", CombatStats(hp=1000, mp=0, attack=40, defense=0, speed=100, recovery=0))

    result = run_auto_battle(cleric, foe, max_actions=1)

    assert result.events[0].actor_id == "foe"
    assert result.left_resources.current_hp == expected_hp
