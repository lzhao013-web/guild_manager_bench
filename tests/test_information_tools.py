from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import random
import re

import pytest

from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import build_system_prompt
from guild_manager_bench.bench.llm.runner import (
    LlmAgentResponse, LlmRunConfig, LlmToolCall, _format_tool_result_for_model, run_llm_game,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.game.engine import spawn_monsters
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.observation import _skill_to_dict
from guild_manager_bench.runtime.session import GameSession


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def game():
    definition = load_game_definition(ROOT / "data/presets/advanced")
    tools = GuildManagerTools(definition)
    sid = tools.start_session("information")['session_id']
    harness = TurnToolHarness(tools, sid, max_tool_calls=20)
    assert harness.call_tool("recruit_adventurer", {"candidate_id": 1})["ok"]
    return tools, sid, harness


def test_preview_log_is_cached_paginated_and_uses_only_query_budget(game, monkeypatch):
    tools, sid, harness = game
    preview = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})
    battle_id = preview["preview"]["battle_id"]
    text = _format_tool_result_for_model("preview_battle", preview)
    assert f"battle_id={battle_id}" in text
    assert "不随后续调整更新" in text
    state = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    refs = deepcopy(harness._turn_refs)

    def no_simulation(*args, **kwargs):
        raise AssertionError("读取记录不得重新模拟")
    monkeypatch.setattr("guild_manager_bench.bench.llm.tools.preview_battle_result", no_simulation)
    summary = harness.call_tool("get_battle_log", {"battle_id": battle_id})
    assert summary["battle"]["event_count"] == len(preview["preview"]["events"])
    args = {"battle_id": battle_id, "detail": "events"}
    result = harness.call_tool("get_battle_log", args)
    returned = list(result["events"])
    assert result["tool_budget"]["battle_preview_used"] == 1
    assert result["tool_budget"]["used"] == 4
    while result["next_cursor"] is not None:
        result = harness.call_tool("get_battle_log", {**args, "cursor": result["next_cursor"]})
        assert result["ok"]
        returned.extend(result["events"])
    assert len(returned) == len(preview["preview"]["events"])
    for actual, expected in zip(returned, preview["preview"]["events"], strict=True):
        assert {k: actual[k] for k in expected if k not in ("actor_id", "target_id")} == {
            k: v for k, v in expected.items() if k not in ("actor_id", "target_id")
        }
        assert "actor_id" not in actual and "target_id" not in actual
    assert tools.get_state(sid) is state
    assert tools.get_events(sid) == events
    assert harness._turn_refs == refs
    visible = _format_tool_result_for_model("get_battle_log", result)
    assert "当轮预览记录（未实际执行）" in visible
    assert "技能" in visible or "普攻" in visible or "状态结算" in visible
    assert "当前队伍" in visible


def test_log_window_and_cached_records_survive_roster_changes(game):
    tools, sid, harness = game
    preview = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})
    battle_id = preview["preview"]["battle_id"]
    original = tools.get_battle_log(sid, battle_id, "events")
    assert tools.dismiss_adventurer(sid, tools.get_state(sid).adventurers[0].adventurer_id)["ok"]
    assert tools.get_battle_log(sid, battle_id, "events") == original
    stats = tools.get_battle_log(sid, battle_id)["statistics"]
    all_events = list(original["events"])
    cursor = original["next_cursor"]
    while cursor is not None:
        page = tools.get_battle_log(sid, battle_id, "events", cursor=cursor)
        all_events.extend(page["events"])
        cursor = page["next_cursor"]
    for side in ("left", "right"):
        own = [e for e in all_events if e["actor_side"] == side]
        assert stats[side]["direct_damage"] == sum(e["damage"] for e in own if e["action_type"] != "status")
    # Returned objects cannot be used to rewrite the cached record.
    original["events"][0]["damage"] = -999
    assert tools.get_battle_log(sid, battle_id, "events")["events"][0]["damage"] >= 0


