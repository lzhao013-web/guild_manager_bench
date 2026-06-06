from guild_manager_bench.game.combat import (
    Combatant,
    calculate_basic_attack_damage,
    run_auto_battle,
)
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import (
    Skill,
    SkillCondition,
    SkillEffect,
    StatusDefinition,
)


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


def test_winner_does_not_recover_after_battle() -> None:
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
    assert result.left_resources.current_hp == 18


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


def test_run_auto_battle_can_skip_events_without_changing_result() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=1, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="poison_dart",
                name="毒镖",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(
                    SkillEffect(
                        effect_type="apply_status",
                        target="target",
                        status=StatusDefinition(
                            status_id="poison",
                            name="中毒",
                            duration=2,
                            polarity="negative",
                            effects=(
                                SkillEffect(effect_type="true_damage", value=3),
                            ),
                        ),
                    ),
                ),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=1, defense=0, speed=10, recovery=0),
    )

    logged = run_auto_battle(left, right, max_actions=4)
    unlogged = run_auto_battle(left, right, max_actions=4, record_events=False)

    assert unlogged.events == ()
    assert unlogged.outcome == logged.outcome
    assert unlogged.reason == logged.reason
    assert unlogged.actions_taken == logged.actions_taken
    assert unlogged.time_elapsed == logged.time_elapsed
    assert unlogged.left_resources.current_hp == logged.left_resources.current_hp
    assert unlogged.left_resources.current_mp == logged.left_resources.current_mp
    assert unlogged.right_resources.current_hp == logged.right_resources.current_hp
    assert unlogged.right_resources.current_mp == logged.right_resources.current_mp


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


def test_mp_condition_controls_active_skill_use() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=20, mp=10, attack=2, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=20, current_mp=4),
        skills=(
            Skill(
                skill_id="high_mp_burst",
                name="满盈爆发",
                kind="active",
                condition=SkillCondition(condition_type="self_mp_pct_gte", value=0.5),
                effects=(SkillEffect(effect_type="damage_multiplier", value=5.0),),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=1)

    assert result.events[0].action_type == "basic_attack"
    assert result.events[0].damage == 2


def test_action_index_condition_limits_opening_skill() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=3, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="opening_strike",
                name="开场打击",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(SkillEffect(effect_type="damage_bonus", value=2),),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=30, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=2)

    assert [event.action_type for event in result.events] == ["skill", "basic_attack"]
    assert [event.damage for event in result.events] == [5, 3]


def test_extended_active_effects_apply_to_resources_and_damage() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=50, mp=10, attack=4, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=20, current_mp=4),
        skills=(
            Skill(
                skill_id="blood_channel",
                name="血脉导流",
                kind="active",
                condition=SkillCondition(condition_type="always"),
                effects=(
                    SkillEffect(effect_type="heal_percent", value=0.2, target="self"),
                    SkillEffect(effect_type="mp_restore", value=5, target="self"),
                    SkillEffect(effect_type="damage_bonus", value=3),
                    SkillEffect(effect_type="true_damage", value=6),
                    SkillEffect(effect_type="self_damage", value=4),
                ),
                mp_cost=3,
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=40, mp=0, attack=0, defense=10, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=1)

    assert result.events[0].action_type == "skill"
    assert result.events[0].damage == 10
    assert result.events[0].target_hp == 30
    assert result.events[0].healing == 10
    assert result.left_resources.current_hp == 26
    assert result.left_resources.current_mp == 6


def test_self_damage_can_defeat_actor() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=3, current_mp=0),
        skills=(
            Skill(
                skill_id="reckless_blow",
                name="舍身击",
                kind="active",
                condition=SkillCondition(condition_type="always"),
                effects=(SkillEffect(effect_type="self_damage", value=5),),
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=10, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=1)

    assert result.outcome == "right_win"
    assert result.reason == "actor_defeated"


def test_negative_status_deals_damage_on_holder_actions() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=1, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="poison_dart",
                name="毒镖",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(
                    SkillEffect(
                        effect_type="apply_status",
                        target="target",
                        status=StatusDefinition(
                            status_id="poison",
                            name="中毒",
                            duration=2,
                            polarity="negative",
                            effects=(
                                SkillEffect(effect_type="true_damage", value=3),
                            ),
                        ),
                    ),
                ),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=20, mp=0, attack=1, defense=0, speed=10, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=4)
    status_events = [event for event in result.events if event.action_type == "status"]

    assert [event.damage for event in status_events] == [3, 3]
    assert status_events[0].status_name == "中毒"
    assert result.actions_taken == 4


