from dataclasses import replace

import pytest

from guild_manager_bench.game.actions import CraftAction, EndTurnAction, HuntAction, PurchaseUpgradeAction
from guild_manager_bench.game.crafting import CraftingRecipe, MaterialCost
from guild_manager_bench.game.engine import apply_preparation_action, end_turn, new_game, preview_battle
from guild_manager_bench.game.equipment import EquipmentTemplate
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.state import AdventurerState, GameContent, GameDefinition, GameRules, MonsterArchetype, MonsterSpawnRules, FloatCurve, RewardBundle
from guild_manager_bench.game.upgrades import GlobalUpgrade, UpgradeError, UpgradeInventory, missing_upgrade_requirements, purchase_upgrade
from guild_manager_bench.runtime.observation import build_observation


def test_talent_purchase_spends_all_resources_without_mutating_input():
    talent = GlobalUpgrade(
        upgrade_id="training", name="研究", gold_cost=20, experience_cost=75,
        material_costs=(MaterialCost("iron_ore", 3),),
    )
    inventory = UpgradeInventory(gold=30, experience_pool=100, materials={"iron_ore": 4, "herb": 2})
    result = purchase_upgrade(talent, inventory).inventory
    assert (result.gold, result.experience_pool, dict(result.materials)) == (10, 25, {"iron_ore": 1, "herb": 2})
    assert result.unlocked_upgrade_ids == {"training"}
    assert (inventory.gold, inventory.experience_pool, dict(inventory.materials)) == (30, 100, {"iron_ore": 4, "herb": 2})


def test_missing_resources_and_exclusivity_are_reported_together():
    talent = GlobalUpgrade(
        upgrade_id="war", name="战争", gold_cost=30, experience_cost=100,
        material_costs=(MaterialCost("iron_ore", 3),), required_upgrade_ids=("base",),
        exclusive_upgrade_ids=("arcane",),
    )
    inventory = UpgradeInventory(gold=20, experience_pool=70, materials={"iron_ore": 1}, unlocked_upgrade_ids=frozenset({"arcane"}))
    assert missing_upgrade_requirements(talent, inventory) == {
        "gold": 10, "experience": 30, "materials": {"iron_ore": 2},
        "required_upgrade_ids": ("base",), "exclusive_upgrade_ids": ("arcane",),
    }
    with pytest.raises(UpgradeError):
        purchase_upgrade(talent, inventory)
    assert inventory.gold == 20 and inventory.experience_pool == 70 and inventory.materials["iron_ore"] == 1


@pytest.fixture
def economy_definition():
    stats = CombatStats(hp=100, mp=20, attack=40, defense=20, speed=20, recovery=0)
    return GameDefinition(
        rules=GameRules(max_turns=3, seed=7, monster_spawn=MonsterSpawnRules(count_curve=FloatCurve(base=1))), starting_gold=150, starting_materials={"iron_ore": 10},
        content=GameContent(
            adventurers=(AdventurerState(adventurer_id="hero", name="测试员", base_stats=stats, resources=CombatResources.full(stats)),),
            monster_archetypes=(MonsterArchetype(archetype_id="slime", name="史莱姆", base_stats=CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0), base_reward=RewardBundle(gold=7, experience=61, materials={"herb": 1})),),
            equipment_templates=(EquipmentTemplate(equipment_id="sword", name="剑", slot="main_hand", stat_modifier=CombatStatModifier(attack=2)),),
            crafting_recipes=(CraftingRecipe.from_mapping(recipe_id="sword", name="剑", output_template_id="sword", gold_cost=11, material_costs={"iron_ore": 2}),),
            global_upgrades=(
                GlobalUpgrade(upgrade_id="craft", name="工艺", gold_cost=20, experience_cost=75, material_costs=(MaterialCost("iron_ore", 3),), craft_gold_discount_percent=25),
                GlobalUpgrade(upgrade_id="teach", name="教范", gold_cost=10, experience_cost=100, hunt_experience_bonus_percent=15),
            ),
        ),
    )


def test_discount_observation_and_actual_crafting_share_rounding_and_cost(economy_definition):
    d = economy_definition
    state = replace(new_game(d), experience_pool=200)
    state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id="craft"))
    assert (state.gold, state.experience_pool, state.materials["iron_ore"]) == (130, 125, 7)
    state = replace(state, gold=9)
    recipe = build_observation(d, state)["crafting_recipes"][0]
    assert (recipe["base_gold_cost"], recipe["gold_cost"], recipe["can_craft"]) == (11, 9, True)
    crafted = apply_preparation_action(d, state, CraftAction(recipe_id="sword"))
    assert crafted.gold == 0 and crafted.materials["iron_ore"] == 5
    assert len(crafted.equipment_inventory) == 1
    assert d.content.crafting_recipes[0].gold_cost == 11


