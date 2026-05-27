from dataclasses import replace

import pytest

from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    EndTurnAction,
    EquipAction,
    HuntAction,
    PurchaseUpgradeAction,
    RecruitAction,
    TurnAction,
    UnequipAction,
)
from guild_manager_bench.game.crafting import CraftingRecipe
from guild_manager_bench.game.engine import (
    GameError,
    apply_preparation_action,
    apply_turn,
    end_turn,
    effective_adventurer_skills,
    effective_adventurer_stats,
    new_game,
    party_size_limit,
    spawn_recruit_candidates,
    spawn_monsters,
)
from guild_manager_bench.game.equipment import EquippedItem, EquipmentInstance, EquipmentLoadout, EquipmentTemplate
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.progression import ExperienceRules
from guild_manager_bench.game.state import (
    AdventurerState,
    GameContent,
    GameDefinition,
    GameRules,
    IntCurve,
    LevelSkillUnlock,
    MonsterArchetype,
    MonsterSpawnRules,
    RecruitableAdventurerTemplate,
    RecruitmentRules,
    RewardBundle,
    TurnRecoveryRules,
)
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.game.upgrades import GlobalUpgrade


def test_new_game_spawns_first_turn_monsters() -> None:
    definition = _definition()

    state = new_game(definition)

    assert state.turn == 1
    assert state.gold == 20
    assert dict(state.materials) == {"iron_ore": 1}
    assert len(state.current_monsters) == 2
    assert state.current_monsters[0].stats.hp == 20
    assert state.recruit_candidates == ()


def test_spawn_monsters_uses_count_and_growth_curves() -> None:
    definition = _definition()

    monsters = spawn_monsters(definition, 2)

    assert len(monsters) == 3
    assert monsters[0].stats.hp == 30
    assert monsters[0].stats.attack == 2
    assert monsters[0].reward.gold == 6
    assert monsters[0].reward.experience == 70
    assert dict(monsters[0].reward.materials) == {"slime_gel": 2}


def test_apply_turn_runs_preparation_battle_rewards_and_next_turn_spawn() -> None:
    definition = _definition()
    state = replace(new_game(definition), experience_pool=50)
    monster_id = state.current_monsters[0].monster_id

    result = apply_turn(
        definition,
        state,
        TurnAction(
            operations=(
                CraftAction(recipe_id="iron_sword_recipe"),
                PurchaseUpgradeAction(upgrade_id="weapon_training"),
                AllocateExperienceAction(adventurer_id="a1", amount=50),
                EquipAction(adventurer_id="a1", equipment_instance_id="eq_0001"),
            ),
            hunts=(HuntAction(adventurer_id="a1", monster_id=monster_id),),
        ),
    )

    next_state = result.state
    adventurer = next_state.adventurers[0]

    assert result.crafted_equipment_ids == ("eq_0001",)
    assert result.purchased_upgrade_ids == ("weapon_training",)
    assert result.battles[0].won
    assert result.battles[0].reward.gold == 5
    assert next_state.turn == 2
    assert len(next_state.current_monsters) == 3
    assert next_state.gold == 10
    assert dict(next_state.materials) == {"iron_ore": 0, "slime_gel": 1}
    assert next_state.experience_pool == 60
    assert adventurer.level == 2
    assert adventurer.resources.current_hp == 110
    assert adventurer.equipment.equipped_instance_ids() == ("eq_0001",)
    assert next_state.unlocked_upgrade_ids == frozenset({"weapon_training"})


def test_apply_turn_rejects_duplicate_adventurer_hunts() -> None:
    definition = _definition()
    state = new_game(definition)

    with pytest.raises(GameError):
        apply_turn(
            definition,
            state,
            TurnAction(
                hunts=(
                    HuntAction(adventurer_id="a1", monster_id=state.current_monsters[0].monster_id),
                    HuntAction(adventurer_id="a1", monster_id=state.current_monsters[1].monster_id),
                ),
            ),
        )


