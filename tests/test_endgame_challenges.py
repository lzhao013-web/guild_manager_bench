from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from guild_manager_bench.bench.llm.forecast import query_forecast
from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import build_system_prompt, build_turn_prompt
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.metrics import compute_rank_score
from guild_manager_bench.game.actions import EndTurnAction, HuntAction
from guild_manager_bench.game.engine import end_turn, new_game, spawn_monsters, spawn_recruit_candidates
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.state import AdventurerState
from guild_manager_bench.runtime.observation import build_observation
from guild_manager_bench.runtime.session import GameSession, _restore_from_observation, _serialize_state, _deserialize_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def definition():
    return load_game_definition(ROOT / "data/presets/advanced")


@pytest.fixture
def game(definition):
    # 规则测试使用可击败的挑战配置，职业强度另由离线真实引擎矩阵验证。
    challenges = tuple(replace(c, base_stats=CombatStats(30, 0, 1, 0, 1, 0), skills=())
                       for c in definition.content.challenge_archetypes)
    cleric = next(t for t in definition.content.recruitable_adventurers if t.template_id == "cleric")
    heroes = tuple(AdventurerState(
        f"hero_{i}", f"神官{i}", cleric.base_stats, CombatResources.full(cleric.base_stats),
        skills=cleric.skills, level_skill_unlocks=cleric.level_skill_unlocks,
        stat_growth_per_level=cleric.stat_growth_per_level, template_id=cleric.template_id,
    ) for i in range(1, 4))
    definition = replace(definition, content=replace(definition.content,
        adventurers=heroes, challenge_archetypes=challenges),
        scoring=replace(definition.scoring, rank_max_diff=11, rank_step=10, rank_waves=2))
    tools = GuildManagerTools(definition)
    sid = tools.start_session("challenges")["session_id"]
    tools._sessions[sid].state = replace(tools.get_state(sid), turn=30,
        current_monsters=spawn_monsters(definition, 30),
        recruit_candidates=spawn_recruit_candidates(definition, 30))
    return tools, sid


def challenge_ids(state):
    return {m.archetype_id for m in state.current_monsters if m.tier == "challenge"}


def kill(tools, sid, challenge="doom_oracle", hero="hero_1"):
    result = tools.end_turn(sid, hunts=[{"adventurer_id": hero, "monster_id": f"challenge_{challenge}"}])
    assert result["ok"], result
    assert result["turn_result"]["battles"][0]["won"]
    return result


@pytest.mark.parametrize("seed", [0, 42, 1234])
def test_fixed_challenges_do_not_change_normal_refresh_or_recruits(definition, seed):
    d = replace(definition, rules=replace(definition.rules, seed=seed))
    baseline = replace(d, content=replace(d.content, challenge_archetypes=()))
    for turn in (1, 18, 29, 30, 31, 34):
        monsters = spawn_monsters(d, turn)
        assert tuple(m for m in monsters if m.tier != "challenge") == spawn_monsters(baseline, turn)
        assert spawn_recruit_candidates(d, turn) == spawn_recruit_candidates(baseline, turn)
    assert not any(m.tier == "challenge" for m in spawn_monsters(d, 29))
    c30 = tuple(m for m in spawn_monsters(d, 30) if m.tier == "challenge")
    assert len(c30) == 3
    assert all(m.name.startswith("【挑战】") for m in c30)
    assert c30 == tuple(m for m in spawn_monsters(d, 34) if m.tier == "challenge")
    assert spawn_monsters(d, 35) == ()
    assert d.content.experience_rules.max_level == 12


def test_transition_spawns_challenges_and_unattempted_targets_persist(game):
    tools, sid = game
    tools._sessions[sid].state = replace(tools.get_state(sid), turn=29,
        current_monsters=spawn_monsters(tools.definition, 29))
    assert tools.end_turn(sid, hunts=[])["ok"]
    before = tuple(m for m in tools.get_state(sid).current_monsters if m.tier == "challenge")
    assert len(before) == 3
    assert tools.end_turn(sid, hunts=[])["ok"]
    assert tuple(m for m in tools.get_state(sid).current_monsters if m.tier == "challenge") == before