def test_experience_bonus_matches_observation_preview_and_settlement(economy_definition):
    d = economy_definition
    state = replace(new_game(d), experience_pool=200)
    state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id="teach"))
    obs = build_observation(d, state)
    target = obs["monsters"][0]
    assert target["base_reward"]["experience"] == 61
    assert target["reward"] == {"gold": 7, "experience": 70, "materials": {"herb": 1}}
    preview = preview_battle(d, state, adventurer_id="hero", monster_id=target["monster_id"])
    result = end_turn(d, state, EndTurnAction(hunts=(HuntAction(adventurer_id="hero", monster_id=target["monster_id"]),)))
    assert preview.reward == result.battles[0].reward
    assert result.state.experience_pool == 170
    assert result.state.current_monsters[0].reward.experience == 61
    assert build_observation(d, result.state)["monsters"][0]["reward"]["experience"] == 70


def test_failed_hunt_does_not_receive_bonus(economy_definition):
    d = economy_definition
    state = replace(new_game(d), experience_pool=200)
    state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id="teach"))
    weak = replace(state.adventurers[0], base_stats=CombatStats(hp=1, mp=0, attack=1, defense=0, speed=1, recovery=0), resources=CombatResources(current_hp=1, current_mp=0))
    state = replace(state, adventurers=(weak,), current_monsters=(replace(state.current_monsters[0], stats=CombatStats(hp=100, mp=0, attack=100, defense=100, speed=100, recovery=0)),))
    result = end_turn(d, state, EndTurnAction(hunts=(HuntAction(adventurer_id="hero", monster_id=state.current_monsters[0].monster_id),)))
    assert not result.battles[0].won
    assert result.battles[0].reward == RewardBundle()
    assert result.state.experience_pool == 100


@pytest.mark.parametrize("upgrades", [
    (GlobalUpgrade("a", "A", 0, required_upgrade_ids=("missing",)),),
    (GlobalUpgrade("a", "A", 0, required_upgrade_ids=("b",)), GlobalUpgrade("b", "B", 0, required_upgrade_ids=("a",))),
    (GlobalUpgrade("a", "A", 0, exclusive_upgrade_ids=("missing",)),),
    (GlobalUpgrade("a", "A", 0, exclusive_upgrade_ids=("b",)), GlobalUpgrade("b", "B", 0)),
])
def test_invalid_talent_graph_is_rejected(economy_definition, upgrades):
    with pytest.raises(ValueError):
        replace(economy_definition.content, global_upgrades=upgrades)


def test_advanced_tree_has_35_nodes_and_three_mutually_exclusive_capstones():
    d = load_game_definition("data/presets/advanced")
    nodes = {u.upgrade_id: u for u in d.content.global_upgrades}
    assert len(nodes) == 35
    capstones = {"war_academy", "paladin_order", "arcane_council"}
    for uid in capstones:
        assert set(nodes[uid].exclusive_upgrade_ids) == capstones - {uid}
        assert nodes[uid].experience_cost > 0 and nodes[uid].material_costs
        state = replace(new_game(d), gold=100000, experience_pool=100000, materials={c.material_id: 100000 for u in nodes.values() for c in u.material_costs}, unlocked_upgrade_ids=frozenset(nodes) - capstones)
        state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id=uid))
        for other in capstones - {uid}:
            with pytest.raises(UpgradeError):
                apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id=other))
    assert all(not u.exclusive_upgrade_ids for u in nodes.values() if u.upgrade_id not in capstones)


@pytest.mark.parametrize("preset", ["default", "full"])
def test_original_presets_remain_gold_only(preset):
    d = load_game_definition(f"data/presets/{preset}")
    assert all(u.experience_cost == 0 and not u.material_costs and not u.exclusive_upgrade_ids for u in d.content.global_upgrades)
    assert not d.content.has_talent_tree
    observation = build_observation(d, new_game(d))
    assert "branch" not in observation["global_upgrades"][0]
    assert "base_gold_cost" not in observation["crafting_recipes"][0]
    assert "base_reward" not in observation["monsters"][0]