def test_apply_turn_rejects_unknown_equipment_instance() -> None:
    definition = _definition()
    state = new_game(definition)

    with pytest.raises(GameError):
        apply_turn(
            definition,
            state,
            TurnAction(
                operations=(EquipAction(adventurer_id="a1", equipment_instance_id="missing"),)
            ),
        )


def test_apply_turn_recovers_adventurers_between_turns() -> None:
    definition = _definition()
    definition = replace(
        definition,
        rules=replace(
            definition.rules,
            turn_recovery=TurnRecoveryRules(
                hp=5,
                mp=3,
                hp_percent=0.1,
                mp_percent=0.2,
                use_recovery_stat=False,
            ),
        ),
    )
    adventurer = replace(
        definition.content.adventurers[0],
        resources=CombatResources(current_hp=40, current_mp=2),
    )
    state = replace(new_game(definition), adventurers=(adventurer,))

    result = apply_turn(definition, state, TurnAction())

    recovered = result.state.adventurers[0]
    assert recovered.resources.current_hp == 55
    assert recovered.resources.current_mp == 7


def test_apply_turn_unequips_slot() -> None:
    definition = _definition()
    equipped_adventurer = replace(
        definition.content.adventurers[0],
        equipment=EquipmentLoadout(
            items=(EquippedItem(slot="main_hand", instance_id="eq_0001"),)
        ),
    )
    state = replace(
        new_game(definition),
        adventurers=(equipped_adventurer,),
        equipment_inventory=(EquipmentInstance(instance_id="eq_0001", template_id="iron_sword"),),
    )

    result = apply_turn(
        definition,
        state,
        TurnAction(operations=(UnequipAction(adventurer_id="a1", slot="main_hand"),)),
    )

    assert result.state.adventurers[0].equipment.equipped_instance_ids() == ()


def test_apply_turn_rejects_unequip_empty_slot() -> None:
    definition = _definition()
    state = new_game(definition)

    with pytest.raises(GameError):
        apply_turn(
            definition,
            state,
            TurnAction(operations=(UnequipAction(adventurer_id="a1", slot="main_hand"),)),
        )


def test_preparation_actions_can_be_applied_before_ending_turn() -> None:
    definition = _definition()
    state = new_game(definition)

    state = apply_preparation_action(
        definition,
        state,
        CraftAction(recipe_id="iron_sword_recipe"),
    )
    state = apply_preparation_action(
        definition,
        state,
        EquipAction(adventurer_id="a1", equipment_instance_id="eq_0001"),
    )
    assert state.turn == 1
    assert state.adventurers[0].equipment.equipped_instance_ids() == ("eq_0001",)

    result = end_turn(
        definition,
        state,
        EndTurnAction(hunts=(HuntAction(adventurer_id="a1", monster_id=state.current_monsters[0].monster_id),)),
    )

    assert result.state.turn == 2
    assert result.battles[0].won


def test_adventurer_specific_growth_overrides_global_growth() -> None:
    definition = _definition()
    adventurer = replace(
        definition.content.adventurers[0],
        stat_growth_per_level=CombatStatModifier(hp=4, attack=20),
    )
    definition = replace(
        definition,
        content=replace(definition.content, adventurers=(adventurer,)),
    )
    state = replace(new_game(definition), experience_pool=50)

    state = apply_preparation_action(
        definition,
        state,
        AllocateExperienceAction(adventurer_id="a1", amount=50),
    )

    updated = state.adventurers[0]
    stats = effective_adventurer_stats(definition, state, updated)
    assert updated.level == 2
    assert stats.hp == 104
    assert stats.attack == 30