def test_only_current_previews_and_previous_actual_battles_are_visible(game):
    tools, sid, harness = game
    assert not harness.call_tool("get_battle_log", {"battle_id": "missing"})["ok"]
    preview = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})
    preview_id = preview["preview"]["battle_id"]
    ended = harness.call_tool("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]})
    assert ended["ok"]
    battle_id = ended["turn_result"]["battles"][0]["battle_id"]
    assert battle_id != preview_id
    assert ended["event"]["battles"][0]["battle_id"] == battle_id
    assert battle_id in _format_tool_result_for_model("end_turn", ended)
    from guild_manager_bench.bench.llm.prompts import build_turn_prompt
    prompt = build_turn_prompt(tools.get_observation(sid)["observation"], previous_turn_event=ended["event"])
    assert f"battle_id={battle_id}" in prompt
    assert sid not in tools._battle_previews
    harness = TurnToolHarness(tools, sid, max_tool_calls=10)
    assert not harness.call_tool("get_battle_log", {"battle_id": preview_id})["ok"]
    result = harness.call_tool("get_battle_log", {"battle_id": battle_id, "detail": "events"})
    real = next(e for e in tools._sessions[sid].events if e.event_type == "turn_ended").payload["battles"][0]
    assert result["turn"] == 1
    assert result["battle"]["outcome"] == real["combat"]["outcome"]
    expected = [{k: v for k, v in e.items() if k not in ("actor_id", "target_id")}
                for e in real["combat"]["events"][:20]]
    assert result["events"] == expected
    assert "上轮真实战斗记录" in _format_tool_result_for_model("get_battle_log", result)
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]
    assert not TurnToolHarness(tools, sid, max_tool_calls=1).call_tool("get_battle_log", {"battle_id": battle_id})["ok"]


def test_battle_statistics_keep_status_damage_and_instant_skill_events_distinct():
    from guild_manager_bench.bench.llm.battle_log import query_battle_log
    from guild_manager_bench.game.combat import Combatant, run_auto_battle
    from guild_manager_bench.game.models import CombatResources, CombatStats
    from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition

    poison = StatusDefinition("poison", "中毒", 2, (SkillEffect("true_damage", 3),))
    opening = Skill("opening", "毒刃与治疗", "active", SkillCondition("always"), (
        SkillEffect("apply_status", status=poison), SkillEffect("heal", 10, target="self"),
    ), mp_cost=2, once_per_battle=True, free=True)
    hero = Combatant("hero", CombatStats(100, 20, 15, 3, 20, 0), CombatResources(60, 20), (opening,))
    monster = Combatant("monster", CombatStats(100, 0, 8, 2, 20, 0))
    combat = run_auto_battle(hero, monster)
    record = {"adventurer_name": "角色", "monster_name": "怪物", "combat": asdict(combat)}
    records = {"test-battle": {**record, "source": "preview", "turn": 1}}
    summary = query_battle_log(records, battle_id="test-battle")
    stats = summary["statistics"]
    assert stats["left"]["skill_uses"] == {"opening": 1}
    assert stats["left"]["healing_received"] == 10
    assert stats["right"]["status_damage_taken"] == 6
    assert stats["left"]["status_damage_taken"] == 0
    assert stats["left"]["direct_damage"] > 0
    # Both a status tick and a normal action can share one action index.
    status_action = next(e.action_index for e in combat.events if e.action_type == "status")
    page = query_battle_log(records, battle_id="test-battle", detail="events")
    selected = [event for event in page["events"] if event["action_index"] == status_action]
    assert {e["action_type"] for e in selected} == {"status", "basic_attack"}
    assert len(selected) == 2
    text = _format_tool_result_for_model("get_battle_log", summary)
    assert "附带普攻合并" in text
    assert "日志不包含逐动作MP余额" in text


def test_log_queries_work_after_preview_quota_is_exhausted_and_are_session_local(game):
    tools, sid, _ = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=10, max_battle_preview_per_turn=1)
    preview = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})
    assert preview["ok"]
    battle_id = preview["preview"]["battle_id"]
    assert not harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})["ok"]
    assert harness.call_tool("get_battle_log", {"battle_id": battle_id})["ok"]
    tools.start_session("other")
    assert not TurnToolHarness(tools, "other", max_tool_calls=1).call_tool("get_battle_log", {"battle_id": battle_id})["ok"]


