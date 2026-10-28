from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.game.actions import PurchaseUpgradeAction, RecruitAction
from guild_manager_bench.game.engine import apply_preparation_action, new_game
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.observation import build_observation
from guild_manager_bench.runtime.session import GameSession


DATA = Path(__file__).resolve().parents[1] / "data/presets"


def recruitment_state(preset):
    definition = load_game_definition(DATA / preset)
    state = new_game(definition)
    if preset == "advanced":
        state = replace(state, gold=1000, experience_pool=2000, materials={
            "iron_ore": 20, "bone": 20, "herb": 20,
            "sharp_claw": 20, "beast_hide": 20, "arcane_dust": 20,
        })
        for upgrade_id in (
            "training_grounds", "blacksmith", "scout_network", "battle_coordination",
            "herb_garden", "emergency_care",
        ):
            state = apply_preparation_action(definition, state, PurchaseUpgradeAction(upgrade_id))
    return definition, state


@pytest.mark.parametrize("preset", ["default", "advanced"])
def test_candidate_panel_matches_actual_recruitment(preset):
    definition, state = recruitment_state(preset)
    observation = build_observation(definition, state)
    for candidate in observation["recruit_candidates"]:
        recruited = apply_preparation_action(definition, state, RecruitAction(candidate["candidate_id"]))
        member = build_observation(definition, recruited)["adventurers"][-1]
        assert member["level"] == 1
        assert candidate["base_stats"] == member["base_stats"]
        assert candidate["effective_stats"] == member["effective_stats"]
        assert member["resources"] == {
            "current_hp": candidate["effective_stats"]["hp"],
            "current_mp": candidate["effective_stats"]["mp"],
        }
        # 条件被动保留为战斗效果，只将直接加成计入面板。
        expected_attack_bonus = 9 if preset == "advanced" else 0
        assert candidate["effective_stats"]["attack"] == candidate["base_stats"]["attack"] + expected_attack_bonus
        text = _format_tool_result_for_model("get_recruitment", {"recruit_candidates": [candidate]})
        assert "Lv1面板，已计入公会直接加成" in text
        assert f"攻击 {member['effective_stats']['attack']} " in text


def test_unaffordable_candidate_still_has_accurate_panel():
    definition, state = recruitment_state("advanced")
    before = build_observation(definition, state)["recruit_candidates"]
    candidates = build_observation(definition, replace(state, gold=0))["recruit_candidates"]
    assert all(not candidate["can_recruit"] for candidate in candidates)
    assert [c["effective_stats"] for c in candidates] == [c["effective_stats"] for c in before]
    assert state.gold > 0


def test_observation_restore_keeps_candidate_base_and_effective_stats_distinct():
    definition, state = recruitment_state("advanced")
    session = GameSession(definition, state=state)
    exported = session.export_replay()
    # 旧归档通过观测恢复，新增展示字段应保留基础属性语义。
    exported.pop("_state")
    restored = GameSession.from_export(definition, exported)
    assert restored.observation()["recruit_candidates"] == session.observation()["recruit_candidates"]