def test_adventurer_unlocks_level_skills_after_level_up() -> None:
    level_skill = Skill(
        skill_id="guard_break",
        name="破防训练",
        kind="passive",
        condition=SkillCondition(condition_type="always"),
        effects=(
            SkillEffect(
                effect_type="stat_bonus",
                stat="attack",
                value=3,
                target="self",
            ),
        ),
    )
    definition = _definition()
    adventurer = replace(
        definition.content.adventurers[0],
        level_skill_unlocks=(
            LevelSkillUnlock(level=2, skills=(level_skill,)),
        ),
    )
    definition = replace(
        definition,
        content=replace(definition.content, adventurers=(adventurer,)),
    )
    state = new_game(definition)

    assert [
        skill.skill_id
        for skill in effective_adventurer_skills(definition, state, state.adventurers[0])
    ] == []

    state = replace(state, experience_pool=50)
    state = apply_preparation_action(
        definition,
        state,
        AllocateExperienceAction(adventurer_id="a1", amount=50),
    )

    assert [
        skill.skill_id
        for skill in effective_adventurer_skills(definition, state, state.adventurers[0])
    ] == ["guard_break"]


def test_spawn_recruit_candidates_is_deterministic_and_varies_template_values() -> None:
    definition = _definition_with_recruitment()

    first = spawn_recruit_candidates(definition, 1)
    second = spawn_recruit_candidates(definition, 1)

    assert first == second
    assert len(first) == 4
    assert first[0].candidate_id == "turn_1_recruit_1"
    assert first[0].template_id in {"guard", "scout"}
    assert len(spawn_recruit_candidates(definition, 2)) == 2
    assert any(
        candidate.recruit_gold != _template_by_id(definition, candidate.template_id).recruit_gold
        or candidate.base_stats != _template_by_id(definition, candidate.template_id).base_stats
        or candidate.stat_growth_per_level
        != _template_by_id(definition, candidate.template_id).stat_growth_per_level
        for candidate in first
    )


def test_recruit_action_deducts_gold_adds_adventurer_and_removes_candidate() -> None:
    definition = _definition_with_recruitment()
    state = replace(new_game(definition), unlocked_upgrade_ids=frozenset({"recruitment_board"}))
    candidate = state.recruit_candidates[0]

    state = apply_preparation_action(
        definition,
        state,
        RecruitAction(candidate_id=candidate.candidate_id),
    )

    recruited = state.adventurers[-1]
    assert state.gold == 200 - candidate.recruit_gold
    assert recruited.adventurer_id == "recruit_0001"
    assert recruited.name == candidate.name
    assert recruited.base_stats == candidate.base_stats
    assert recruited.stat_growth_per_level == candidate.stat_growth_per_level
    assert recruited.resources == CombatResources.full(effective_adventurer_stats(definition, state, recruited))
    assert candidate.candidate_id not in {item.candidate_id for item in state.recruit_candidates}
    assert state.next_adventurer_number == 2


def test_recruit_action_rejects_full_party_until_party_size_upgrade() -> None:
    definition = _definition_with_recruitment()
    state = new_game(definition)
    assert party_size_limit(definition, state) == 1

    with pytest.raises(GameError, match="party size limit"):
        apply_preparation_action(
            definition,
            state,
            RecruitAction(candidate_id=state.recruit_candidates[0].candidate_id),
        )

    state = apply_preparation_action(
        definition,
        state,
        PurchaseUpgradeAction(upgrade_id="recruitment_board"),
    )
    assert party_size_limit(definition, state) == 2

    state = apply_preparation_action(
        definition,
        state,
        RecruitAction(candidate_id=state.recruit_candidates[0].candidate_id),
    )

    assert len(state.adventurers) == 2


def test_apply_turn_reports_recruited_adventurers() -> None:
    definition = _definition_with_recruitment()
    state = replace(new_game(definition), unlocked_upgrade_ids=frozenset({"recruitment_board"}))

    result = apply_turn(
        definition,
        state,
        TurnAction(
            operations=(
                RecruitAction(candidate_id=state.recruit_candidates[0].candidate_id),
            )
        ),
    )

    assert result.recruited_adventurer_ids == ("recruit_0001",)
    assert result.state.turn == 2
    assert len(result.state.recruit_candidates) == 2


