import pytest

from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect


def test_hp_condition_accepts_ratio_value() -> None:
    condition = SkillCondition(condition_type="self_hp_pct_lte", value=0.5)

    assert condition.value == 0.5


def test_hp_condition_rejects_out_of_range_ratio() -> None:
    with pytest.raises(ValueError):
        SkillCondition(condition_type="target_hp_pct_gte", value=1.1)


def test_condition_can_combine_multiple_child_conditions() -> None:
    condition = SkillCondition(
        condition_type="all",
        conditions=(
            SkillCondition(condition_type="self_hp_pct_lte", value=0.5),
            SkillCondition(condition_type="target_hp_pct_gte", value=0.3),
        ),
    )

    assert len(condition.conditions) == 2


def test_active_skill_can_be_once_per_battle() -> None:
    skill = Skill(
        skill_id="final_strike",
        name="决死一击",
        kind="active",
        condition=SkillCondition(condition_type="target_hp_pct_lte", value=0.3),
        effects=(SkillEffect(effect_type="damage_multiplier", value=2.0),),
        mp_cost=5,
        priority=100,
        once_per_battle=True,
    )

    assert skill.once_per_battle is True


def test_passive_skill_rejects_once_per_battle() -> None:
    with pytest.raises(ValueError):
        Skill(
            skill_id="berserk",
            name="狂战",
            kind="passive",
            condition=SkillCondition(condition_type="self_hp_pct_lte", value=0.5),
            effects=(SkillEffect(effect_type="stat_multiplier", stat="attack", value=1.5, target="self"),),
            once_per_battle=True,
        )


def test_passive_skill_accepts_stat_effect() -> None:
    skill = Skill(
        skill_id="guard_stance",
        name="守备姿态",
        kind="passive",
        condition=SkillCondition(condition_type="self_hp_pct_lte", value=0.5),
        effects=(SkillEffect(effect_type="stat_bonus", stat="defense", value=3, target="self"),),
    )

    assert skill.effects[0].effect_type == "stat_bonus"


def test_passive_skill_rejects_active_effect() -> None:
    with pytest.raises(ValueError):
        Skill(
            skill_id="wrong_passive",
            name="错误被动",
            kind="passive",
            condition=SkillCondition(condition_type="always"),
            effects=(SkillEffect(effect_type="heal", value=5, target="self"),),
        )


def test_active_skill_rejects_stat_effect() -> None:
    with pytest.raises(ValueError):
        Skill(
            skill_id="wrong_active",
            name="错误主动",
            kind="active",
            condition=SkillCondition(condition_type="always"),
            effects=(SkillEffect(effect_type="stat_bonus", stat="attack", value=3, target="self"),),
        )