def test_winning_preview_never_clears_target_or_grants_rewards(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=20)
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    monster_ref = harness._turn_refs["monster"]["challenge_doom_oracle"]
    result = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": monster_ref})
    assert result["ok"] and result["preview"]["won"]
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert not tools.get_state(sid).used_challenge_class_ids
    log = harness.call_tool("get_battle_log", {"battle_id": result["preview"]["battle_id"]})
    assert log["battle"]["monster_name"].startswith("【挑战】")
    assert log["tool_budget"]["battle_preview_used"] == 1


@pytest.mark.parametrize("challenge,core", [
    ("iron_colossus", "siege_core"),
    ("blade_tyrant", "aegis_core"),
    ("doom_oracle", "renewal_core"),
])
def test_first_clear_grants_only_one_core_and_stays_cleared_across_turns(game, challenge, core):
    tools, sid = game
    before = tools.get_state(sid)
    result = kill(tools, sid, challenge)
    state = tools.get_state(sid)
    assert state.defeated_challenge_ids == {challenge}
    assert state.used_challenge_class_ids == {"cleric"}
    assert state.gold == before.gold
    assert state.experience_pool == before.experience_pool
    assert state.materials[core] == 1
    assert challenge_ids(state) == challenge_ids(before) - {challenge}
    battle = result["turn_result"]["battles"][0]
    assert battle["reward"] == {"gold": 0, "experience": 0, "materials": {core: 1}}
    actual = tools.get_battle_log(sid, battle["battle_id"], "events")
    assert actual["battle"]["outcome"] == "left_win"
    assert not tools.end_turn(sid, hunts=[{"adventurer_id": "hero_1", "monster_id": f"challenge_{challenge}"}])["ok"]
    assert tools.get_state(sid) is state
    assert tools.end_turn(sid, hunts=[])["ok"]
    assert challenge not in challenge_ids(tools.get_state(sid))
    assert tools.get_state(sid).materials[core] == 1


def test_multiple_first_clears_and_duplicate_target_atomicity(game):
    tools, sid = game
    templates = {t.template_id: t for t in tools.definition.content.recruitable_adventurers}
    heroes = tuple(AdventurerState(
        f"hero_{i}", t.name, t.base_stats, CombatResources.full(t.base_stats),
        skills=t.skills, level_skill_unlocks=t.level_skill_unlocks,
        stat_growth_per_level=t.stat_growth_per_level, template_id=t.template_id,
    ) for i, t in enumerate((templates[c] for c in ("cleric", "woodland_archer", "mercenary_warrior")), 1))
    tools._sessions[sid].state = replace(tools.get_state(sid), adventurers=heroes)
    before = tools.get_state(sid)
    duplicate = tools.end_turn(sid, hunts=[
        {"adventurer_id": "hero_1", "monster_id": "challenge_doom_oracle"},
        {"adventurer_id": "hero_2", "monster_id": "challenge_doom_oracle"},
    ])
    assert not duplicate["ok"] and tools.get_state(sid) is before
    hunts = [{"adventurer_id": f"hero_{i}", "monster_id": f"challenge_{c.archetype_id}"}
             for i, c in enumerate(tools.definition.content.challenge_archetypes, 1)]
    assert tools.end_turn(sid, hunts=hunts)["ok"]
    after = tools.get_state(sid)
    assert len(after.defeated_challenge_ids) == 3
    assert after.used_challenge_class_ids == {"cleric", "woodland_archer", "mercenary_warrior"}
    cleared_prompt = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert "固定挑战已用职业" not in cleared_prompt
    assert "固定挑战已击败" not in cleared_prompt
    assert not challenge_ids(after)
    assert after.gold == before.gold
    assert after.experience_pool == before.experience_pool
    assert all(after.materials[core] == 1 for core in ("siege_core", "aegis_core", "renewal_core"))