def test_actual_battle_log_survives_session_export_and_restore(game):
    tools, sid, harness = game
    ended = harness.call_tool("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]})
    battle_id = ended["turn_result"]["battles"][0]["battle_id"]
    expected = tools.get_battle_log(sid, battle_id, "events")
    fast_definition = replace(tools.definition, scoring=replace(tools.definition.scoring, rank_waves=1, rank_max_diff=1))
    tools._sessions[sid].definition = fast_definition
    saved = tools._sessions[sid].export_replay()
    restored_tools = GuildManagerTools(tools.definition)
    restored_tools._sessions[sid] = GameSession.from_export(tools.definition, saved)
    assert restored_tools.get_battle_log(sid, battle_id, "events") == expected
    restored_event = next(e for e in restored_tools.get_events(sid)["events"] if e["type"] == "turn_ended")
    assert restored_event["battles"][0]["battle_id"] == battle_id
    assert sid not in restored_tools._battle_previews


@pytest.mark.parametrize("arguments", [
    {}, {"battle_id": ""}, {"battle_id": []}, {"battle_id": True}, {"battle_id": 1},
    {"battle_id": "missing"}, {"battle_id": "missing", "detail": "all"},
    {"battle_id": "missing", "detail": "events", "cursor": -1},
    {"battle_id": "missing", "detail": "events", "cursor": True},
    {"battle_id": "missing", "cursor": 20},
    {"source": "preview", "battle_index": 1},
    {"battle_id": "missing", "action_start": 2},
    {"battle_id": "missing", "limit": 1},
])
def test_bad_log_queries_are_bounded_and_do_not_mutate(game, arguments):
    tools, sid, harness = game
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    result = harness.call_tool("get_battle_log", arguments)
    assert not result["ok"]
    assert result["tool_budget"]["used"] == 2
    assert result["tool_budget"]["battle_preview_used"] == 0
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events


def test_event_pages_have_fixed_size_and_preserve_all_events():
    from guild_manager_bench.bench.llm.battle_log import query_battle_log
    from guild_manager_bench.game.combat import Combatant, run_auto_battle
    from guild_manager_bench.game.models import CombatStats

    stats = CombatStats(45, 0, 1, 100, 20, 0)
    combat = run_auto_battle(Combatant("hero", stats), Combatant("monster", stats))
    records = {"long-battle": {
        "source": "actual", "turn": 1, "adventurer_name": "角色", "monster_name": "怪物",
        "combat": asdict(combat),
    }}
    page = query_battle_log(records, battle_id="long-battle", detail="events")
    assert len(page["events"]) == 20 and page["next_cursor"] == 20
    assert "cursor=20" in _format_tool_result_for_model("get_battle_log", page)
    events = list(page["events"])
    while page["next_cursor"] is not None:
        page = query_battle_log(records, battle_id="long-battle", detail="events", cursor=page["next_cursor"])
        assert 1 <= len(page["events"]) <= 20
        events.extend(page["events"])
    expected = [{k: v for k, v in asdict(event).items() if k not in ("actor_id", "target_id")}
                for event in combat.events]
    assert events == expected
    assert "已返回全部事件" in _format_tool_result_for_model("get_battle_log", page)


def test_preview_ids_are_distinct_and_never_alias_later_turns(game):
    tools, sid, harness = game
    first = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})["preview"]["battle_id"]
    second = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})["preview"]["battle_id"]
    assert first != second
    assert harness.call_tool("get_battle_log", {"battle_id": first})["ok"]
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]
    current = TurnToolHarness(tools, sid, max_tool_calls=5)
    third = current.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": 1})["preview"]["battle_id"]
    assert third not in (first, second)
    assert not current.call_tool("get_battle_log", {"battle_id": first})["ok"]
    assert current.call_tool("get_battle_log", {"battle_id": third})["ok"]


def test_forecast_is_paginated_and_matches_real_future_refresh(game):
    tools, sid, harness = game
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    refs = deepcopy(harness._turn_refs)
    rng = random.getstate()
    args = {"kind": "recruits", "start_turn": 3, "detail": "full", "limit": 1}
    result = harness.call_tool("get_forecast", args)
    items = list(result["items"])
    while result["next_offset"] is not None:
        result = harness.call_tool("get_forecast", {**args, "offset": result["next_offset"]})
        items.extend(result["items"])
    assert len(items) == 3
    assert result["tool_budget"]["battle_preview_used"] == 0
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert harness._turn_refs == refs
    assert random.getstate() == rng
    assert all("candidate_id" not in item for item in items)
    assert all(item["forecast_ref"].startswith("future:T3:recruits:") for item in items)
    visible = _format_tool_result_for_model("get_forecast", result)
    assert "未来刷新查询" in visible and "不能用于当前操作" in visible
    assert "每级成长" in visible and "解锁" in visible

    # Player actions do not change the advertised sequence.
    assert harness.call_tool("dismiss_adventurer", {"adventurer_id": 1})["ok"]
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]
    assert tools.end_turn(sid, [])["ok"]
    actual = tools.get_observation(sid)["observation"]["recruit_candidates"]
    for forecast, candidate in zip(items, actual, strict=True):
        for key in ("name", "template_id", "recruit_gold", "base_stats", "stat_growth_per_level", "skills"):
            assert forecast[key] == candidate[key]
        assert forecast["level_skill_unlocks"] == [
            {"level": u["level"], "skills": u["skills"]} for u in candidate["level_skill_unlocks"]
        ]


