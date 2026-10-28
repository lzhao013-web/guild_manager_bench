from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re

import pytest

from guild_manager_bench.bench.llm.archive import _message_steps
from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.runner import (
    LlmAgentResponse, LlmRunConfig, LlmToolCall, _compute_run_stats,
    _format_tool_result_for_model, _is_retryable, _replay_confirmed_tool_result,
    _traces_from_replay, rebuild_replay_observations, replay_observations_complete,
    run_llm_turn,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.llm.trace import LlmGameRun
from guild_manager_bench.game.actions import CraftAction, DismissAction
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.session import GameSession


DATA = Path(__file__).resolve().parents[1] / "data/presets/default"


@pytest.fixture
def game():
    definition = load_game_definition(DATA)
    definition = replace(definition, starting_gold=1000,
                         llm_tools=replace(definition.llm_tools, endgame_start_turn=None))
    tools = GuildManagerTools(definition)
    sid = tools.start_session("plans")["session_id"]
    candidate = tools.get_state(sid).recruit_candidates[0].candidate_id
    assert tools.recruit_adventurer(sid, candidate)["ok"]
    return tools, sid


def equipment_plan():
    return [
        {"tool": "craft_equipment", "arguments": {"recipe_id": 1}, "as": "blade"},
        {"tool": "equip_item", "arguments": {
            "adventurer_id": 1, "equipment_instance_id": "$blade",
        }},
    ]


def test_execution_is_free_once_and_records_real_actions(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=3)
    before = tools.get_state(sid)
    original_events = deepcopy(tools.get_events(sid))
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert preview["can_execute"] is True
    assert preview["required_calls"] == preview["remaining_calls"] == 2
    assert preview["tool_budget"]["used"] == 1
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == original_events
    assert "eq_0001" not in str(preview)

    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"] is True
    assert result["already_executed"] is False
    assert result["tool_budget"]["used"] == 1
    assert result["tool_budget"]["remaining"] == 2
    assert [event["action"]["type"] for event in result["events"]] == ["craft", "equip"]
    after = tools.get_state(sid)
    assert after.adventurers[0].equipment.items[0].instance_id == after.equipment_inventory[0].instance_id
    assert "eq_0001" in harness._turn_refs["equipment"]
    assert "未扣调用预算" in _format_tool_result_for_model("execute_plan", result)
    saved_events = deepcopy(tools.get_events(sid))

    # 模拟客户端丢失响应后的重试，不重复扣资源或追加事件。
    repeated = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert repeated["ok"] and repeated["already_executed"]
    assert repeated["events"] == result["events"]
    assert repeated["tool_budget"]["used"] == 1
    assert tools.get_state(sid) is after
    assert tools.get_events(sid) == saved_events
    assert "未重复执行" in _format_tool_result_for_model("execute_plan", repeated)
    harness.call_tool("get_party")
    harness.call_tool("get_inventory")
    exhausted_retry = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert exhausted_retry["ok"] and exhausted_retry["already_executed"]
    assert exhausted_retry["tool_budget"]["remaining"] == 0
    assert tools.get_events(sid) == saved_events


def test_query_can_leave_enough_budget_or_make_execution_impossible(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=4)
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert preview["remaining_calls"] == 3
    assert harness.call_tool("get_inventory")["tool_budget"]["remaining"] == 2
    assert harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})["ok"]

    slot = tools.get_state(sid).adventurers[0].equipment.items[0].slot
    preview = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "unequip_item", "arguments": {"adventurer_id": 1, "slot": slot}},
        {"tool": "equip_item", "arguments": {"adventurer_id": 1, "equipment_instance_id": 1}},
    ]})
    assert preview["ok"] and not preview["can_execute"]
    assert preview["remaining_calls"] == 1
    before, events = tools.get_state(sid), deepcopy(tools.get_events(sid))
    for _ in range(2):
        rejected = harness.call_tool("execute_plan", {
            "plan_id": preview["plan_id"], "remaining_calls": 999,
        })
        assert rejected["ok"] is False
        assert rejected["required_calls"] == 2
        assert rejected["remaining_calls"] == 1  # 模型不能伪造余额。
        assert rejected["tool_budget"]["used"] == 3
        assert "不要原样重试" in rejected["error"]
        assert tools.get_state(sid) is before
        assert tools.get_events(sid) == events


def test_query_after_executable_preview_can_invalidate_budget_without_a_fee(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=3)
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert preview["can_execute"] is True
    harness.call_tool("get_party")
    before = tools.get_state(sid)
    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"] is False
    assert result["remaining_calls"] == 1
    assert result["tool_budget"]["used"] == 2
    assert tools.get_state(sid) is before


