from guild_manager_bench.game.combat import (
    Combatant,
    calculate_basic_attack_damage,
    run_auto_battle,
)
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect


def test_basic_attack_damage_has_minimum_one() -> None:
    attacker = CombatStats(hp=10, mp=0, attack=3, defense=0, speed=1, recovery=0)
    defender = CombatStats(hp=10, mp=0, attack=3, defense=10, speed=1, recovery=0)

    assert calculate_basic_attack_damage(attacker, defender) == 1


def test_faster_combatant_acts_first() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=20, mp=0, attack=3, defense=0, speed=10, recovery=0),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=3, defense=0, speed=5, recovery=0),
    )

    result = run_auto_battle(left, right)

    assert result.events[0].actor_id == "left"


def test_winner_recovers_without_exceeding_max_hp() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=10, defense=0, speed=10, recovery=20),
        resources=CombatResources(current_hp=18, current_mp=0),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    result = run_auto_battle(left, right)

    assert result.outcome == "left_win"
    assert result.left_resources.current_hp == 30


def test_run_auto_battle_does_not_mutate_input_resources() -> None:
    left_resources = CombatResources(current_hp=18, current_mp=0)
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=10, defense=0, speed=10, recovery=20),
        resources=left_resources,
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    run_auto_battle(left, right)

    assert left_resources.current_hp == 18


def test_zero_speed_combatants_draw() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=10, mp=0, attack=10, defense=0, speed=0, recovery=0),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=10, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right)

    assert result.outcome == "draw"
    assert result.reason == "no_combatant_can_act"


def test_overflowed_action_gauge_can_act_without_negative_time() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=1, defense=0, speed=1_000, recovery=0),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=30, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=3)

    assert result.reason == "max_actions_reached"
    assert [event.time_elapsed for event in result.events] == [1, 1, 1]


def test_active_damage_skill_replaces_basic_attack_and_spends_mp() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=20, mp=10, attack=5, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="power_strike",
                name="强力打击",
                kind="active",
                condition=SkillCondition(condition_type="always"),
                effects=(SkillEffect(effect_type="damage_multiplier", value=2.0),),
                mp_cost=3,
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    result = run_auto_battle(left, right)

    assert result.outcome == "left_win"
    assert result.events[0].action_type == "skill"
    assert result.events[0].skill_id == "power_strike"
    assert result.events[0].damage == 10
    assert result.left_resources.current_mp == 7


def test_once_per_battle_active_skill_only_triggers_once() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=20, mp=10, attack=2, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="opening_burst",
                name="开场爆发",
                kind="active",
                condition=SkillCondition(condition_type="always"),
                effects=(SkillEffect(effect_type="damage_multiplier", value=2.0),),
                priority=100,
                once_per_battle=True,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=2)

    assert [event.action_type for event in result.events] == ["skill", "basic_attack"]
    assert [event.damage for event in result.events] == [4, 2]


def test_passive_stat_multiplier_applies_while_condition_is_met() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=10, mp=0, attack=5, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=5, current_mp=0),
        skills=(
            Skill(
                skill_id="berserk",
                name="狂战",
                kind="passive",
                condition=SkillCondition(condition_type="self_hp_pct_lte", value=0.5),
                effects=(
                    SkillEffect(
                        effect_type="stat_multiplier",
                        stat="attack",
                        value=2.0,
                        target="self",
                    ),
                ),
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    result = run_auto_battle(left, right)

    assert result.outcome == "left_win"
    assert result.events[0].damage == 10


def test_active_heal_skill_supports_combined_conditions() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=20, mp=10, attack=1, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=4, current_mp=10),
        skills=(
            Skill(
                skill_id="self_heal",
                name="自愈",
                kind="active",
                condition=SkillCondition(
                    condition_type="all",
                    conditions=(
                        SkillCondition(condition_type="self_hp_pct_lte", value=0.5),
                        SkillCondition(condition_type="target_hp_pct_gte", value=0.5),
                    ),
                ),
                effects=(SkillEffect(effect_type="heal", value=10, target="self"),),
                mp_cost=5,
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=1, defense=0, speed=1, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=1)

    assert result.events[0].action_type == "skill"
    assert result.events[0].damage == 0
    assert result.events[0].healing == 10
    assert result.events[0].healing_target_side == "left"
    assert result.left_resources.current_hp == 14
    assert result.left_resources.current_mp == 5