def test_forecast_filters_by_name_material_tier_and_preserves_original_index(game):
    tools, sid, harness = game
    all_entries = [(turn, index, monster) for turn in range(2, 11)
                   for index, monster in enumerate(spawn_monsters(tools.definition, turn), 1)]
    turn, index, monster = next(row for row in all_entries if row[2].reward.materials)
    material = next(iter(monster.reward.materials))
    arguments = {"kind": "monsters", "start_turn": 2, "end_turn": 10,
                 "name_contains": monster.archetype_id, "tier": monster.tier,
                 "material": material, "limit": 10}
    result = harness.call_tool("get_forecast", arguments)
    assert result["ok"]
    expected = [(t, i, m) for t, i, m in all_entries if m.archetype_id == monster.archetype_id
                and m.tier == monster.tier and m.reward.materials.get(material, 0) > 0]
    assert result["total_matches"] == len(expected)
    assert [(item["turn"], item["entry_index"]) for item in result["items"]] == [(t, i) for t, i, _ in expected[:10]]
    from guild_manager_bench.game.state import MATERIAL_NAMES
    translated = harness.call_tool("get_forecast", {**arguments, "material": MATERIAL_NAMES[material]})
    assert translated["items"] == result["items"]
    detail = harness.call_tool("get_forecast", {
        "kind": "monsters", "start_turn": turn, "entry_index": index, "detail": "full",
    })
    item = detail["items"][0]
    assert item["stats"] == asdict(monster.stats)
    assert item["skills"] == [_skill_to_dict(skill) for skill in monster.skills]
    assert item["base_reward"] == {"gold": monster.reward.gold, "experience": monster.reward.experience,
                                   "materials": dict(monster.reward.materials)}
    assert "monster_id" not in item
    text = _format_tool_result_for_model("get_forecast", detail)
    assert "未计经营天赋加成" in text
    assert "评分种子" not in text and "20260529" not in text


def test_forecast_base_rewards_are_not_changed_by_economy_upgrades(game):
    tools, sid, harness = game
    args = {"kind": "monsters", "start_turn": 4, "detail": "full"}
    before = tools.get_forecast(sid, **args)
    upgrade = next(u for u in tools.definition.content.global_upgrades if u.hunt_experience_bonus_percent)
    state = tools.get_state(sid)
    tools._sessions[sid].state = replace(state, unlocked_upgrade_ids=state.unlocked_upgrade_ids | {upgrade.upgrade_id})
    assert tools.get_forecast(sid, **args) == before
    for _ in range(3):
        assert tools.end_turn(sid, [])["ok"]
    observed = tools.get_observation(sid)["observation"]["monsters"]
    for forecast, actual in zip(before["items"], observed):
        assert forecast["base_reward"] == actual["base_reward"]
        assert forecast["stats"] == actual["stats"]
        assert actual["reward"]["experience"] > forecast["base_reward"]["experience"]


def test_future_references_cannot_be_used_as_current_candidates_or_monsters(game):
    tools, sid, harness = game
    before = tools.get_state(sid)
    refs = deepcopy(harness._turn_refs)
    recruit = harness.call_tool("get_forecast", {"kind": "recruits", "start_turn": 2})["items"][0]
    failed = harness.call_tool("recruit_adventurer", {"candidate_id": recruit["forecast_ref"]})
    assert not failed["ok"]
    monster = harness.call_tool("get_forecast", {"kind": "monsters", "start_turn": 2})["items"][0]
    failed = harness.call_tool("preview_battle", {"adventurer_id": 1, "monster_id": monster["forecast_ref"]})
    assert not failed["ok"]
    assert tools.get_state(sid) is before
    assert harness._turn_refs == refs