def test_failed_challenge_restores_enemy_not_accumulated_chip_damage(game):
    tools, sid = game
    d = tools.definition
    target = replace(d.content.challenge_archetypes[0], base_stats=CombatStats(300, 0, 40, 0, 8, 0), skills=())
    d = replace(d, content=replace(d.content, challenge_archetypes=(target,)),
                rules=replace(d.rules, turn_recovery=replace(d.rules.turn_recovery, hp=10000, mp=10000)))
    tools.definition = d
    tools._sessions[sid].definition = d
    tools._sessions[sid].state = replace(tools.get_state(sid), current_monsters=spawn_monsters(d, 30))
    original = tools.preview_battle(sid, "hero_1", "challenge_iron_colossus")["preview"]
    assert not original["won"]
    assert original["monster_resources"]["after"]["current_hp"] < 300
    assert tools.end_turn(sid, hunts=[{"adventurer_id": "hero_1", "monster_id": "challenge_iron_colossus"}])["ok"]
    state = tools.get_state(sid)
    assert not state.defeated_challenge_ids and "siege_core" not in state.materials
    assert not state.used_challenge_class_ids
    repeated = tools.preview_battle(sid, "hero_1", "challenge_iron_colossus")["preview"]
    assert repeated["monster_resources"] == original["monster_resources"]
    assert repeated["events"] == original["events"]


def test_forecast_challenges_are_conditional_paginated_and_not_operable(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=20)
    refs = deepcopy(harness._turn_refs)
    response = harness.call_tool("get_forecast", {
        "kind": "monsters", "start_turn": 31, "tier": "challenge", "detail": "full", "limit": 1,
    })
    assert response["total_matches"] == 3 and response["next_offset"] == 1
    entry = response["items"][0]
    assert entry["base_reward"] == {"gold": 0, "experience": 0, "materials": {"siege_core": 1}}
    assert "此前尚未击败" in entry["availability_condition"]
    assert "仅在此前尚未击败" in _format_tool_result_for_model("get_forecast", response)
    assert harness._turn_refs == refs
    assert not harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": entry["forecast_ref"]})["ok"]
    kill(tools, sid)
    forecast = tools.get_forecast(sid, kind="monsters", start_turn=32, tier="challenge", material="轮回核心")
    assert forecast["total_matches"] == 0
    assert tools.get_forecast(sid, kind="monsters", start_turn=35)["total_matches"] == 0


def test_ultimate_plan_crafting_and_transfer_preserve_material_scarcity(game):
    tools, sid = game
    kill(tools, sid)
    harness = TurnToolHarness(tools, sid, max_tool_calls=20, endgame_start_turn=31)
    recipe = harness._turn_refs["recipe"]["recipe_seal_cleric"]
    plan = [
        {"tool": "craft_equipment", "arguments": {"recipe_id": recipe}, "as": "seal"},
        {"tool": "equip_item", "arguments": {"adventurer_id": 1, "equipment_instance_id": "$seal"}},
        {"tool": "configure_tactics", "arguments": {"adventurer_id": 1,
            "skill_priority": ["ultimate_cleric"], "disabled_skills": []}},
    ]
    before = tools.get_state(sid)
    result = harness.call_tool("preview_team_power", {"operations": plan})
    assert result["ok"] and tools.get_state(sid) is before
    assert harness.call_tool("craft_equipment", {"recipe_id": recipe})["ok"]
    assert not harness.call_tool("craft_equipment", {"recipe_id": recipe})["ok"]
    assert harness.call_tool("equip_item", {"adventurer_id": 1, "equipment_instance_id": 1})["ok"]
    assert harness.call_tool("configure_tactics", plan[-1]["arguments"])["ok"]
    state = tools.get_state(sid)
    assert round(compute_rank_score(tools.definition, state), 2) == result["projected_rank_score"]
    assert state.materials["renewal_core"] == 0
    assert len(state.equipment_inventory) == 1
    obs = tools.get_observation(sid)["observation"]
    assert any(s["skill_id"] == "ultimate_cleric" for s in obs["adventurers"][0]["skills"])
    assert harness.call_tool("equip_item", {"adventurer_id": 2, "equipment_instance_id": 1})["ok"]
    obs = tools.get_observation(sid)["observation"]
    assert not any(s["skill_id"] == "ultimate_cleric" for s in obs["adventurers"][0]["skills"])
    assert any(s["skill_id"] == "ultimate_cleric" for s in obs["adventurers"][1]["skills"])
    assert harness.call_tool("dismiss_adventurer", {"adventurer_id": 2})["ok"]
    assert len(tools.get_state(sid).equipment_inventory) == 1
    assert tools.get_state(sid).materials["renewal_core"] == 0
    assert harness.call_tool("equip_item", {"adventurer_id": 1, "equipment_instance_id": 1})["ok"]
    assert harness.call_tool("unequip_item", {"adventurer_id": 1, "slot": "ultimate"})["ok"]


