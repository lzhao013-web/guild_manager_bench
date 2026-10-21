from dataclasses import replace

import pytest

from guild_manager_bench.game.actions import CraftAction, EquipAction, PurchaseUpgradeAction
from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.engine import (
    apply_preparation_action,
    effective_adventurer_skills,
    effective_adventurer_stats,
    effective_crafting_recipe,
    new_game,
    spawn_monsters,
)
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.game.state import AdventurerState
from guild_manager_bench.game.upgrades import UpgradeError
from guild_manager_bench.runtime.observation import build_observation


@pytest.fixture(scope="module")
def definition():
    return load_game_definition("data/presets/advanced")


@pytest.fixture(scope="module")
def talents(definition):
    return {skill.skill_id: skill for node in definition.content.global_upgrades for skill in node.skills}


@pytest.mark.parametrize("talent_id,hp,mp", [
    ("talent_emergency_care", 30, 20),
    ("talent_mana_reflux", 100, 5),
])
def test_recovery_talent_does_not_replace_a_payable_finisher(talents, talent_id, hp, mp):
    finisher = Skill(
        "finisher", "致胜主动", "active", SkillCondition("always"),
        (SkillEffect("true_damage", 50),), mp_cost=5, priority=5,
    )
    stats = CombatStats(hp=100, mp=20, attack=5, defense=0, speed=10, recovery=0)
    foe = CombatStats(hp=40, mp=0, attack=100, defense=0, speed=10, recovery=0)
    result = run_auto_battle(
        Combatant("hero", stats, CombatResources(hp, mp), (finisher, talents[talent_id])),
        Combatant("foe", foe),
    )
    assert result.outcome == "left_win"
    assert result.events[-1].skill_id == "finisher"
    assert result.actions_taken == 1
    assert result.events[0].skill_id == talent_id