def test_advanced_inherits_full_except_talents_and_documented_balance_overrides():
    full = load_game_definition("data/presets/full")
    advanced = load_game_definition("data/presets/advanced")
    assert advanced.rules.final_preparation_only
    assert advanced.rules.monster_refresh.enabled
    assert not full.rules.monster_refresh.enabled
    assert replace(advanced.rules, final_preparation_only=False, monster_refresh=full.rules.monster_refresh) == full.rules
    assert advanced.scoring == full.scoring
    assert advanced.starting_gold == full.starting_gold
    assert advanced.starting_materials == full.starting_materials
    for field in ("adventurers", "experience_rules"):
        assert getattr(advanced.content, field) == getattr(full.content, field)
    original_monsters = full.content.monster_archetypes
    assert advanced.content.monster_archetypes[:len(original_monsters)] == original_monsters
    assert {a.archetype_id for a in advanced.content.monster_archetypes[len(original_monsters):]} == {
        "mana_hound", "crystal_guardian", "blood_fanatic",
    }
    assert advanced.content.crafting_recipes[:len(full.content.crafting_recipes)] == full.content.crafting_recipes
    added_recipes = advanced.content.crafting_recipes[len(full.content.crafting_recipes):]
    assert len(added_recipes) == 10
    assert all(r.output_template_id.startswith("seal_") for r in added_recipes)
    assert len(advanced.content.challenge_archetypes) == 3
    assert not full.content.challenge_archetypes

    expected_equipment = tuple(
        replace(template, skills=()) if template.equipment_id == "t1_prayer_beads" else template
        for template in full.content.equipment_templates
    )
    assert advanced.content.equipment_templates[:len(expected_equipment)] == expected_equipment
    added_equipment = advanced.content.equipment_templates[len(expected_equipment):]
    assert len(added_equipment) == 10
    assert all(t.slot == "ultimate" for t in added_equipment)

    full_skills = {
        skill.skill_id: skill
        for template in full.content.recruitable_adventurers
        for skill in (*template.skills, *(s for unlock in template.level_skill_unlocks for s in unlock.skills))
    }
    # 仅放行已明确列出的开发版职业改动，解锁等级、成长及其他技能不变。
    overrides = {
        "minor_heal", "soothing_light", "holy_strike", "divine_guard",
        "necrotic_blast", "withering_touch", "epidemic", "shadow_form",
    }
    normalized = []
    for template in advanced.content.recruitable_adventurers:
        unlocks = tuple(replace(unlock, skills=tuple(
            full_skills[skill.skill_id] if skill.skill_id in overrides else skill
            for skill in unlock.skills
        )) for unlock in template.level_skill_unlocks)
        base_skills = tuple(
            full_skills[skill.skill_id] if skill.skill_id in overrides else skill
            for skill in template.skills
        )
        normalized.append(replace(template, skills=base_skills, level_skill_unlocks=unlocks))
    assert tuple(normalized) == full.content.recruitable_adventurers


def test_full_preset_keeps_original_data_hash():
    from guild_manager_bench.game.presets import resolve_data_preset
    assert resolve_data_preset("data", "full").data_hash == "08abe457e84235f4ff9433c6de7aa95153bc250d738f8a98097402789816dd64"
    assert resolve_data_preset("data", "advanced").data_hash != resolve_data_preset("data", "full").data_hash


def test_restoring_observation_does_not_apply_economic_bonus_twice(economy_definition):
    from guild_manager_bench.runtime.session import GameSession
    d = economy_definition
    state = replace(new_game(d), experience_pool=200)
    for uid in ("craft", "teach"):
        state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id=uid))
    observation = build_observation(d, state)
    restored = GameSession.from_export(d, {"final_observation": observation})
    current = restored.observation()
    assert current["monsters"] == observation["monsters"]
    assert current["crafting_recipes"] == observation["crafting_recipes"]
    assert current["experience_pool"] == 25
    result, _ = restored.end_turn(EndTurnAction(hunts=(HuntAction(adventurer_id="hero", monster_id=current["monsters"][0]["monster_id"]),)))
    assert result.state.experience_pool == 95


def test_failed_session_purchase_is_atomic(economy_definition):
    from guild_manager_bench.runtime.session import GameSession
    d = economy_definition
    session = GameSession(d, state=replace(new_game(d), experience_pool=74))
    before = session.observation()
    events = len(session.events)
    with pytest.raises(UpgradeError):
        session.apply_preparation(PurchaseUpgradeAction(upgrade_id="craft"))
    assert session.observation() == before
    assert len(session.events) == events


def test_shadow_purchase_and_discount_match_engine_in_one_turn(economy_definition):
    from guild_manager_bench.bench.operators.shadow import ShadowState
    d = economy_definition
    state = replace(new_game(d), experience_pool=200)
    observation = build_observation(d, state)
    shadow = ShadowState.from_observation(observation)
    upgrade = observation["global_upgrades"][0]
    recipe = observation["crafting_recipes"][0]
    assert shadow.can_purchase_upgrade(upgrade)
    shadow.apply_upgrade(upgrade)
    shadow.apply_craft(recipe, shadow.next_craft_instance_id())
    state = apply_preparation_action(d, state, PurchaseUpgradeAction(upgrade_id="craft"))
    state = apply_preparation_action(d, state, CraftAction(recipe_id="sword"))
    assert (shadow.gold, shadow.experience_pool, shadow.materials) == (state.gold, state.experience_pool, dict(state.materials))
    assert shadow.clone().craft_gold_cost(recipe) == 9
    assert observation["gold"] == 150


