from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.metrics import compute_rank_score
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.game.state import AdventurerState


@pytest.fixture
def game():
    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data/presets/default")
    stats = CombatStats(hp=100, mp=50, attack=20, defense=8, speed=20, recovery=0)
    skills = (
        Skill("weak", "轻击", "active", SkillCondition("always"),
              (SkillEffect("damage_multiplier", 0.5),), priority=10),
        Skill("strong", "重击", "active", SkillCondition("always"),
              (SkillEffect("damage_multiplier", 3.0),), priority=1, mp_cost=2),
        Skill("passive", "坚韧", "passive", SkillCondition("always"),
              (SkillEffect("stat_bonus", 1, stat="defense", target="self"),)),
    )
    hero = AdventurerState("hero", "测试角色", stats, CombatResources.full(stats),
                           skills=skills, level=2,
                           stat_growth_per_level=definition.content.experience_rules.stat_growth_per_level)
    definition = replace(
        definition,
        content=replace(definition.content, adventurers=(hero,)),
        starting_gold=1000,
        llm_tools=replace(definition.llm_tools, expose_tactics=True,
                          expose_battle_preview=True, endgame_start_turn=1),
        scoring=replace(definition.scoring, rank_max_diff=31, rank_step=10, rank_waves=2),
    )
    tools = GuildManagerTools(definition)
    sid = tools.start_session("tactics")['session_id']
    return tools, sid


def test_tactics_changes_real_combat_and_can_reset(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=10, endgame_start_turn=1)
    before = tools.get_state(sid)
    monster_id = before.current_monsters[0].monster_id
    original = tools.preview_battle(sid, "hero", monster_id)["preview"]
    assert next(e["skill_id"] for e in original["events"] if e["actor_id"] == "hero") == "weak"

    result = harness.call_tool("configure_tactics", {
        "adventurer_id": 1, "skill_priority": ["strong"], "disabled_skills": [],
    })
    assert result["ok"]
    actual = tools.preview_battle(sid, "hero", monster_id)["preview"]
    assert next(e["skill_id"] for e in actual["events"] if e["actor_id"] == "hero") == "strong"
    assert tools.get_state(sid).adventurers[0].resources == before.adventurers[0].resources
    assert tools.get_state(sid).gold == before.gold

    assert harness.call_tool("configure_tactics", {
        "adventurer_id": 1, "skill_priority": [], "disabled_skills": ["weak", "strong"],
    })["ok"]
    observed = tools.get_observation(sid)["observation"]["adventurers"][0]
    assert [s["skill_id"] for s in observed["skills"]] == ["passive"]
    assert {s["skill_id"] for s in observed["tactics"]["available_active_skills"]} == {"weak", "strong"}

    assert harness.call_tool("configure_tactics", {
        "adventurer_id": 1, "skill_priority": [], "disabled_skills": [],
    })["ok"]
    assert tools.get_state(sid) == before
    assert tools.preview_battle(sid, "hero", monster_id)["preview"] == original


def test_plan_preview_compares_current_state_without_mutation(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=10, endgame_start_turn=1)
    # The baseline must include changes made since this turn started.
    assert harness.call_tool("recruit_adventurer", {"candidate_id": 1})["ok"]
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    refs = deepcopy(harness._turn_refs)
    current = round(compute_rank_score(tools.definition, before), 2)
    operation = {"tool": "configure_tactics", "arguments": {
        "adventurer_id": 1, "skill_priority": ["strong"], "disabled_skills": [],
    }}
    preview = harness.call_tool("preview_team_power", {"operations": [operation]})
    assert preview["ok"]
    assert preview["simulation_only"] is True
    assert preview["current_rank_score"] == current
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert harness._turn_refs == refs
    assert "per_adventurer" not in preview
    assert "event" not in preview
    assert "observation" not in preview
    text = _format_tool_result_for_model("preview_team_power", preview)
    assert "真实状态未改变" in text
    assert "相对调用前" in text
    assert "累计变化" not in text

    assert harness.call_tool(operation["tool"], operation["arguments"])["ok"]
    actual = round(compute_rank_score(tools.definition, tools.get_state(sid)), 2)
    assert preview["projected_rank_score"] == actual
    assert preview["rank_score_delta"] == round(actual - current, 2)
    assert actual > current


