import pytest

from guild_manager_bench.game.models import CombatStatModifier, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.game.upgrades import (
    GlobalUpgrade,
    UpgradeError,
    UpgradeInventory,
    apply_upgrade_stats,
    can_purchase_upgrade,
    combine_upgrade_skills,
    combine_party_size_bonus,
    missing_upgrade_requirements,
    purchase_upgrade,
)


def test_can_purchase_upgrade_when_gold_and_requirements_are_met() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="training_ground_2",
        name="训练场 II",
        gold_cost=50,
        required_upgrade_ids=("training_ground_1",),
    )
    inventory = UpgradeInventory(
        gold=60,
        unlocked_upgrade_ids=frozenset({"training_ground_1"}),
    )

    assert can_purchase_upgrade(upgrade, inventory)


def test_missing_upgrade_requirements_reports_gold_and_prerequisites() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="training_ground_2",
        name="训练场 II",
        gold_cost=50,
        required_upgrade_ids=("training_ground_1",),
    )
    inventory = UpgradeInventory(gold=30)

    assert missing_upgrade_requirements(upgrade, inventory) == {
        "gold": 20,
        "required_upgrade_ids": ("training_ground_1",),
    }
    assert not can_purchase_upgrade(upgrade, inventory)


def test_purchase_upgrade_consumes_gold_and_unlocks_upgrade() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="training_ground_1",
        name="训练场 I",
        gold_cost=50,
    )
    inventory = UpgradeInventory(gold=60)

    result = purchase_upgrade(upgrade, inventory)

    assert result.upgrade_id == "training_ground_1"
    assert result.inventory.gold == 10
    assert result.inventory.unlocked_upgrade_ids == frozenset({"training_ground_1"})


def test_purchase_upgrade_does_not_mutate_input_inventory() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="training_ground_1",
        name="训练场 I",
        gold_cost=50,
    )
    inventory = UpgradeInventory(gold=60)

    purchase_upgrade(upgrade, inventory)

    assert inventory.gold == 60
    assert inventory.unlocked_upgrade_ids == frozenset()


def test_purchase_upgrade_rejects_already_unlocked_upgrade() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="training_ground_1",
        name="训练场 I",
        gold_cost=50,
    )
    inventory = UpgradeInventory(
        gold=60,
        unlocked_upgrade_ids=frozenset({"training_ground_1"}),
    )

    with pytest.raises(UpgradeError):
        purchase_upgrade(upgrade, inventory)


def test_apply_upgrade_stats_adds_all_modifiers() -> None:
    base_stats = CombatStats(hp=100, mp=10, attack=8, defense=4, speed=5, recovery=1)
    attack_upgrade = GlobalUpgrade(
        upgrade_id="weapon_training",
        name="武器训练",
        gold_cost=50,
        stat_modifier=CombatStatModifier(attack=3),
    )
    endurance_upgrade = GlobalUpgrade(
        upgrade_id="endurance_training",
        name="耐力训练",
        gold_cost=50,
        stat_modifier=CombatStatModifier(hp=20, recovery=2),
    )

    result = apply_upgrade_stats(base_stats, (attack_upgrade, endurance_upgrade))

    assert result == CombatStats(hp=120, mp=10, attack=11, defense=4, speed=5, recovery=3)


def test_combine_upgrade_skills_appends_global_skills() -> None:
    base_skill = Skill(
        skill_id="base_strike",
        name="基础打击",
        kind="active",
        condition=SkillCondition(condition_type="always"),
        effects=(SkillEffect(effect_type="damage_multiplier", value=1.2),),
        priority=10,
    )
    upgrade_skill = Skill(
        skill_id="battle_focus",
        name="战斗专注",
        kind="passive",
        condition=SkillCondition(condition_type="always"),
        effects=(SkillEffect(effect_type="stat_bonus", stat="attack", value=2, target="self"),),
    )
    upgrade = GlobalUpgrade(
        upgrade_id="focus_training",
        name="专注训练",
        gold_cost=50,
        skills=(upgrade_skill,),
    )

    result = combine_upgrade_skills((base_skill,), (upgrade,))

    assert result == (base_skill, upgrade_skill)


def test_combine_party_size_bonus_adds_unlocked_upgrade_capacity() -> None:
    first = GlobalUpgrade(
        upgrade_id="contracts",
        name="契约",
        gold_cost=50,
        party_size_bonus=1,
    )
    second = GlobalUpgrade(
        upgrade_id="roster",
        name="名册",
        gold_cost=80,
        party_size_bonus=2,
    )

    assert combine_party_size_bonus((first, second)) == 3


def test_global_upgrade_rejects_self_requirement() -> None:
    with pytest.raises(ValueError):
        GlobalUpgrade(
            upgrade_id="training_ground_1",
            name="训练场 I",
            gold_cost=50,
            required_upgrade_ids=("training_ground_1",),
        )


def test_apply_upgrade_stats_rejects_duplicate_upgrades() -> None:
    upgrade = GlobalUpgrade(
        upgrade_id="weapon_training",
        name="武器训练",
        gold_cost=50,
        stat_modifier=CombatStatModifier(attack=3),
    )

    with pytest.raises(ValueError):
        apply_upgrade_stats(
            CombatStats(hp=100, mp=10, attack=8, defense=4, speed=5, recovery=1),
            (upgrade, upgrade),
        )