def test_wrong_class_cannot_equip_ultimate_and_plan_failure_is_atomic(game):
    tools, sid = game
    kill(tools, sid, "iron_colossus")
    harness = TurnToolHarness(tools, sid, max_tool_calls=20, endgame_start_turn=31)
    recipe = harness._turn_refs["recipe"]["recipe_seal_cannoneer"]
    before = tools.get_state(sid)
    result = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "craft_equipment", "arguments": {"recipe_id": recipe}, "as": "seal"},
        {"tool": "equip_item", "arguments": {"adventurer_id": 1, "equipment_instance_id": "$seal"}},
    ]})
    assert not result["ok"] and "第 2 步" in result["error"]
    assert tools.get_state(sid) is before and before.materials["siege_core"] == 1


def test_observation_and_state_roundtrip_keep_clear_flags_and_ultimates(game):
    tools, sid = game
    kill(tools, sid)
    assert tools.craft_equipment(sid, "recipe_seal_cleric")["ok"]
    assert tools.equip_item(sid, "hero_1", "eq_0001")["ok"]
    state = tools.get_state(sid)
    observation = tools.get_observation(sid)["observation"]
    restored = _restore_from_observation(tools.definition, observation)
    assert restored.defeated_challenge_ids == state.defeated_challenge_ids
    assert restored.used_challenge_class_ids == state.used_challenge_class_ids == {"cleric"}
    assert observation["used_challenge_class_ids"] == ["cleric"]
    assert all("challenge_class_available" not in a for a in observation["adventurers"])
    assert build_observation(tools.definition, restored) == build_observation(tools.definition, state)
    raw = _serialize_state(state)
    assert _deserialize_state(raw) == state
    session = GameSession.from_export(tools.definition, {"final_observation": observation})
    result, _ = session.end_turn(EndTurnAction())
    assert "doom_oracle" not in challenge_ids(result.state)


def test_last_battle_core_can_be_crafted_in_final_preparation(game):
    tools, sid = game
    tools._sessions[sid].state = replace(tools.get_state(sid), turn=34,
        current_monsters=spawn_monsters(tools.definition, 34))
    kill(tools, sid)
    state = tools.get_state(sid)
    assert state.turn == 35 and not state.current_monsters
    text = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert "当前为最终整备" in text
    assert tools.craft_equipment(sid, "recipe_seal_cleric")["ok"]
    assert tools.equip_item(sid, "hero_1", "eq_0001")["ok"]
    assert not tools.end_turn(sid, hunts=[{"adventurer_id": "hero_1", "monster_id": "challenge_iron_colossus"}])["ok"]
    assert tools.end_turn(sid, hunts=[])["ok"]
    assert tools.get_state(sid).turn == 36


def test_static_rules_disclose_challenges_without_strategy_recommendations():
    text = build_system_prompt(forecast_enabled=True,
        challenge_rules={"start_turn": 30, "last_hunt_turn": 34})
    for fragment in ("T30", "T34", "T35", "tier=challenge", "ultimate", "不能拆回核心", "不奖励金币或经验", "满HP/MP"):
        assert fragment in text
    assert "普通怪物和未招募" in text
    assert "刷新的普通怪物和招募候选" in text
    assert "神官" not in text and "炮手" not in text