def test_plan_can_replace_recruit_train_and_craft_with_local_references(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=20, endgame_start_turn=1)
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    refs = deepcopy(harness._turn_refs)
    refund = tools.definition.content.experience_rules.base_required_experience
    operations = [
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 1}},
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "replacement"},
        {"tool": "allocate_experience", "arguments": {"adventurer_id": "$replacement", "amount": refund}},
        {"tool": "craft_equipment", "arguments": {"recipe_id": 1}, "as": "weapon"},
        {"tool": "equip_item", "arguments": {
            "adventurer_id": "$replacement", "equipment_instance_id": "$weapon",
        }},
        {"tool": "configure_tactics", "arguments": {
            "adventurer_id": "$replacement", "skill_priority": [], "disabled_skills": [],
        }},
    ]
    preview = harness.call_tool("preview_team_power", {"operations": operations})
    assert preview["ok"], preview
    assert preview["operation_count"] == 6
    assert preview["tool_budget"]["used"] == 1
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert harness._turn_refs == refs
    assert "recruit_0001" not in str(preview)
    assert "eq_0001" not in str(preview)

    assert harness.call_tool("dismiss_adventurer", {"adventurer_id": 1})["ok"]
    assert harness.call_tool("recruit_adventurer", {"candidate_id": 1})["ok"]
    assert harness.call_tool("allocate_experience", {"adventurer_id": 2, "amount": refund})["ok"]
    assert harness.call_tool("craft_equipment", {"recipe_id": 1})["ok"]
    assert harness.call_tool("equip_item", {"adventurer_id": 2, "equipment_instance_id": 1})["ok"]
    assert harness.call_tool("configure_tactics", {
        "adventurer_id": 2, "skill_priority": [], "disabled_skills": [],
    })["ok"]
    actual = round(compute_rank_score(tools.definition, tools.get_state(sid)), 2)
    assert preview["projected_rank_score"] == actual
    # References belong to the real turn, not to a rebuilt positional roster.
    current = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 2}},
    ]})
    assert current["ok"]
    assert current["projected_rank_score"] == 0


@pytest.mark.parametrize("bad", [
    {"tool": "get_recruitment", "arguments": {}},
    {"tool": "preview_team_power", "arguments": {"operations": []}},
    {"tool": "preview_battle", "arguments": {}},
    {"tool": "end_turn", "arguments": {"hunts": []}},
    {"tool": "write_memo", "arguments": {"content": "虚拟备忘"}},
    {"tool": "allocate_experience", "arguments": {"adventurer_id": 1, "amount": 10000}},
    {"tool": "allocate_experience", "arguments": {"adventurer_id": 1, "amount": True}},
    {"tool": "allocate_experience", "arguments": {"adventurer_id": 1, "amount": -1}},
    {"tool": "allocate_experience", "arguments": {"adventurer_id": "$future", "amount": 0}},
    {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 999}},
    {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 1, "session_id": "other"}},
    {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 1}, "as": "wrong"},
])
def test_invalid_plan_is_atomic_and_does_not_return_partial_score(game, bad):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=1, endgame_start_turn=1)
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    result = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "craft_equipment", "arguments": {"recipe_id": 1}, "as": "sword"}, bad,
    ]})
    assert not result["ok"]
    assert "第 2 步" in result["error"]
    assert "projected_rank_score" not in result
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert harness.memo_store.snapshot().content == ""
    assert harness.memo_store.snapshot().updated_turn is None
    assert result["tool_budget"]["used"] == 1
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]


