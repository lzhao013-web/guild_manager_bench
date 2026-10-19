from dataclasses import replace
from itertools import permutations
from pathlib import Path
import random

import pytest

from guild_manager_bench.bench import metrics, protocols
from guild_manager_bench.bench.llm.forecast import query_forecast
from guild_manager_bench.bench.llm.formatting import skill_summary
from guild_manager_bench.game import engine
from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.runtime.observation import _skill_to_dict, build_observation
from guild_manager_bench.runtime.session import _restore_from_observation


ROOT = Path(__file__).resolve().parents[1]
NEW = {"mana_hound": 24, "crystal_guardian": 26, "blood_fanatic": 28}


@pytest.fixture(scope="module")
def definition():
    return load_game_definition(ROOT / "data/presets/advanced")


def archetype(definition, name):
    return next(a for a in definition.content.monster_archetypes if a.archetype_id == name)


def monster(definition, name, *, tier="normal", bonus=()):
    rules = definition.rules.monster_spawn
    return engine._spawn_monster(
        archetype(definition, name), 29, 1,
        rules.stat_growth_curve.value_at(29), rules.reward_growth_curve.value_at(29),
        tier, None if tier == "normal" else getattr(rules, tier), bonus,
    )


def combatant(m, *, hp=None):
    return Combatant(m.monster_id, m.stats,
                     None if hp is None else CombatResources(hp, m.stats.mp), m.skills)


@pytest.mark.parametrize("name,turn", NEW.items())
def test_new_archetypes_are_ordinary_low_weight_targets(definition, name, turn):
    a = archetype(definition, name)
    assert a.min_turn == turn
    assert a.spawn_weight == 8
    assert name not in {a.archetype_id for a in definition.content.challenge_archetypes}
    assert not ({"siege_core", "aegis_core", "renewal_core"} & a.base_reward.materials.keys())


def test_new_targets_do_not_change_pre_t24_spawns_or_refreshes(definition):
    old = replace(definition, content=replace(definition.content,
        monster_archetypes=tuple(a for a in definition.content.monster_archetypes if a.archetype_id not in NEW)))
    for turn in range(1, 24):
        expected = engine.spawn_monsters(old, turn)
        assert engine.spawn_monsters(definition, turn) == expected
        state = replace(engine.new_game(definition), turn=turn, current_monsters=expected)
        assert engine.preview_monster_refresh(definition, state) == engine.preview_monster_refresh(old, state)


@pytest.mark.parametrize("name,turn", NEW.items())
def test_natural_and_manual_spawns_respect_unlock(definition, name, turn):
    seen_natural, seen_manual = set(), set()
    for seed in range(32):
        d = replace(definition, rules=replace(definition.rules, seed=seed))
        state = engine.new_game(d)
        for n in (turn - 1, turn):
            natural = engine.spawn_monsters(d, n)
            state = replace(state, turn=n, current_monsters=natural)
            refreshed = engine.preview_monster_refresh(d, state)
            assert all(archetype(d, m.archetype_id).min_turn <= n for m in (*natural, *refreshed))
            if n == turn - 1:
                assert name not in {m.archetype_id for m in (*natural, *refreshed)}
            else:
                seen_natural.update(m.archetype_id for m in natural)
                seen_manual.update(m.archetype_id for m in refreshed)
    assert name in seen_natural
    assert name in seen_manual


@pytest.mark.parametrize("name", NEW)
@pytest.mark.parametrize("tier,multiplier,count", [("elite", 1.3, 1), ("boss", 1.5, 2)])
def test_tiers_keep_base_mechanics_and_scale_once(definition, name, tier, multiplier, count):
    normal = monster(definition, name)
    for theme in definition.rules.monster_spawn.bonus_skill_themes:
        for bonus in permutations(theme.skills, count):
            m = monster(definition, name, tier=tier, bonus=bonus)
            assert m.skills == normal.skills + bonus
            assert m.stats.hp == int(normal.stats.hp * multiplier)
            assert m.stats.attack == int(normal.stats.attack * multiplier)
            assert m.stats.defense == int(normal.stats.defense * multiplier)
            assert m.stats.speed == int(normal.stats.speed * multiplier)
            assert m.reward.gold > normal.reward.gold
            assert m.reward.experience > normal.reward.experience


@pytest.mark.parametrize("mp,raging", [(0, False), (59, False), (60, True), (100, True)])
def test_mana_hunt_uses_current_target_mp_at_sixty_percent(definition, mp, raging):
    m = monster(definition, "mana_hound")
    dummy = Combatant("target", CombatStats(2000, 100, 0, 0, 0, 0), CombatResources(2000, mp))
    result = run_auto_battle(combatant(m), dummy, max_actions=1)
    assert result.events[0].damage == (int(m.stats.attack * 1.45) if raging else m.stats.attack)
    assert result.time_elapsed == (5 if raging else 6)


def test_zero_max_mp_does_not_trigger_mana_hunt(definition):
    m = monster(definition, "mana_hound")
    dummy = Combatant("target", CombatStats(2000, 0, 0, 0, 0, 0))
    result = run_auto_battle(combatant(m), dummy, max_actions=1)
    assert result.events[0].damage == m.stats.attack


def test_mana_hunt_rechecks_after_target_spends_mp(definition):
    m = monster(definition, "mana_hound")
    spend = Skill("spend_mp", "耗蓝", "active", SkillCondition("always"),
                  (SkillEffect("heal", value=0, target="self"),), mp_cost=50)
    hero = Combatant("hero", CombatStats(2000, 100, 1, 0, 40, 0), skills=(spend,))
    result = run_auto_battle(hero, combatant(m), max_actions=2)
    assert result.events[0].skill_id == "spend_mp"
    assert result.left_resources.current_mp == 50
    assert result.events[1].actor_side == "right"
    assert result.events[1].damage == m.stats.attack