def test_negative_stat_status_changes_later_damage() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=10, defense=0, speed=10, recovery=0),
        skills=(
            Skill(
                skill_id="armor_break",
                name="破甲",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(
                    SkillEffect(
                        effect_type="apply_status",
                        target="target",
                        status=StatusDefinition(
                            status_id="armor_break",
                            name="破甲",
                            duration=2,
                            polarity="negative",
                            effects=(
                                SkillEffect(
                                    effect_type="stat_multiplier",
                                    stat="defense",
                                    value=0.5,
                                    target="self",
                                ),
                            ),
                        ),
                    ),
                ),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=30, mp=0, attack=0, defense=8, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=2)

    assert [event.damage for event in result.events] == [0, 6]


def test_positive_status_can_heal_and_buff_holder() -> None:
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=4, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=10, current_mp=0),
        skills=(
            Skill(
                skill_id="battle_trance",
                name="战斗专注",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(
                    SkillEffect(
                        effect_type="apply_status",
                        target="self",
                        status=StatusDefinition(
                            status_id="trance",
                            name="专注",
                            duration=2,
                            polarity="positive",
                            effects=(
                                SkillEffect(effect_type="heal", value=2, target="self"),
                                SkillEffect(
                                    effect_type="stat_bonus",
                                    stat="attack",
                                    value=3,
                                    target="self",
                                ),
                            ),
                        ),
                    ),
                ),
                priority=100,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=30, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=3)
    status_events = [event for event in result.events if event.action_type == "status"]
    action_events = [event for event in result.events if event.action_type != "status"]

    assert [event.healing for event in status_events] == [2, 2]
    assert [event.damage for event in action_events] == [0, 7, 7]
    assert result.left_resources.current_hp == 14


def test_free_skill_applies_effects_and_basic_attack() -> None:
    """free 技能触发效果后还会执行普通攻击，不浪费回合。"""
    left = Combatant(
        combatant_id="left",
        stats=CombatStats(hp=30, mp=0, attack=5, defense=0, speed=10, recovery=0),
        resources=CombatResources(current_hp=12, current_mp=0),
        skills=(
            Skill(
                skill_id="rally",
                name="鼓舞",
                kind="active",
                condition=SkillCondition(condition_type="action_index_lte", value=1),
                effects=(
                    SkillEffect(
                        effect_type="apply_status",
                        target="self",
                        status=StatusDefinition(
                            status_id="inspired",
                            name="鼓舞",
                            duration=2,
                            polarity="positive",
                            effects=(
                                SkillEffect(
                                    effect_type="stat_bonus",
                                    stat="attack",
                                    value=3,
                                    target="self",
                                ),
                            ),
                        ),
                    ),
                ),
                priority=100,
                once_per_battle=True,
                free=True,
            ),
        ),
    )
    right = Combatant(
        combatant_id="right",
        stats=CombatStats(hp=30, mp=0, attack=0, defense=0, speed=0, recovery=0),
    )

    result = run_auto_battle(left, right, max_actions=4)

    action_events = [event for event in result.events if event.action_type != "status"]

    # Action 1: free skill fires, applies buff, then bonus basic attack.
    # Buff is already active when basic attack resolves → attack=5+3=8.
    assert action_events[0].action_type == "skill"
    assert action_events[0].skill_id == "rally"
    assert action_events[0].damage == 8

    # Action 2: buff active (tick+decrement from snapshot=empty, stays remaining=2).
    assert action_events[1].action_type == "basic_attack"
    assert action_events[1].damage == 8

    # Action 3: buff still active (decrement to remaining=1 on action 2's snapshot).
    assert action_events[2].action_type == "basic_attack"
    assert action_events[2].damage == 8

    # Action 4: buff expired (decrement to remaining=0 on action 3's snapshot).
    assert action_events[3].action_type == "basic_attack"
    assert action_events[3].damage == 5
