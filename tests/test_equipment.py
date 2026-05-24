import pytest

from guild_manager_bench.game.equipment import (
    EquippedItem,
    EquipmentLoadout,
    EquipmentTemplate,
    apply_equipment_stats,
    combine_equipment_skills,
)
from guild_manager_bench.game.models import CombatStatModifier, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect


def test_apply_equipment_stats_adds_all_modifiers() -> None:
    base_stats = CombatStats(hp=100, mp=10, attack=8, defense=4, speed=5, recovery=1)
    weapon = EquipmentTemplate(
        equipment_id="iron_sword",
        name="铁剑",
        slot="main_hand",
        stat_modifier=CombatStatModifier(attack=5),
    )
    armor = EquipmentTemplate(
        equipment_id="leather_armor",
        name="皮甲",
        slot="armor",
        stat_modifier=CombatStatModifier(hp=20, defense=3),
    )

    result = apply_equipment_stats(base_stats, (weapon, armor))

    assert result == CombatStats(hp=120, mp=10, attack=13, defense=7, speed=5, recovery=1)


def test_combine_equipment_skills_appends_equipment_skills() -> None:
    base_skill = Skill(
        skill_id="base_strike",
        name="基础打击",
        kind="active",
        condition=SkillCondition(condition_type="always"),
        effects=(SkillEffect(effect_type="damage_multiplier", value=1.2),),
        priority=10,
    )
    equipment_skill = Skill(
        skill_id="blade_focus",
        name="剑术专注",
        kind="passive",
        condition=SkillCondition(condition_type="always"),
        effects=(SkillEffect(effect_type="stat_bonus", stat="attack", value=2, target="self"),),
    )
    weapon = EquipmentTemplate(
        equipment_id="focused_blade",
        name="专注之刃",
        slot="main_hand",
        skills=(equipment_skill,),
    )

    result = combine_equipment_skills((base_skill,), (weapon,))

    assert result == (base_skill, equipment_skill)


def test_apply_equipment_stats_rejects_duplicate_slots() -> None:
    first_weapon = EquipmentTemplate(
        equipment_id="iron_sword",
        name="铁剑",
        slot="main_hand",
        stat_modifier=CombatStatModifier(attack=5),
    )
    second_weapon = EquipmentTemplate(
        equipment_id="steel_sword",
        name="钢剑",
        slot="main_hand",
        stat_modifier=CombatStatModifier(attack=8),
    )

    with pytest.raises(ValueError):
        apply_equipment_stats(
            CombatStats(hp=100, mp=10, attack=8, defense=4, speed=5, recovery=1),
            (first_weapon, second_weapon),
        )


def test_equipment_loadout_rejects_duplicate_slots() -> None:
    with pytest.raises(ValueError):
        EquipmentLoadout(
            items=(
                EquippedItem(slot="main_hand", instance_id="eq_1"),
                EquippedItem(slot="main_hand", instance_id="eq_2"),
            )
        )


def test_equipment_loadout_returns_equipped_instance_ids() -> None:
    loadout = EquipmentLoadout(
        items=(
            EquippedItem(slot="main_hand", instance_id="eq_1"),
            EquippedItem(slot="armor", instance_id="eq_2"),
        )
    )

    assert loadout.equipped_instance_ids() == ("eq_1", "eq_2")


def test_two_hand_rejects_main_or_off_hand_equipment() -> None:
    greatsword = EquipmentTemplate(
        equipment_id="greatsword",
        name="巨剑",
        slot="two_hand",
        stat_modifier=CombatStatModifier(attack=12),
    )
    shield = EquipmentTemplate(
        equipment_id="wooden_shield",
        name="木盾",
        slot="off_hand",
        stat_modifier=CombatStatModifier(defense=5),
    )

    with pytest.raises(ValueError):
        apply_equipment_stats(
            CombatStats(hp=100, mp=10, attack=8, defense=4, speed=5, recovery=1),
            (greatsword, shield),
        )


def test_loadout_rejects_two_hand_with_main_hand() -> None:
    with pytest.raises(ValueError):
        EquipmentLoadout(
            items=(
                EquippedItem(slot="two_hand", instance_id="eq_1"),
                EquippedItem(slot="main_hand", instance_id="eq_2"),
            )
        )
