from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats


@pytest.fixture(scope="module")
def skills():
    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    values = [s for eq in definition.content.equipment_templates for s in eq.skills]
    values.extend(
        s for t in definition.content.recruitable_adventurers
        for s in (*t.skills, *(s for u in t.level_skill_unlocks for s in u.skills))
    )
    return {s.skill_id: s for s in values}


@pytest.mark.parametrize("maximum_mp,current_mp,first_cast,remaining_mp", [
    (100, 41, None, 41),
    (100, 42, 5, 25),
    (100, 100, 5, 83),
    (30, 16, None, 16),
    (30, 17, 5, 0),
])
def test_starfall_checks_warmup_reserve_and_payment(
    skills, maximum_mp, current_mp, first_cast, remaining_mp,
):
    hero = Combatant(
        "mage", CombatStats(1000, maximum_mp, 80, 0, 10, 0),
        CombatResources(1000, current_mp), skills=(skills["ultimate_spellshot"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=5)

    casts = [e for e in result.events if e.skill_id == "ultimate_spellshot"]
    assert [e.action_index for e in casts] == ([] if first_cast is None else [first_cast])
    if casts:
        assert casts[0].damage == 249
    assert result.left_resources.current_mp == remaining_mp


def test_starfall_repeats_until_below_reserve(skills):
    hero = Combatant(
        "mage", CombatStats(1000, 100, 80, 0, 10, 0), skills=(skills["ultimate_spellshot"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=10)

    assert [e.skill_id for e in result.events] == [None] * 4 + ["ultimate_spellshot"] * 4 + [None] * 2
    assert result.left_resources.current_mp == 32


@pytest.mark.parametrize("target_hp,mp,casts", [
    (8501, 100, False), (8500, 24, False), (8500, 25, True), (8500, 10, False),
])
def test_annihilation_requires_wounded_target_and_mana(skills, target_hp, mp, casts):
    hero = Combatant(
        "cannon", CombatStats(1000, 100, 100, 0, 10, 0),
        CombatResources(1000, mp), skills=(skills["ultimate_cannoneer"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 0, 0), CombatResources(target_hp, 0))

    result = run_auto_battle(hero, target, max_actions=1)

    assert result.events[0].skill_id == ("ultimate_cannoneer" if casts else None)
    assert result.events[0].damage == (230 if casts else 100)
    assert result.left_resources.current_mp == (mp - 11 if casts else mp)


@pytest.mark.parametrize("hp,mp,casts,healing,remaining_mp", [
    (701, 56, False, 0, 56),
    (700, 100, True, 300, 100),
    (1000, 55, True, 0, 95),
    (100, 0, True, 350, 40),
])
def test_dawn_accepts_either_wounds_or_low_mana_after_warmup(
    skills, hp, mp, casts, healing, remaining_mp,
):
    hero = Combatant(
        "cleric", CombatStats(1000, 100, 100, 0, 10, 0),
        CombatResources(hp, mp), skills=(skills["ultimate_cleric"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=5)

    assert all(e.skill_id is None for e in result.events[:4])
    assert result.events[-1].skill_id == ("ultimate_cleric" if casts else None)
    assert result.events[-1].healing == healing
    assert result.events[-1].damage == (145 if casts else 100)
    assert result.left_resources.current_mp == remaining_mp


def test_dawn_minimum_mana_and_six_following_actions(skills):
    hero = Combatant(
        "cleric", CombatStats(1000, 20, 100, 0, 10, 0),
        CombatResources(100, 0), skills=(skills["ultimate_cleric"],),
    )
    target = Combatant("target", CombatStats(10000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=12)

    assert sum(e.skill_id == "ultimate_cleric" for e in result.events) == 1
    assert [e.damage for e in result.events] == [100] * 4 + [145] * 7 + [100]
    assert result.left_resources == CombatResources(450, 18)


@pytest.mark.parametrize("mp,target_hp,casts", [(35, 88004, True), (34, 88004, False), (100, 88005, False)])
def test_plague_tide_requires_all_three_conditions(skills, mp, target_hp, casts):
    hero = Combatant(
        "plague", CombatStats(1000, 100, 1, 0, 10, 0),
        CombatResources(1000, mp), skills=(skills["ultimate_plague"],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 0, 0), CombatResources(target_hp, 0))

    result = run_auto_battle(hero, target, max_actions=5)

    assert all(e.skill_id is None for e in result.events[:4])
    assert result.events[-1].skill_id == ("ultimate_plague" if casts else None)
    assert result.left_resources.current_mp == (mp - 16 if casts else mp)


@pytest.mark.parametrize("seal,hp,mp,first_action,damage,healing,remaining_mp", [
    ("ultimate_knight", 500, 0, 3, 415, 220, 0),
    ("ultimate_bloodfiend", 500, 40, 4, 375, 150, 32),
    ("ultimate_jester", 1000, 45, 6, 290, 0, 35),
])
def test_bastion_blood_moon_and_blades_payloads(
    skills, seal, hp, mp, first_action, damage, healing, remaining_mp,
):
    hero = Combatant(
        "hero", CombatStats(1000, 100, 100, 0, 10, 0),
        CombatResources(hp, mp), skills=(skills[seal],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=first_action)

    assert all(e.skill_id is None for e in result.events[:-1])
    assert result.events[-1].skill_id == seal
    assert result.events[-1].damage == damage
    assert result.events[-1].healing == healing
    assert result.left_resources.current_mp == remaining_mp


@pytest.mark.parametrize("seal,hp,mp", [
    ("ultimate_knight", 851, 100),
    ("ultimate_bloodfiend", 651, 100),
    ("ultimate_bloodfiend", 500, 39),
    ("ultimate_bloodfiend", 500, 7),
    ("ultimate_jester", 1000, 44),
    ("ultimate_jester", 1000, 9),
])
def test_specialist_seals_skip_unmet_thresholds(skills, seal, hp, mp):
    hero = Combatant(
        "hero", CombatStats(1000, 100, 100, 0, 10, 0),
        CombatResources(hp, mp), skills=(skills[seal],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 0, 0))

    result = run_auto_battle(hero, target, max_actions=7)

    assert all(e.skill_id is None for e in result.events)
    assert result.left_resources.current_mp == mp


@pytest.mark.parametrize("skill_id,status_id,ticks,damage", [
    ("epidemic", "epidemic_poison", 6, 25),
    ("ultimate_plague", "ultimate_plague_status", 8, 38),
    ("ultimate_jester", "thousand_blades_break", 8, 35),
])
def test_poison_and_bleed_tick_on_target_actions_for_the_configured_duration(
    skills, skill_id, status_id, ticks, damage,
):
    hero = Combatant(
        "hero", CombatStats(10000, 100, 10, 0, 1, 0), skills=(skills[skill_id],),
    )
    target = Combatant(
        "target", CombatStats(100000, 0, 1, 0, 1, 0), CombatResources(80000, 0),
    )

    result = run_auto_battle(hero, target, max_actions=80)

    assert sum(e.skill_id == skill_id for e in result.events) == 1
    status_events = [e for e in result.events if e.status_id == status_id]
    assert [e.damage for e in status_events] == [damage] * ticks
    assert all(e.actor_side == "right" for e in status_events)


@pytest.mark.parametrize("seal,status_id,ticks", [
    ("ultimate_knight", "ultimate_bastion", 12),
    ("ultimate_bloodfiend", "ultimate_blood_moon", 8),
])
def test_regeneration_ticks_on_own_actions_and_expires(skills, seal, status_id, ticks):
    hero = Combatant(
        "hero", CombatStats(10000, 100, 100, 0, 1, 0),
        CombatResources(1000, 100), skills=(skills[seal],),
    )
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 1, 0))

    result = run_auto_battle(hero, target, max_actions=80)

    assert sum(e.skill_id == seal for e in result.events) == 1
    status_events = [e for e in result.events if e.status_id == status_id]
    assert len(status_events) == ticks
    assert all(e.actor_side == "left" for e in status_events)
    assert status_events[0].healing == (600 if seal == "ultimate_knight" else 700)


@pytest.mark.parametrize("seal,blocker,hp", [
    ("ultimate_knight", "shield_bash", 500),
    ("ultimate_bloodfiend", "blood_drain", 500),
    ("ultimate_jester", "combo_strike", 1000),
])
def test_explicit_priority_can_release_seals_behind_repeatable_skills(skills, seal, blocker, hp):
    stats = CombatStats(10000, 1000, 100, 0, 10, 0)
    resources = CombatResources(hp, 1000)
    default = (skills[seal], skills[blocker])
    configured = (replace(skills[seal], priority=40), skills[blocker])
    target = Combatant("target", CombatStats(100000, 0, 1, 0, 0, 0))

    before = run_auto_battle(Combatant("hero", stats, resources, default), target, max_actions=12)
    after = run_auto_battle(Combatant("hero", stats, resources, configured), target, max_actions=12)

    assert not any(e.skill_id == seal for e in before.events)
    assert sum(e.skill_id == seal for e in after.events) == 1