def test_reflux_is_used_when_original_skill_is_unaffordable_then_allows_casting(talents):
    spell = Skill(
        "spell", "法术", "active", SkillCondition("always"),
        (SkillEffect("true_damage", 50),), mp_cost=6, priority=10,
    )
    stats = CombatStats(hp=100, mp=20, attack=5, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(
        Combatant("hero", stats, CombatResources(100, 0), (spell, talents["talent_mana_reflux"])),
        Combatant("foe", foe), max_actions=4,
    )
    assert [event.skill_id for event in result.events] == ["talent_mana_reflux", "spell", "spell", None, None]
    assert result.events[0].damage == 0
    assert result.left_resources.current_mp == 0


def test_emergency_care_precedes_reflux_without_consuming_normal_action(talents):
    stats = CombatStats(hp=100, mp=20, attack=5, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(
        Combatant("hero", stats, CombatResources(30, 0), tuple(talents[key] for key in ("talent_mana_reflux", "talent_emergency_care"))),
        Combatant("foe", foe), max_actions=2,
    )
    assert [event.skill_id for event in result.events] == ["talent_emergency_care", "talent_mana_reflux", None, None]
    assert result.left_resources.current_hp == 55
    assert result.left_resources.current_mp == 12
    assert [event.damage for event in result.events] == [0, 0, 5, 5]


def test_current_character_and_equipment_actives_precede_recovery_talents(definition, talents):
    skills = []
    for actor in (*definition.content.adventurers, *definition.content.recruitable_adventurers):
        skills.extend(actor.skills)
        for unlock in actor.level_skill_unlocks:
            skills.extend(unlock.skills)
    for item in definition.content.equipment_templates:
        skills.extend(item.skills)
    actives = [skill for skill in skills if skill.kind == "active"]
    assert actives
    for key in ("talent_emergency_care", "talent_mana_reflux"):
        assert all(skill.priority > talents[key].priority for skill in actives)


@pytest.mark.parametrize("node_id,class_id,monster_id,tier,seed", [
    ("emergency_care", "plague_mage", "hellhound", "normal", 42),
    ("mana_reflux", "spellshot_mage", "orc_warrior", "boss", 43),
])
def test_recorded_recovery_regressions_remain_winnable(definition, node_id, class_id, monster_id, tier, seed):
    # 固定复现前期机制实验，免费提供必要前置，不代表正常经营可在T10买到。
    d = replace(definition, rules=replace(definition.rules, seed=seed))
    template = next(item for item in d.content.recruitable_adventurers if item.template_id == class_id)
    hero = AdventurerState(
        adventurer_id="hero", name=template.name, template_id=class_id,
        base_stats=template.base_stats, resources=CombatResources.full(template.base_stats),
        skills=template.skills, level_skill_unlocks=template.level_skill_unlocks,
        stat_growth_per_level=template.stat_growth_per_level, level=3,
    )
    prerequisites = {"herb_garden", "supply_post", "training_grounds", "blacksmith", "scout_network"}
    if node_id == "mana_reflux":
        prerequisites.add("meditation_hall")
    materials = {cost.material_id: 100000 for item in (*d.content.global_upgrades, *d.content.crafting_recipes) for cost in item.material_costs}
    state = replace(new_game(d), turn=10, adventurers=(hero,), gold=100000, experience_pool=100000, materials=materials, unlocked_upgrade_ids=frozenset(prerequisites))
    kit = ("t1_heavy_sword", "t1_heavy_shield", "t1_heavy_armor", "t1_light_helmet", "t1_light_boots", "t1_light_charm")
    for template_id in kit:
        recipe = next(item for item in d.content.crafting_recipes if item.output_template_id == template_id)
        instance_id = f"eq_{state.next_equipment_instance_number:04d}"
        state = apply_preparation_action(d, state, CraftAction(recipe.recipe_id))
        state = apply_preparation_action(d, state, EquipAction("hero", instance_id))
    monster = next(item for item in spawn_monsters(d, 10) if item.archetype_id == monster_id and item.tier == tier)

    def fight(candidate):
        actor = candidate.adventurers[0]
        return run_auto_battle(
            Combatant("hero", effective_adventurer_stats(d, candidate, actor), skills=effective_adventurer_skills(d, candidate, actor)),
            Combatant("foe", monster.stats, skills=monster.skills),
        )

    baseline = fight(state)
    upgraded = fight(apply_preparation_action(d, state, PurchaseUpgradeAction(node_id)))
    assert baseline.outcome == "left_win"
    assert upgraded.outcome == "left_win"
    assert upgraded.events[-1].skill_id == baseline.events[-1].skill_id


@pytest.mark.parametrize("node_id,gold,experience", [
    ("craft_standardization", 150, 50),
    ("training_manual", 100, 80),
])
def test_economy_revised_purchase_boundary_and_atomic_failure(definition, node_id, gold, experience):
    node = next(item for item in definition.content.global_upgrades if item.upgrade_id == node_id)
    state = replace(
        new_game(definition), gold=gold, experience_pool=experience,
        materials={cost.material_id: cost.quantity for cost in node.material_costs},
        unlocked_upgrade_ids=frozenset(node.required_upgrade_ids),
    )
    after = apply_preparation_action(definition, state, PurchaseUpgradeAction(node_id))
    assert after.gold == 0 and after.experience_pool == 0
    assert all(amount == 0 for amount in after.materials.values())
    assert node_id in after.unlocked_upgrade_ids
    assert node.craft_gold_discount_percent == (15 if node_id == "craft_standardization" else 0)
    assert node.hunt_experience_bonus_percent == (6 if node_id == "training_manual" else 0)
    for field in ("gold", "experience_pool"):
        short = replace(state, **{field: getattr(state, field) - 1})
        before = build_observation(definition, short)
        with pytest.raises(UpgradeError):
            apply_preparation_action(definition, short, PurchaseUpgradeAction(node_id))
        assert build_observation(definition, short) == before


def test_craft_standardization_requires_only_early_iron_and_blacksmith(definition):
    node = next(u for u in definition.content.global_upgrades if u.upgrade_id == "craft_standardization")
    assert node.required_upgrade_ids == ("blacksmith",)
    assert {c.material_id: c.quantity for c in node.material_costs} == {"iron_ore": 4}
    state = replace(
        new_game(definition), gold=150, experience_pool=50,
        materials={"iron_ore": 4}, unlocked_upgrade_ids=frozenset({"blacksmith"}),
    )
    after = apply_preparation_action(definition, state, PurchaseUpgradeAction(node.upgrade_id))
    assert (after.gold, after.experience_pool, dict(after.materials)) == (0, 0, {"iron_ore": 0})
    for short in (replace(state, materials={"iron_ore": 3}), replace(state, unlocked_upgrade_ids=frozenset())):
        before = build_observation(definition, short)
        with pytest.raises(UpgradeError):
            apply_preparation_action(definition, short, PurchaseUpgradeAction(node.upgrade_id))
        assert build_observation(definition, short) == before


def test_craft_standardization_fifteen_percent_discount_matches_all_real_recipes(definition):
    state = replace(
        new_game(definition), unlocked_upgrade_ids=frozenset({"blacksmith", "craft_standardization"}),
    )
    recipes = build_observation(definition, state)["crafting_recipes"]
    for raw, observed in zip(definition.content.crafting_recipes, recipes):
        expected = (raw.gold_cost * 85 + 99) // 100
        assert effective_crafting_recipe(definition, state, raw).gold_cost == expected
        assert (observed["base_gold_cost"], observed["gold_cost"]) == (raw.gold_cost, expected)
    raw = next(r for r in definition.content.crafting_recipes if r.recipe_id == "recipe_t1_heavy_sword")
    assert raw.gold_cost == 25
    state = replace(state, gold=22, materials={c.material_id: c.quantity for c in raw.material_costs})
    crafted = apply_preparation_action(definition, state, CraftAction(raw.recipe_id))
    assert crafted.gold == 0
    assert all(amount == 0 for amount in crafted.materials.values())
    assert len(crafted.equipment_inventory) == 1
