from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats


@pytest.fixture(scope="module")
def caster_skills():
    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    return {
        skill.skill_id: skill
        for template in definition.content.recruitable_adventurers
        if template.template_id in {"cleric", "plague_mage"}
        for skill in (*template.skills, *(s for unlock in template.level_skill_unlocks for s in unlock.skills))
    }


@pytest.mark.parametrize("maximum_mp,current_mp,damage,remaining_mp", [
    (100, 39, 100, 39),
    (100, 40, 236, 30),
    (100, 100, 236, 90),
    (20, 9, 100, 9),
    (20, 10, 236, 0),
])
def test_holy_strike_checks_mana_ratio_and_payment_before_cast(
    caster_skills, maximum_mp, current_mp, damage, remaining_mp,
):
    hero = Combatant(
        "cleric", CombatStats(1000, maximum_mp, 100, 0, 10, 0),
        CombatResources(1000, current_mp), skills=(caster_skills["holy_strike"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 1, 0))

    result = run_auto_battle(hero, target, max_actions=1)

    assert result.events[0].damage == damage
    assert result.left_resources.current_mp == remaining_mp


def test_holy_infusion_affects_two_following_actions_but_not_its_own_hit(caster_skills):
    hero = Combatant(
        "cleric", CombatStats(1000, 10, 100, 0, 10, 0),
        skills=(caster_skills["holy_strike"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 1, 0))

    result = run_auto_battle(hero, target, max_actions=4)

    assert [e.skill_id for e in result.events] == ["holy_strike", None, None, None]
    assert [e.damage for e in result.events] == [236, 135, 135, 100]
    assert result.left_resources.current_mp == 0


def test_cleric_default_heal_and_explicit_priority_have_different_resource_tradeoffs(caster_skills):
    stats = CombatStats(1000, 100, 100, 0, 10, 0)
    resources = CombatResources(600, 100)
    default = (caster_skills["minor_heal"], caster_skills["soothing_light"])
    tuned = (default[0], replace(default[1], priority=30))
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 1, 0))

    safe = run_auto_battle(Combatant("cleric", stats, resources, default), target, max_actions=1)
    efficient = run_auto_battle(Combatant("cleric", stats, resources, tuned), target, max_actions=1)

    assert safe.events[0].skill_id == "minor_heal"
    assert (safe.events[0].healing, safe.events[0].damage) == (35, 0)
    assert safe.left_resources == CombatResources(635, 91)
    assert efficient.events[0].skill_id == "soothing_light"
    assert (efficient.events[0].healing, efficient.events[0].damage) == (220, 100)
    assert efficient.left_resources == CombatResources(820, 98)


@pytest.mark.parametrize("current_mp,damage", [(0, 90), (44, 90), (45, 74)])
def test_shadow_form_loses_defense_below_mana_reserve(caster_skills, current_mp, damage):
    hero = Combatant(
        "plague", CombatStats(1000, 100, 1, 10, 1, 0),
        CombatResources(1000, current_mp), skills=(caster_skills["shadow_form"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 100, 0, 10, 0))

    result = run_auto_battle(hero, target, max_actions=1)

    assert result.events[0].actor_side == "right"
    assert result.events[0].damage == damage
    assert result.left_resources.current_hp == 1000 - damage


def test_shadow_form_expires_after_tenth_combined_action(caster_skills):
    hero = Combatant(
        "plague", CombatStats(10000, 100, 1, 10, 0, 0),
        skills=(caster_skills["shadow_form"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 100, 0, 10, 0))

    result = run_auto_battle(hero, target, max_actions=11)

    assert [e.damage for e in result.events] == [74] * 10 + [90]


@pytest.mark.parametrize("mp,expected", [(39, []), (40, [3])])
def test_epidemic_requires_its_action_window_and_mana_reserve(caster_skills, mp, expected):
    hero = Combatant(
        "plague", CombatStats(10000, 100, 1, 0, 10, 0),
        CombatResources(10000, mp), skills=(caster_skills["epidemic"],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=12)

    assert [e.action_index for e in result.events if e.skill_id == "epidemic"] == expected
    assert result.left_resources.current_mp == (mp - 14 if expected else mp)


def test_epidemic_can_miss_window_when_the_enemy_outspeeds_it(caster_skills):
    hero = Combatant(
        "plague", CombatStats(10000, 100, 1, 0, 1, 0),
        skills=(caster_skills["epidemic"],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 20, 0))

    result = run_auto_battle(hero, target, max_actions=25)

    actions = [e for e in result.events if e.actor_side == "left"]
    assert actions and actions[0].action_index > 9
    assert all(e.skill_id is None for e in actions)
    assert result.left_resources.current_mp == 100


def test_plague_default_control_opener_then_explicit_poison_sequence(caster_skills):
    ids = ["withering_touch", "necrotic_blast", "epidemic", "plague_curse"]
    default = tuple(caster_skills[s] for s in ids)
    tuned = tuple(
        replace(caster_skills[s], priority=40 - i)
        for i, s in enumerate(["withering_touch", "epidemic", "plague_curse"])
    )
    hero_stats = CombatStats(1000, 100, 10, 0, 10, 0)
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 1, 0))

    safe = run_auto_battle(Combatant("plague", hero_stats, skills=default), target, max_actions=4)
    planned = run_auto_battle(Combatant("plague", hero_stats, skills=tuned), target, max_actions=4)

    assert [e.skill_id for e in safe.events] == ["withering_touch"] + ["necrotic_blast"] * 3
    assert [e.skill_id for e in planned.events] == [
        "withering_touch", "plague_curse", "epidemic", "plague_curse",
    ]
    assert safe.left_resources.current_mp == 53
    assert planned.left_resources.current_mp == 67