def test_success_blocks_the_whole_class_but_not_normal_hunts_or_arena(game):
    tools, sid = game
    kill(tools, sid)
    before = tools.get_state(sid)
    for hero in ("hero_1", "hero_2"):
        preview = tools.preview_battle(sid, hero, "challenge_iron_colossus")
        assert not preview["ok"] and "该职业已成功通关固定挑战" in preview["error"]
        result = tools.end_turn(sid, hunts=[{"adventurer_id": hero, "monster_id": "challenge_iron_colossus"}])
        assert not result["ok"] and "cleric" in result["error"]
        assert tools.get_state(sid) is before
    reference_score = compute_rank_score(tools.definition, replace(before, used_challenge_class_ids=frozenset()))
    assert reference_score > 0
    assert compute_rank_score(tools.definition, before) == reference_score
    normal = next(m for m in before.current_monsters if m.tier != "challenge")
    assert tools.preview_battle(sid, "hero_2", normal.monster_id)["ok"]
    assert tools.end_turn(sid, hunts=[{"adventurer_id": "hero_2", "monster_id": normal.monster_id}])["ok"]
    assert tools.get_state(sid).used_challenge_class_ids == {"cleric"}


def test_same_class_cannot_win_two_challenges_in_one_atomic_submission(game):
    tools, sid = game
    state = tools.get_state(sid)
    heroes = tuple(replace(a, name="神官·疾风") if a.adventurer_id == "hero_2" else a for a in state.adventurers)
    tools._sessions[sid].state = before = replace(state, adventurers=heroes)
    result = tools.end_turn(sid, hunts=[
        {"adventurer_id": "hero_1", "monster_id": "challenge_doom_oracle"},
        {"adventurer_id": "hero_2", "monster_id": "challenge_iron_colossus"},
    ])
    assert not result["ok"] and "该职业已成功通关固定挑战" in result["error"]
    assert tools.get_state(sid) is before
    assert not before.used_challenge_class_ids and not before.defeated_challenge_ids
    assert "renewal_core" not in before.materials


def test_draw_does_not_block_another_same_class_hero(game):
    tools, sid = game
    stats = CombatStats(10000, 0, 1, 10000, 1, 0)
    d = tools.definition
    target = replace(d.content.challenge_archetypes[0], base_stats=stats, skills=())
    d = replace(d, content=replace(d.content, challenge_archetypes=(target, *d.content.challenge_archetypes[1:])))
    tools.definition = tools._sessions[sid].definition = d
    before = tools.get_state(sid)
    draw_hero = replace(before.adventurers[0], base_stats=stats, resources=CombatResources.full(stats),
                        skills=(), level_skill_unlocks=())
    tools._sessions[sid].state = replace(before, adventurers=(draw_hero, *before.adventurers[1:]),
                                        current_monsters=spawn_monsters(d, 30))
    result = tools.end_turn(sid, hunts=[{"adventurer_id": "hero_1", "monster_id": "challenge_iron_colossus"}])
    assert result["ok"] and not result["turn_result"]["battles"][0]["won"]
    assert not tools.get_state(sid).used_challenge_class_ids
    log = tools.get_battle_log(sid, result["turn_result"]["battles"][0]["battle_id"], "summary")
    assert log["battle"]["outcome"] == "draw"
    kill(tools, sid, "doom_oracle", "hero_2")
    assert tools.get_state(sid).used_challenge_class_ids == {"cleric"}


def test_dismissal_and_recruitment_do_not_restore_used_class_or_change_xp_refund(game):
    tools, sid = game
    tools._sessions[sid].state = replace(tools.get_state(sid), experience_pool=100)
    assert tools.allocate_experience(sid, "hero_1", 100)["ok"]
    kill(tools, sid)
    assert tools.dismiss_adventurer(sid, "hero_1")["ok"]
    assert tools.get_state(sid).experience_pool == 100
    assert tools.dismiss_adventurer(sid, "hero_2")["ok"]
    assert tools.dismiss_adventurer(sid, "hero_3")["ok"]
    empty_observation = tools.get_observation(sid)["observation"]
    assert not empty_observation["adventurers"]
    assert _restore_from_observation(tools.definition, empty_observation).used_challenge_class_ids == {"cleric"}
    candidate = next(c for c in spawn_recruit_candidates(tools.definition, 1) if c.template_id == "cleric")
    tools._sessions[sid].state = replace(tools.get_state(sid), recruit_candidates=(candidate,))
    recruitment = tools.get_recruitment(sid)
    assert recruitment["recruit_candidates"][0]["can_recruit"]
    assert "challenge_class_available" not in recruitment["recruit_candidates"][0]
    assert "固定挑战职业资格" not in _format_tool_result_for_model("get_recruitment", recruitment)
    assert tools.recruit_adventurer(sid, candidate.candidate_id)["ok"]
    state = tools.get_state(sid)
    new_hero = state.adventurers[-1]
    restored = _restore_from_observation(tools.definition, build_observation(tools.definition, state))
    assert restored.used_challenge_class_ids == {"cleric"}
    assert _deserialize_state(_serialize_state(state)).used_challenge_class_ids == {"cleric"}
    result = tools.end_turn(sid, hunts=[{"adventurer_id": new_hero.adventurer_id, "monster_id": "challenge_iron_colossus"}])
    assert not result["ok"] and "cleric" in result["error"]