@pytest.mark.parametrize("updates", [
    {"kind": "arena"}, {"kind": []}, {"start_turn": 1}, {"start_turn": True}, {"start_turn": 36},
    {"end_turn": 1}, {"end_turn": 36}, {"end_turn": 3, "entry_index": 1},
    {"entry_index": 0}, {"offset": -1}, {"limit": 11}, {"limit": True},
    {"detail": "full", "limit": 4}, {"detail": "all"}, {"name_contains": " "},
    {"kind": "recruits", "material": "bone"}, {"kind": "recruits", "tier": "boss"},
    {"material": "unobtainium"}, {"tier": "unknown"},
])
def test_invalid_forecast_queries_consume_only_query_budget(game, updates):
    tools, sid, harness = game
    before = tools.get_state(sid)
    events = deepcopy(tools.get_events(sid))
    result = harness.call_tool("get_forecast", {"kind": "monsters", "start_turn": 2, **updates})
    assert not result["ok"]
    assert result["tool_budget"]["used"] == 2
    assert result["tool_budget"]["battle_preview_used"] == 0
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events


def test_forecast_empty_results_and_last_turn_boundary(game):
    tools, sid, harness = game
    result = harness.call_tool("get_forecast", {
        "kind": "recruits", "start_turn": 35, "name_contains": "不存在的职业",
    })
    assert result["ok"] and result["items"] == [] and result["next_offset"] is None
    state = tools.get_state(sid)
    tools._sessions[sid].state = replace(state, turn=35)
    last = TurnToolHarness(tools, sid, max_tool_calls=2)
    assert not last.call_tool("get_forecast", {"kind": "monsters", "start_turn": 36})["ok"]


def test_tools_are_discoverable_and_off_by_default(game):
    tools, sid, harness = game
    schemas = {schema["name"]: schema for schema in harness.tool_schemas()}
    assert {"get_battle_log", "get_forecast"} <= schemas.keys()
    for name in ("get_battle_log", "get_forecast"):
        assert "session_id" not in schemas[name]["parameters"]["properties"]
    assert schemas["get_battle_log"]["parameters"]["required"] == ["battle_id"]
    assert set(schemas["get_battle_log"]["parameters"]["properties"]) == {"battle_id", "detail", "cursor"}
    system = build_system_prompt(battle_log_enabled=True, forecast_enabled=True)
    assert "get_battle_log" in system and "get_forecast" in system
    plain = GuildManagerTools.from_data_dir(ROOT / "data/presets/default")
    plain.start_session("plain")
    schemas = {schema["name"] for schema in plain.list_tool_schemas()}
    assert not {"get_battle_log", "get_forecast"} & schemas
    assert "get_forecast" not in build_system_prompt()
    wrapper = TurnToolHarness(plain, "plain", max_tool_calls=5)
    assert not wrapper.call_tool("get_forecast", {"kind": "monsters", "start_turn": 2})["ok"]
    assert not wrapper.call_tool("get_battle_log", {"battle_id": "missing"})["ok"]


def test_runner_renders_query_outputs_and_retains_previous_battle_on_resume(tmp_path):
    class Agent:
        def __init__(self):
            self.call = 0
            self.saw_log = False

        def respond(self, *, messages, tools):
            self.call += 1
            if self.call == 1:
                assert "get_forecast" in messages[0]["content"]
                assert {"get_battle_log", "get_forecast"} <= {tool["name"] for tool in tools}
                return LlmAgentResponse(tool_calls=(LlmToolCall("recruit_adventurer", {"candidate_id": 1}),))
            if self.call == 2:
                return LlmAgentResponse(tool_calls=(LlmToolCall("get_forecast", {"kind": "recruits", "start_turn": 3}),))
            if self.call == 3:
                assert "未来刷新查询" in messages[-1]["content"]
                return LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]}),))
            if self.call == 4:
                self.battle_id = re.search(r"battle_id=([^\s，。]+)", messages[-1]["content"]).group(1)
                raise RuntimeError("interrupt after first turn")
            if self.call == 5:
                assert f"battle_id={self.battle_id}" in messages[-1]["content"]
                return LlmAgentResponse(tool_calls=(LlmToolCall("get_battle_log", {"battle_id": self.battle_id, "detail": "events"}),))
            self.saw_log = "上轮真实战斗记录" in messages[-1]["content"]
            raise RuntimeError("stop after verifying restored log")

    agent = Agent()
    config = LlmRunConfig(archive_dir=tmp_path, max_turn_retries=0)
    with pytest.raises(RuntimeError, match="interrupt"):
        run_llm_game(agent, data_dir=ROOT / "data/presets/advanced", config=config)
    archives = list(tmp_path.glob("*/replay.json"))
    assert len(archives) == 1
    with pytest.raises(RuntimeError, match="stop after"):
        run_llm_game(agent, data_dir=ROOT / "data/presets/advanced", config=config,
                     resume_archive_dir=archives[0].parent)
    assert agent.saw_log
