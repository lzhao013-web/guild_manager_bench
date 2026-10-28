import json
from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm.formatting import skill_summary
from guild_manager_bench.bench.metrics import compute_rank_score, rank_score_from_final_observation
from guild_manager_bench.game.actions import ConfigureTacticsAction, CraftAction, EndTurnAction, EquipAction, HuntAction
from guild_manager_bench.game.engine import effective_adventurer_stats, new_game, preview_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.state import AdventurerState, RewardBundle, SpawnedMonster
from guild_manager_bench.runtime.session import GameSession


@pytest.mark.parametrize("class_id,seal,core,priority,disabled", [
    ("cleric", "cleric", "renewal_core", ["ultimate_cleric", "holy_strike", "soothing_light"], ["minor_heal"]),
    ("plague_mage", "plague", "renewal_core", ["withering_touch", "epidemic", "ultimate_plague", "plague_curse"], ["necrotic_blast"]),
    ("spellshot_mage", "spellshot", "siege_core", ["ultimate_spellshot", "arcane_missile"], ["arcane_barrage", "mana_dump"]),
    ("cannoneer", "cannoneer", "siege_core", ["siege_cannon", "ultimate_cannoneer", "armor_piercing_shot"], ["shrapnel_round"]),
    ("foot_knight", "knight", "aegis_core", ["ultimate_knight", "shield_bash"], []),
    ("bloodfiend", "bloodfiend", "renewal_core", ["ultimate_bloodfiend", "crimson_frenzy", "vampiric_embrace"], ["blood_drain"]),
    ("jester", "jester", "aegis_core", ["ultimate_jester", "combo_finisher", "combo_strike"], []),
])
def test_configured_build_survives_export_and_uses_identical_preview_combat_and_rank(
    class_id, seal, core, priority, disabled,
):
    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    template = next(t for t in definition.content.recruitable_adventurers if t.template_id == class_id)
    hero = AdventurerState(
        "hero", template.name, template.base_stats, CombatResources.full(template.base_stats),
        skills=template.skills, level_skill_unlocks=template.level_skill_unlocks,
        stat_growth_per_level=template.stat_growth_per_level, template_id=class_id, level=12,
    )
    # Controlled integration fixture, not an earned full-game build or a balance experiment.
    definition = replace(
        definition, content=replace(definition.content, adventurers=(hero,)),
        starting_materials={core: 1},
        scoring=replace(definition.scoring, rank_max_diff=11, rank_step=10, rank_waves=2),
    )
    state = new_game(definition)
    stats = effective_adventurer_stats(definition, state, hero)
    hero = replace(hero, resources=CombatResources(stats.hp * 3 // 5, stats.mp))
    target = SpawnedMonster(
        "target", "controlled_target", "测试目标", CombatStats(2000, 0, 1, 10, 1, 0), RewardBundle(),
    )
    state = replace(state, turn=31, adventurers=(hero,), current_monsters=(target,))
    session = GameSession(definition, session_id="build-contract", state=state)
    session.apply_preparation(CraftAction(recipe_id=f"recipe_seal_{seal}"))
    session.apply_preparation(EquipAction(adventurer_id="hero", equipment_instance_id="eq_0001"))
    session.apply_preparation(ConfigureTacticsAction(
        adventurer_id="hero", skill_priority=tuple(priority), disabled_skills=tuple(disabled),
    ))
    before = session.state
    expected = preview_battle(definition, before, adventurer_id="hero", monster_id="target")
    assert any(e.skill_id == f"ultimate_{seal}" for e in expected.combat_result.events)
    assert session.preview_battle("hero", "target")["adventurer_after_resources"] == {
        "current_hp": expected.combat_result.left_resources.current_hp,
        "current_mp": expected.combat_result.left_resources.current_mp,
    }
    assert session.state is before

    raw = json.loads(json.dumps(session.export_replay(), ensure_ascii=False))
    # The observation scorer runs without recording combat events. It must still use the same skills.
    rank = compute_rank_score(definition, before)
    assert raw["score"]["rank_score"] == rank
    assert rank_score_from_final_observation(definition, raw["final_observation"]) == rank
    for use_internal_state in (True, False):
        exported = dict(raw)
        if not use_internal_state:
            exported.pop("_state")
        restored = GameSession.from_export(definition, exported)
        assert restored.observation() == session.observation()
        assert compute_rank_score(definition, restored.state) == rank
        result, _ = restored.end_turn(EndTurnAction(hunts=(HuntAction("hero", "target"),)))
        assert result.battles[0].combat_result == expected.combat_result


def test_player_visible_skill_details_include_compound_conditions_and_mana_minimum():
    from guild_manager_bench.bench.llm.prompts import build_system_prompt
    from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
    from guild_manager_bench.bench.llm.tools import GuildManagerTools

    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    tools = GuildManagerTools(definition)
    sid = tools.start_session("visible-text")["session_id"]
    observation = tools.get_observation(sid)["observation"]
    recipe = next(r for r in observation["crafting_recipes"] if r["output_template_id"] == "seal_cleric")
    text = skill_summary(recipe["output_skills"])

    assert "双方累计行动序号至少为5" in text
    assert "满足任一条件" in text
    assert "自身HP不高于70%" in text and "自身MP不高于55%" in text
    assert "恢复40%最大MP，至少18MP" in text
    assert "持续状态持有者的6次行动" in text
    assert "重复施加" not in text
    assert "状态持有者攻击×1.45，防御×1.25" in text

    upgrades_text = _format_tool_result_for_model("get_upgrades", tools.get_upgrades(sid))
    assert "前置要求:" in upgrades_text and "前置全部满足:" not in upgrades_text
    assert "HP:0" not in upgrades_text and "队伍上限+0" not in upgrades_text
    assert upgrades_text.count("15%") == 1

    system_text = build_system_prompt(
        tactics_enabled=True,
        challenge_rules={"start_turn": 30, "last_hunt_turn": 34, "unique_challenge_classes": True},
    )
    assert "穿戴新装备或印记不会自动将其技能置顶" in system_text
    assert "终极印记：" in system_text and "最终整备：" in system_text