def test_brief_rule_and_single_status_only_when_challenges_are_present(game):
    tools, sid = game
    text = build_system_prompt(challenge_rules={
        "start_turn": 30, "last_hunt_turn": 34, "unique_challenge_classes": True,
    })
    rule = "每种职业本局最多赢得一个固定挑战。"
    assert text.splitlines().count(rule) == 1
    assert "按职业模板 template_id 识别" not in text
    fresh = build_turn_prompt(build_observation(tools.definition, new_game(tools.definition)))
    assert "固定挑战已用职业" not in fresh
    assert "固定挑战已击败" not in fresh
    active = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert active.count("固定挑战已用职业：无。") == 1
    kill(tools, sid)
    observation = tools.get_observation(sid)["observation"]
    assert build_turn_prompt(observation).count("固定挑战已用职业：cleric。") == 1
    assert "固定挑战职业资格" not in build_turn_prompt(observation)
    party = tools.get_party(sid)
    assert "固定挑战职业资格" not in _format_tool_result_for_model("get_party", party)
    assert all("challenge_class_available" not in a for a in party["adventurers"])
    while tools.get_state(sid).turn < 35:
        assert tools.end_turn(sid, hunts=[])["ok"]
    final = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert "固定挑战已用职业" not in final
    assert "固定挑战已击败" not in final


def test_old_rule_does_not_limit_class_reuse_or_add_observation_fields(game):
    tools, sid = game
    d = replace(tools.definition, rules=replace(tools.definition.rules, unique_challenge_classes=False))
    tools.definition = tools._sessions[sid].definition = d
    observation = tools.get_observation(sid)["observation"]
    assert "unique_challenge_classes" not in observation
    assert "used_challenge_class_ids" not in observation
    assert all("challenge_class_available" not in a for a in observation["adventurers"])
    kill(tools, sid, "doom_oracle")
    kill(tools, sid, "iron_colossus")
    kill(tools, sid, "blade_tyrant")
    assert len(tools.get_state(sid).defeated_challenge_ids) == 3
    assert not tools.get_state(sid).used_challenge_class_ids


def test_challenge_requires_class_identity_when_class_limit_enabled(game):
    tools, sid = game
    state = tools.get_state(sid)
    tools._sessions[sid].state = replace(state, adventurers=(replace(state.adventurers[0], template_id=""),))
    assert "challenge_class_available" not in tools.get_party(sid)["adventurers"][0]
    result = tools.end_turn(sid, hunts=[{"adventurer_id": "hero_1", "monster_id": "challenge_doom_oracle"}])
    assert not result["ok"] and "职业 template_id" in result["error"]


def test_failed_attempt_can_precede_same_class_success_in_one_submission(game):
    tools, sid = game
    d = tools.definition
    hard = replace(d.content.challenge_archetypes[0], base_stats=CombatStats(300, 0, 40, 0, 8, 0), skills=())
    d = replace(d, content=replace(d.content, challenge_archetypes=(hard, *d.content.challenge_archetypes[1:])))
    tools.definition = tools._sessions[sid].definition = d
    tools._sessions[sid].state = replace(tools.get_state(sid), current_monsters=spawn_monsters(d, 30))
    result = tools.end_turn(sid, hunts=[
        {"adventurer_id": "hero_1", "monster_id": "challenge_iron_colossus"},
        {"adventurer_id": "hero_2", "monster_id": "challenge_doom_oracle"},
    ])
    assert result["ok"]
    assert [b["won"] for b in result["turn_result"]["battles"]] == [False, True]
    assert tools.get_state(sid).used_challenge_class_ids == {"cleric"}
    assert tools.get_state(sid).defeated_challenge_ids == {"doom_oracle"}