def test_crystal_guardian_has_less_armor_and_more_attack_at_half_hp(definition):
    m = monster(definition, "crystal_guardian")
    hitter = Combatant("hitter", CombatStats(2000, 0, 200, 0, 100, 0))
    full_hit = run_auto_battle(hitter, combatant(m), max_actions=1)
    broken_hit = run_auto_battle(hitter, combatant(m, hp=m.stats.hp // 2), max_actions=1)
    assert broken_hit.events[0].damage > full_hit.events[0].damage
    dummy = Combatant("dummy", CombatStats(2000, 0, 0, 0, 0, 0))
    full_attack = run_auto_battle(combatant(m), dummy, max_actions=1)
    broken_attack = run_auto_battle(combatant(m, hp=m.stats.hp // 2), dummy, max_actions=1)
    assert broken_attack.events[0].damage > full_attack.events[0].damage


def test_blood_pact_repeats_and_pays_hp_on_each_attack(definition):
    m = monster(definition, "blood_fanatic")
    dummy = Combatant("dummy", CombatStats(2000, 0, 0, 1000, 0, 0))
    result = run_auto_battle(combatant(m), dummy, max_actions=2)
    assert [e.skill_id for e in result.events] == ["blood_pact_strike", "blood_pact_strike"]
    assert result.left_resources.current_hp == m.stats.hp - 90
    assert result.right_resources.current_hp == 1998


def test_blood_pact_can_kill_the_monster_without_awarding_a_draw(definition):
    m = monster(definition, "blood_fanatic")
    dummy = Combatant("dummy", CombatStats(2000, 0, 0, 1000, 0, 0))
    result = run_auto_battle(dummy, combatant(m, hp=45))
    assert result.outcome == "left_win"
    assert result.right_resources.current_hp == 0
    assert result.left_resources.current_hp == 1999


def test_blood_pact_self_damage_does_not_make_a_mutual_kill_a_win(definition):
    m = monster(definition, "blood_fanatic")
    fragile = Combatant("fragile", CombatStats(1, 0, 0, 0, 0, 0))
    result = run_auto_battle(fragile, combatant(m, hp=45))
    assert result.outcome == "draw"
    assert result.left_resources.current_hp == result.right_resources.current_hp == 0


def test_fanatic_bonus_opening_does_not_pay_blood_pact_hp_cost(definition):
    theme = next(t for t in definition.rules.monster_spawn.bonus_skill_themes if t.theme_id == "brute")
    ambush = next(s for s in theme.skills if s.skill_id == "ambush_strike")
    slam = next(s for s in theme.skills if s.skill_id == "crushing_slam")
    m = monster(definition, "blood_fanatic", tier="boss", bonus=(ambush, slam))
    dummy = Combatant("dummy", CombatStats(2000, 0, 0, 1000, 0, 0))
    result = run_auto_battle(combatant(m), dummy, max_actions=1)
    assert result.events[0].skill_id == "ambush_strike"
    assert result.left_resources.current_hp == m.stats.hp


@pytest.mark.parametrize("name,turn", NEW.items())
def test_forecast_exposes_actual_new_skills_and_rewards(definition, name, turn):
    state = engine.new_game(definition)
    result = query_forecast(definition, state, kind="monsters", start_turn=turn, end_turn=34,
                            name_contains=name, detail="full")
    assert result["total_matches"] > 0
    a = archetype(definition, name)
    for entry in result["items"]:
        assert entry["turn"] >= turn
        assert [s["skill_id"] for s in entry["skills"][:len(a.skills)]] == [s.skill_id for s in a.skills]
        assert entry["base_reward"]["gold"] > 0
        assert entry["forecast_ref"].startswith("future:")
    text = skill_summary([_skill_to_dict(s) for s in a.skills])
    if name == "mana_hound":
        assert "目标MP不低于60%" in text
    elif name == "crystal_guardian":
        assert "自身HP不高于50%" in text
    else:
        assert "自身受到45点伤害" in text


@pytest.mark.parametrize("name,turn", NEW.items())
def test_arena_pool_unlocks_new_archetype_and_uses_its_skills(definition, name, turn):
    before_rng, after_rng = random.Random(42), random.Random(42)
    before = [metrics._sample_arena_monster(definition, before_rng,
        wave_index=turn - 1, index=i, difficulty=50) for i in range(300)]
    after = [metrics._sample_arena_monster(definition, after_rng,
        wave_index=turn, index=i, difficulty=50) for i in range(300)]
    skills = archetype(definition, name).skills
    assert not any(m.skills == skills for m in before)
    assert any(m.skills == skills for m in after)


def test_new_targets_survive_observation_round_trip(definition):
    state = engine.new_game(definition)
    pool = list(engine.spawn_monsters(definition, 29))
    for i, name in enumerate(NEW):
        pool[i] = replace(monster(definition, name, tier="boss"), monster_id=pool[i].monster_id)
    state = replace(state, turn=29, current_monsters=tuple(pool))
    restored = _restore_from_observation(definition, build_observation(definition, state))
    assert restored.current_monsters == state.current_monsters


@pytest.mark.parametrize("preset", ["default", "full"])
def test_other_presets_do_not_gain_new_monsters(preset):
    d = load_game_definition(ROOT / "data/presets" / preset)
    assert not (NEW.keys() & {a.archetype_id for a in d.content.monster_archetypes})


def test_frozen_full_does_not_gain_new_monsters():
    protocols.verify_full_release()
    runtime = protocols.full_runtime()
    d = runtime.load_game_definition(protocols.FULL_ROOT / "data/presets/full")
    assert not (NEW.keys() & {a.archetype_id for a in d.content.monster_archetypes})