@pytest.mark.parametrize("mutation", ["harness", "direct"])
def test_state_change_expires_receipt_even_when_budget_is_sufficient(game, mutation):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=10)
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    if mutation == "harness":
        assert harness.call_tool("craft_equipment", {"recipe_id": 1})["ok"]
    else:
        recipe = tools.definition.content.crafting_recipes[0].recipe_id
        assert tools.craft_equipment(sid, recipe)["ok"]
    before, events, used = tools.get_state(sid), deepcopy(tools.get_events(sid)), harness.budget.used
    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"] is False
    assert "状态已改变" in result["error"]
    assert result["tool_budget"]["used"] == used
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events


def test_failed_real_action_does_not_expire_receipt(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=4)
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert harness.call_tool("craft_equipment", {"recipe_id": "missing"})["ok"] is False
    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"] is True
    assert result["tool_budget"]["used"] == 2


def test_other_preview_does_not_expire_receipt_but_execution_does(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=5)
    first = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    second = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert first["plan_id"] != second["plan_id"]
    assert harness.call_tool("execute_plan", {"plan_id": first["plan_id"]})["ok"]
    rejected = harness.call_tool("execute_plan", {"plan_id": second["plan_id"]})
    assert rejected["ok"] is False
    assert "状态已改变" in rejected["error"]
    assert rejected["tool_budget"]["used"] == 2


def test_zero_balance_and_foreign_or_expired_receipts_cannot_execute(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=1)
    preview = harness.call_tool("preview_team_power", {"operations": equipment_plan()})
    assert preview["ok"] and not preview["can_execute"]
    assert preview["remaining_calls"] == 0
    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"] is False and result["required_calls"] == 2
    assert result["tool_budget"]["used"] == 1
    assert result["tool_budget"]["allowed_tools"] == ["end_turn"]

    other = tools.start_session("other")["session_id"]
    foreign = TurnToolHarness(tools, other, max_tool_calls=20)
    assert foreign.call_tool("execute_plan", {"plan_id": preview["plan_id"]})["ok"] is False
    assert foreign.budget.used == 0
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]
    next_turn = TurnToolHarness(tools, sid, max_tool_calls=20)
    expired = next_turn.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert expired["ok"] is False and "跨回合" in expired["error"]
    assert next_turn.budget.used == 0


def test_failed_simulation_costs_one_and_cannot_embed_executor(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=2)
    before, events = tools.get_state(sid), deepcopy(tools.get_events(sid))
    result = harness.call_tool("preview_team_power", {"operations": [
        equipment_plan()[0], {"tool": "execute_plan", "arguments": {"plan_id": "fake"}},
    ]})
    assert result["ok"] is False
    assert "第 2 步" in result["error"]
    assert "plan_id" not in result
    assert result["tool_budget"]["used"] == 1
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    invalid = harness.call_tool("execute_plan", {"plan_id": "fake"})
    assert invalid["ok"] is False and invalid["tool_budget"]["used"] == 1


def test_session_batch_failure_does_not_commit_state_or_events(game):
    tools, sid = game
    session = GameSession(tools.definition, state=tools.get_state(sid))
    before, events = session.state, deepcopy(session.events)
    recipe = tools.definition.content.crafting_recipes[0].recipe_id
    with pytest.raises(ValueError):
        session.apply_preparations((CraftAction(recipe), DismissAction("missing")))
    assert session.state is before
    assert session.events == events
    event = session.apply_preparation(CraftAction(recipe))
    assert event.sequence == events[-1].sequence + 1


def test_execution_resolves_recruited_and_crafted_aliases_together(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=6)
    operations = [
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 1}},
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "hero"},
        {"tool": "allocate_experience", "arguments": {"adventurer_id": "$hero", "amount": 0}},
        equipment_plan()[0],
        {"tool": "equip_item", "arguments": {
            "adventurer_id": "$hero", "equipment_instance_id": "$blade",
        }},
    ]
    preview = harness.call_tool("preview_team_power", {"operations": operations})
    assert preview["ok"] and preview["can_execute"]
    assert "recruit_0002" not in str(preview)
    result = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert result["ok"]
    assert result["tool_budget"]["remaining"] == 5
    state = tools.get_state(sid)
    assert len(state.adventurers) == 1
    assert state.adventurers[0].adventurer_id == "recruit_0002"
    assert state.adventurers[0].equipment.items[0].instance_id == "eq_0001"
    assert harness._turn_refs["adventurer"]["recruit_0002"] == 2
    assert all(event["type"] == "preparation_applied" for event in result["events"])