@pytest.mark.parametrize("priority,disabled", [
    (["missing"], []), (["passive"], []), ([], ["passive"]),
    (["weak", "weak"], []), (["weak"], ["weak"]),
    ("weak", []), ([True], []),
])
def test_invalid_tactics_do_not_change_state(game, priority, disabled):
    tools, sid = game
    before = tools.get_state(sid)
    result = tools.configure_tactics(sid, "hero", priority, disabled)
    assert not result["ok"]
    assert tools.get_state(sid) is before


@pytest.mark.parametrize("operations", [None, {}, "bad", [None] * 21])
def test_preview_rejects_invalid_or_unbounded_plans(game, operations):
    tools, sid = game
    before = tools.get_state(sid)
    result = tools.preview_team_power(sid, operations)
    assert not result["ok"]
    assert tools.get_state(sid) is before


def test_tactics_survive_observation_restore_and_action_replay(game):
    from guild_manager_bench.runtime.action_codec import decode_preparation_action
    from guild_manager_bench.runtime.session import GameSession

    tools, sid = game
    initial = tools.get_state(sid)
    assert tools.configure_tactics(sid, "hero", ["strong"], ["weak"])["ok"]
    observation = tools.get_observation(sid)["observation"]
    saved = GameSession(tools.definition, state=tools.get_state(sid)).export_replay()
    assert GameSession.from_export(tools.definition, saved).state == tools.get_state(sid)
    restored = GameSession.from_export(tools.definition, {"final_observation": observation})
    assert restored.state == tools.get_state(sid)
    assert compute_rank_score(tools.definition, restored.state) == compute_rank_score(
        tools.definition, tools.get_state(sid),
    )
    replayed = GameSession(tools.definition, state=initial)
    for event in tools.get_events(sid)["events"]:
        if event["type"] == "preparation_applied":
            replayed.apply_preparation(decode_preparation_action(event["action"]))
    assert replayed.state == tools.get_state(sid)
    restored.apply_preparation(decode_preparation_action({
        "type": "configure_tactics", "adventurer_id": "hero",
        "skill_priority": [], "disabled_skills": [],
    }))
    assert restored.state == initial


def test_tactics_are_visible_and_only_exposed_when_enabled(game):
    from guild_manager_bench.bench.llm.prompts import build_turn_prompt

    tools, sid = game
    assert "configure_tactics" in {s["name"] for s in tools.list_tool_schemas()}
    assert tools.configure_tactics(sid, "hero", [], ["weak"])["ok"]
    text = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert "weak（已停用）" in text
    assert "可配置技能 ID strong" in text
    disabled = GuildManagerTools(replace(
        tools.definition, llm_tools=replace(tools.definition.llm_tools, expose_tactics=False),
    ))
    sid = disabled.start_session("disabled")["session_id"]
    harness = TurnToolHarness(disabled, sid, max_tool_calls=3, endgame_start_turn=1)
    assert "configure_tactics" not in {s["name"] for s in harness.tool_schemas()}
    args = {"adventurer_id": 1, "skill_priority": [], "disabled_skills": []}
    assert not harness.call_tool("configure_tactics", args)["ok"]
    assert not harness.call_tool("preview_team_power", {"operations": [
        {"tool": "configure_tactics", "arguments": args},
    ]})["ok"]


@pytest.mark.parametrize("second", [
    {"tool": "recruit_adventurer", "arguments": {"candidate_id": 2}, "as": "new"},
    {"tool": "equip_item", "arguments": {"adventurer_id": 1, "equipment_instance_id": "$new"}},
])
def test_plan_aliases_cannot_be_redefined_or_used_as_wrong_kind(game, second):
    tools, sid = game
    before = tools.get_state(sid)
    harness = TurnToolHarness(tools, sid, max_tool_calls=2, endgame_start_turn=1)
    result = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "new"}, second,
    ]})
    assert not result["ok"]
    assert "第 2 步" in result["error"]
    assert tools.get_state(sid) is before
    # Local references expire even after a successful preview.
    assert harness.call_tool("preview_team_power", {"operations": [
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": "$new"}},
    ]})["ok"] is False