def _definition() -> GameDefinition:
    adventurer_stats = CombatStats(hp=100, mp=10, attack=10, defense=1, speed=10, recovery=0)
    return GameDefinition(
        content=GameContent(
            adventurers=(
                AdventurerState(
                    adventurer_id="a1",
                    name="先锋",
                    base_stats=adventurer_stats,
                    resources=CombatResources.full(adventurer_stats),
                ),
            ),
            monster_archetypes=(
                MonsterArchetype(
                    archetype_id="slime",
                    name="史莱姆",
                    base_stats=CombatStats(hp=20, mp=0, attack=1, defense=0, speed=1, recovery=0),
                    base_reward=RewardBundle(
                        gold=5,
                        experience=60,
                        materials={"slime_gel": 1},
                    ),
                    stat_growth=CombatStatModifier(hp=10, attack=1),
                    reward_growth=RewardBundle(
                        gold=1,
                        experience=10,
                        materials={"slime_gel": 1},
                    ),
                ),
            ),
            equipment_templates=(
                EquipmentTemplate(
                    equipment_id="iron_sword",
                    name="铁剑",
                    slot="main_hand",
                    stat_modifier=CombatStatModifier(attack=20),
                ),
            ),
            crafting_recipes=(
                CraftingRecipe.from_mapping(
                    recipe_id="iron_sword_recipe",
                    name="铁剑配方",
                    output_template_id="iron_sword",
                    material_costs={"iron_ore": 1},
                    gold_cost=5,
                ),
            ),
            global_upgrades=(
                GlobalUpgrade(
                    upgrade_id="weapon_training",
                    name="武器训练",
                    gold_cost=10,
                    stat_modifier=CombatStatModifier(attack=5),
                ),
            ),
            experience_rules=ExperienceRules(
                base_required_experience=50,
                required_experience_growth=0,
                stat_growth_per_level=CombatStatModifier(hp=10, attack=10),
            ),
        ),
        rules=GameRules(
            max_turns=2,
            seed=1,
            monster_spawn=MonsterSpawnRules(
                count_curve=IntCurve(base=2, per_turn=1),
            ),
        ),
        starting_gold=20,
        starting_materials={"iron_ore": 1},
    )


def _definition_with_recruitment() -> GameDefinition:
    definition = _definition()
    return replace(
        definition,
        starting_gold=200,
        content=replace(
            definition.content,
            recruitable_adventurers=(
                RecruitableAdventurerTemplate(
                    template_id="guard",
                    name="卫士",
                    recruit_gold=40,
                    base_stats=CombatStats(hp=80, mp=8, attack=8, defense=6, speed=6, recovery=2),
                    stat_growth_per_level=CombatStatModifier(hp=8, attack=2, defense=3),
                ),
                RecruitableAdventurerTemplate(
                    template_id="scout",
                    name="斥候",
                    recruit_gold=35,
                    base_stats=CombatStats(hp=60, mp=12, attack=12, defense=3, speed=12, recovery=1),
                    stat_growth_per_level=CombatStatModifier(hp=5, attack=3, speed=3),
                ),
            ),
            global_upgrades=(
                definition.content.global_upgrades[0],
                GlobalUpgrade(
                    upgrade_id="recruitment_board",
                    name="招募栏",
                    gold_cost=10,
                    party_size_bonus=1,
                ),
            ),
        ),
        rules=replace(
            definition.rules,
            recruitment=RecruitmentRules(
                candidate_count=2,
                first_turn_candidate_count=4,
                initial_party_size_limit=1,
                maximum_party_size_limit=2,
            ),
        ),
    )


def _template_by_id(
    definition: GameDefinition,
    template_id: str,
) -> RecruitableAdventurerTemplate:
    for template in definition.content.recruitable_adventurers:
        if template.template_id == template_id:
            return template
    raise AssertionError(f"unknown template {template_id}")