def test_rule_is_opt_in_for_old_data_and_has_a_new_protocol(tmp_path):
    from guild_manager_bench.bench.llm.runner import LlmRunConfig
    from guild_manager_bench.bench.protocols import protocol_metadata, verify_resume_protocol
    from guild_manager_bench.game.presets import describe_data_source

    data = tmp_path / "data"
    shutil.copytree(ROOT / "data/presets/advanced", data)
    path = data / "game.yaml"
    current_yaml = path.read_text(encoding="utf-8")
    path.write_text(current_yaml.replace("  unique_challenge_classes: true\n", ""), encoding="utf-8")
    old = load_game_definition(data)
    assert not old.rules.unique_challenge_classes
    archived = {**describe_data_source(data), **protocol_metadata(data, old, LlmRunConfig())}
    (tmp_path / "replay.json").write_text(json.dumps({"data": archived}), encoding="utf-8")
    path.write_text(current_yaml, encoding="utf-8")
    current = load_game_definition(data)
    assert current.rules.unique_challenge_classes
    source = {**describe_data_source(data), **protocol_metadata(data, current, LlmRunConfig())}
    assert source["data_hash"] != archived["data_hash"]
    assert source["protocol_hash"] != archived["protocol_hash"]
    assert source["benchmark_version"] != archived["benchmark_version"]
    with pytest.raises(ValueError, match="data_hash"):
        verify_resume_protocol(tmp_path, source)


def test_replaying_confirmed_challenge_win_restores_class_limit(game, tmp_path):
    from guild_manager_bench.bench.llm.runner import _restore_from_replay_archive

    tools, _ = game
    d = replace(tools.definition, rules=replace(tools.definition.rules, max_turns=3),
                content=replace(tools.definition.content, challenge_archetypes=tuple(
                    replace(c, min_turn=1) for c in tools.definition.content.challenge_archetypes)))
    original = GuildManagerTools(d)
    sid = original.start_session("challenge-replay")["session_id"]
    before = original.get_observation(sid)["observation"]
    harness = TurnToolHarness(original, sid, max_tool_calls=20)
    args = {"hunts": [{"adventurer_id": 1, "monster_id": harness._turn_refs["monster"]["challenge_doom_oracle"]}]}
    result = harness.call_tool("end_turn", args)
    assert result["ok"] and result["turn_result"]["battles"][0]["won"]
    replay = {"kind": "llm_replay", "session_id": sid, "turns": [{
        "turn": 1, "status": "completed", "observation_before": before,
        "steps": [{"type": "tool_result", "name": "end_turn", "arguments": args, "result": result}],
    }]}
    (tmp_path / "replay.json").write_text(json.dumps(replay), encoding="utf-8")
    restored = GuildManagerTools(d)
    _restore_from_replay_archive(restored, tmp_path, {})
    assert restored.get_state(sid).used_challenge_class_ids == {"cleric"}
    assert restored.get_observation(sid) == original.get_observation(sid)
    blocked = restored.preview_battle(sid, "hero_2", "challenge_iron_colossus")
    assert not blocked["ok"] and "该职业已成功通关固定挑战" in blocked["error"]


def test_runner_discloses_class_limit_from_advanced_preset(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from guild_manager_bench.bench.llm import runner

    monkeypatch.setattr(runner, "ProcessPoolExecutor", ThreadPoolExecutor)

    class EndAgent:
        first_messages = None

        def respond(self, *, messages, tools):
            if self.first_messages is None:
                self.first_messages = deepcopy(messages)
            return runner.LlmAgentResponse(tool_calls=(runner.LlmToolCall("end_turn", {"hunts": []}),))

    agent = EndAgent()
    run = runner.run_llm_game(agent, data_dir=ROOT / "data/presets/advanced",
                              config=runner.LlmRunConfig(archive_dir=tmp_path))
    assert run.status == "completed"
    assert "每种职业本局最多赢得一个固定挑战。" in agent.first_messages[0]["content"]
    assert run.final_observation["unique_challenge_classes"]
    assert run.final_observation["used_challenge_class_ids"] == []