def test_failed_plan_does_not_leak_simulated_entity_ids(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=1, endgame_start_turn=1)
    result = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "new"},
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": "$new"}},
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": "$new"}},
    ]})
    assert not result["ok"]
    assert "$new" in result["error"]
    assert "recruit_0001" not in result["error"]


def test_equipment_skill_configuration_survives_unequip_and_re_equip(game):
    tools, _ = game
    definition = tools.definition
    recipe = definition.content.crafting_recipes[0]
    gear_skill = replace(definition.content.adventurers[0].skills[0], skill_id="gear_active")
    templates = tuple(
        replace(template, skills=(gear_skill,)) if template.equipment_id == recipe.output_template_id
        else template for template in definition.content.equipment_templates
    )
    definition = replace(definition, content=replace(definition.content, equipment_templates=templates))
    tools = GuildManagerTools(definition)
    sid = tools.start_session("gear-tactics")["session_id"]
    assert tools.craft_equipment(sid, recipe.recipe_id)["ok"]
    equipment_id = tools.get_state(sid).equipment_inventory[0].instance_id
    assert tools.equip_item(sid, "hero", equipment_id)["ok"]
    assert tools.configure_tactics(sid, "hero", ["strong"], ["gear_active"])["ok"]
    before = tools.get_state(sid)
    slot = before.adventurers[0].equipment.items[0].slot
    assert tools.unequip_item(sid, "hero", slot)["ok"]
    assert tools.equip_item(sid, "hero", equipment_id)["ok"]
    assert tools.get_state(sid) == before
    observed = tools.get_observation(sid)["observation"]["adventurers"][0]
    assert "gear_active" not in {s["skill_id"] for s in observed["skills"]}
    assert "gear_active" in {s["skill_id"] for s in observed["tactics"]["available_active_skills"]}
    assert tools.configure_tactics(sid, "hero", [], [])["ok"]
    observed = tools.get_observation(sid)["observation"]["adventurers"][0]
    assert "gear_active" in {s["skill_id"] for s in observed["skills"]}


def test_llm_trace_and_rebuild_preserve_real_tactics():
    from guild_manager_bench.bench.llm.runner import (
        LlmAgentResponse, LlmRunConfig, LlmToolCall, rebuild_replay_observations, run_llm_turn,
    )

    data_dir = Path(__file__).resolve().parents[1] / "data/presets/advanced"
    tools = GuildManagerTools.from_data_dir(data_dir)
    sid = tools.start_session("trace-tactics")["session_id"]
    number, candidate = next(
        (i, c) for i, c in enumerate(tools.get_state(sid).recruit_candidates, start=1)
        if any(s.kind == "active" for s in c.skills)
    )
    skill = next(s.skill_id for s in candidate.skills if s.kind == "active")
    calls = iter([
        LlmToolCall("recruit_adventurer", {"candidate_id": number}),
        LlmToolCall("configure_tactics", {
            "adventurer_id": 1, "skill_priority": [], "disabled_skills": [skill],
        }),
        LlmToolCall("end_turn", {"hunts": []}),
    ])

    class Agent:
        def respond(self, *, messages, tools):
            return LlmAgentResponse(tool_calls=(next(calls),))

    trace = run_llm_turn(Agent(), tools, sid, config=LlmRunConfig(archive_dir=None))
    assert trace.status == "completed"
    tactics_result = trace.tool_calls[1].result
    assert tactics_result["_observation_after"]["adventurers"][0]["tactics"]["disabled_skills"] == [skill]
    replay = {"turns": [{"turn": 1, "status": "completed", "steps": [
        {"type": "tool_result", "name": call.name, "arguments": call.arguments,
         "content": _format_tool_result_for_model(call.name, call.result)}
        for call in trace.tool_calls
    ]}]}
    rebuilt = rebuild_replay_observations(replay, data_dir=data_dir)
    reconstructed = rebuilt["turns"][0]["steps"][-1]["observation_after"]
    actual = tools.get_observation(sid)["observation"]
    assert reconstructed["adventurers"] == actual["adventurers"]
    assert reconstructed["adventurers"][0]["tactics"]["disabled_skills"] == [skill]