def test_llm_query_exposes_complete_tree_and_keeps_legacy_tool_contract():
    from guild_manager_bench.bench.llm.tools import GuildManagerTools, tool_schemas
    from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
    from guild_manager_bench.bench.llm.prompts import build_system_prompt
    advanced = GuildManagerTools(load_game_definition("data/presets/advanced"))
    sid = advanced.start_session()["session_id"]
    result = advanced.get_upgrades(sid)
    text = _format_tool_result_for_model("get_upgrades", result)
    for part in ("经验池", "材料", "互斥", "工艺标准化", "15%", "战争学院", "秘法结社"):
        assert part in text
    assert len(result["global_upgrades"]) == 35
    craft = next(u for u in result["global_upgrades"] if u["upgrade_id"] == "craft_standardization")
    assert (craft["gold_cost"], craft["experience_cost"], craft["material_costs"], craft["craft_gold_discount_percent"]) == (150, 50, {"iron_ore": 4}, 15)
    legacy = GuildManagerTools(load_game_definition("data/presets/full"))
    assert legacy.list_tool_schemas() == tool_schemas(expose_battle_preview=True)
    legacy_result = legacy.get_upgrades(legacy.start_session()["session_id"])
    assert set(legacy_result) == {"session_id", "gold", "global_upgrades"}
    assert "全局天赋" not in build_system_prompt()
    assert "全局天赋" in build_system_prompt(talent_tree_enabled=True)


@pytest.fixture(scope="module")
def talent_skills():
    d = load_game_definition("data/presets/advanced")
    return {skill.skill_id: skill for u in d.content.global_upgrades for skill in u.skills}


def test_assault_and_endurance_use_global_action_boundaries(talent_skills):
    from guild_manager_bench.game.combat import Combatant, run_auto_battle
    actor = CombatStats(hp=5000, mp=20, attack=100, defense=100, speed=10, recovery=0)
    foe = CombatStats(hp=5000, mp=0, attack=150, defense=0, speed=10, recovery=0)
    result = run_auto_battle(Combatant("hero", actor, skills=(talent_skills["talent_assault"], talent_skills["talent_endurance"])), Combatant("foe", foe), max_actions=12)
    events = {e.action_index: e for e in result.events}
    assert events[5].damage == 112
    assert events[7].damage == 100
    assert events[8].damage == 50
    # int(100 * 1.15) uses the engine's existing float rounding.
    assert events[10].damage < events[8].damage


@pytest.mark.parametrize("skill_id,mp,target_hp,expected_damage", [
    ("talent_execution", 20, 300, 118),
    ("talent_execution", 20, 301, 100),
    ("talent_mana_abundance", 14, 1000, 110),
    ("talent_mana_abundance", 13, 1000, 100),
])
def test_conditional_attack_talents_trigger_at_threshold(talent_skills, skill_id, mp, target_hp, expected_damage):
    from guild_manager_bench.game.combat import Combatant, run_auto_battle
    actor = CombatStats(hp=1000, mp=20, attack=100, defense=0, speed=10, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(Combatant("hero", actor, CombatResources(1000, mp), (talent_skills[skill_id],)), Combatant("foe", foe, CombatResources(target_hp, 0)), max_actions=1)
    assert result.events[0].damage == expected_damage


@pytest.mark.parametrize("skill_id,hp,mp,triggered", [
    ("talent_emergency_care", 30, 20, True),
    ("talent_emergency_care", 31, 20, False),
    ("talent_mana_reflux", 100, 5, True),
    ("talent_mana_reflux", 100, 6, False),
])
def test_automatic_talents_trigger_once_and_keep_basic_attack(talent_skills, skill_id, hp, mp, triggered):
    from guild_manager_bench.game.combat import Combatant, run_auto_battle
    actor = CombatStats(hp=100, mp=20, attack=5, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    left = Combatant("hero", actor, CombatResources(hp, mp), (talent_skills[skill_id],))
    result = run_auto_battle(left, Combatant("foe", foe), max_actions=3)
    assert sum(e.skill_id == skill_id for e in result.events) == int(triggered)
    assert all(e.damage == 0 for e in result.events if e.skill_id == skill_id)
    assert [e.damage for e in result.events if e.action_type == "basic_attack"] == [5, 5, 5]
    if triggered and skill_id == "talent_emergency_care":
        assert result.left_resources.current_hp == 55
    if triggered and skill_id == "talent_mana_reflux":
        assert result.left_resources.current_mp == 17
    # 使用次数只属于单场战斗，下一场可以重新触发。
    assert run_auto_battle(left, Combatant("foe", foe), max_actions=1).events[0].skill_id == (skill_id if triggered else None)