def test_recovery_allocates_refs_only_for_final_batch_objects(game):
    tools, sid = game
    harness = TurnToolHarness(tools, sid, max_tool_calls=5)
    operations = [
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "temporary"},
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": "$temporary"}},
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 2}},
    ]
    preview = harness.call_tool("preview_team_power", {"operations": operations})
    assert preview["ok"]
    applied = harness.call_tool("execute_plan", {"plan_id": preview["plan_id"]})
    assert applied["ok"]
    assert harness._turn_refs["adventurer"]["recruit_0003"] == 2
    dismissed = harness.call_tool("dismiss_adventurer", {"adventurer_id": 2})
    assert dismissed["ok"]

    fresh = GuildManagerTools(tools.definition)
    fresh_sid = fresh.start_session("transient-recovery")["session_id"]
    candidate = fresh.get_state(fresh_sid).recruit_candidates[0].candidate_id
    assert fresh.recruit_adventurer(fresh_sid, candidate)["ok"]
    restored = TurnToolHarness(fresh, fresh_sid, max_tool_calls=100)
    for name, arguments, result in [
        ("execute_plan", {"plan_id": preview["plan_id"]}, applied),
        ("dismiss_adventurer", {"adventurer_id": 2}, dismissed),
    ]:
        _replay_confirmed_tool_result(restored, {
            "type": "tool_result", "name": name, "arguments": arguments, "result": result,
        })
    assert fresh.get_state(fresh_sid) == tools.get_state(sid)
    assert restored._turn_refs == harness._turn_refs


def test_runner_archive_restore_and_stats_preserve_batch_without_rerunning_preview(monkeypatch):
    tools = GuildManagerTools.from_data_dir(DATA)
    sid = tools.start_session("batch-trace")["session_id"]
    operations = [
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "hero"},
        equipment_plan()[0],
        {"tool": "equip_item", "arguments": {
            "adventurer_id": "$hero", "equipment_instance_id": "$blade",
        }},
    ]

    class Agent:
        phase = 0

        def respond(self, *, messages, tools):
            self.phase += 1
            if self.phase == 1:
                call = LlmToolCall("preview_team_power", {"operations": operations})
            elif self.phase == 2:
                plan_id = re.search(r"plan_id=([0-9a-f]+)", messages[-1]["content"])[1]
                return LlmAgentResponse(tool_calls=(
                    LlmToolCall("execute_plan", {"plan_id": plan_id}),
                    LlmToolCall("execute_plan", {"plan_id": plan_id}),
                ))
            else:
                call = LlmToolCall("end_turn", {"hunts": []})
            return LlmAgentResponse(tool_calls=(call,))

    trace = run_llm_turn(Agent(), tools, sid, config=LlmRunConfig(
        max_tool_calls_per_turn=4, archive_dir=None,
    ))
    assert trace.status == "completed"
    assert not _is_retryable(trace)
    assert "_observation_after" in trace.tool_calls[1].result
    assert all(call.result["tool_budget"]["used"] == 1 for call in trace.tool_calls)
    steps = _message_steps(trace.messages, trace.tool_calls, trace.model_responses)
    execution_steps = [step for step in steps if step.get("name") == "execute_plan"]
    assert len(execution_steps[0]["result"]["events"]) == 3
    assert execution_steps[1]["result"]["already_executed"] is True
    replay = {"turns": [{"turn": 1, "status": "completed", "steps": steps}]}
    assert not replay_observations_complete(replay)

    def forbidden(*args, **kwargs):
        pytest.fail("restoration must replay committed actions, not rerun previews")

    monkeypatch.setattr(GuildManagerTools, "preview_team_power", forbidden)
    rebuilt = rebuild_replay_observations(replay, data_dir=DATA)
    assert replay_observations_complete(rebuilt)
    expected = tools.get_observation(sid)["observation"]
    restored = rebuilt["turns"][0]["steps"][-1]["observation_after"]
    for key in ("gold", "adventurers", "equipment_inventory", "materials"):
        assert restored[key] == expected[key]

    # 续跑使用同一恢复入口，凭据无需跨进程持久化。
    fresh = GuildManagerTools.from_data_dir(DATA)
    restored_sid = fresh.start_session("restored")["session_id"]
    harness = TurnToolHarness(fresh, restored_sid, max_tool_calls=100)
    for step in steps:
        _replay_confirmed_tool_result(harness, step)
    assert fresh.get_state(restored_sid) == tools.get_state(sid)

    original_run = LlmGameRun("completed", sid, expected, [trace])
    resumed_run = LlmGameRun("completed", sid, expected, _traces_from_replay(replay))
    original_stats = _compute_run_stats(original_run)["game_actions"]
    resumed_stats = _compute_run_stats(resumed_run)["game_actions"]
    for key in ("total_recruits", "total_equipment_crafted", "total_equips"):
        assert original_stats[key] == resumed_stats[key] == 1
