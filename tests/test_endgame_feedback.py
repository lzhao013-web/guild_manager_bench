from dataclasses import replace
from pathlib import Path

import pytest

from guild_manager_bench.bench import metrics
from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    TurnToolHarness,
    build_endgame_system_prompt,
    build_turn_prompt,
    run_llm_turn,
)
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.game.loader import load_game_definition


DATA = Path(__file__).resolve().parents[1] / "data/presets/advanced"


def make_session(turn, *, endgame_start_turn=31):
    definition = load_game_definition(DATA)
    definition = replace(
        definition,
        scoring=replace(definition.scoring, rank_max_diff=41, rank_step=10, rank_waves=3),
        llm_tools=replace(definition.llm_tools, endgame_start_turn=endgame_start_turn),
    )
    tools = GuildManagerTools(definition)
    sid = tools.start_session("endgame-feedback")["session_id"]
    for _ in range(1, turn):
        assert tools.end_turn(sid, [])["ok"]
    candidate = tools.get_recruitment(sid)["recruit_candidates"][0]["candidate_id"]
    assert tools.recruit_adventurer(sid, candidate)["ok"]
    return tools, sid


class RecordingAgent:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.requests = []

    def respond(self, *, messages, tools):
        self.requests.append((list(messages), list(tools)))
        name, args = next(self.calls)
        schema = next(tool for tool in tools if tool["name"] == name)
        parameters = schema["parameters"]
        assert set(parameters.get("required", ())).issubset(args), (name, parameters, args)
        if parameters.get("additionalProperties") is False:
            assert set(args).issubset(parameters.get("properties", {}))
        return LlmAgentResponse(tool_calls=(LlmToolCall(name, args),))


def test_endgame_notice_describes_evaluation_without_diversity_advice():
    text = build_endgame_system_prompt(31, 35)
    assert "多类高难度敌人" in text
    assert "满HP/MP" in text
    assert "最佳一一配对" in text
    assert "本回合开始" in text
    assert "多面性" not in text


@pytest.mark.parametrize("turn,start", [(30, 31), (31, None)])
def test_normal_operation_has_no_feedback_or_extra_scoring(monkeypatch, turn, start):
    tools, sid = make_session(turn, endgame_start_turn=start)
    observation = tools.get_observation(sid)["observation"]
    previous_event = next(
        event for event in reversed(tools.get_events(sid)["events"])
        if event["type"] == "turn_ended"
    )
    expected_prompt = build_turn_prompt(
        observation, previous_turn_event=previous_event, endgame_start_turn=start,
    )
    assert build_turn_prompt(
        observation, previous_turn_event=previous_event, endgame_start_turn=start,
        turn_start_rank_score=123.45,
    ) == expected_prompt

    def forbidden(*args, **kwargs):
        pytest.fail("normal-operation turns must not evaluate an automatic rank baseline")

    monkeypatch.setattr(metrics, "compute_rank_score", forbidden)
    agent = RecordingAgent([("end_turn", {"hunts": []})])
    trace = run_llm_turn(agent, tools, sid, config=LlmRunConfig(archive_dir=None))
    assert trace.status == "completed"
    assert trace.prompt == expected_prompt
    messages, schemas = agent.requests[0]
    assert all("preview_team_power" != schema["name"] for schema in schemas)
    assert "当前终局评分" not in trace.prompt
    assert "最佳一一配对" not in trace.prompt
    assert all("rank_score" not in message["content"] for message in messages)


@pytest.mark.parametrize("turn", [31, 35])
def test_endgame_baseline_reaches_model_without_spending_a_tool_call(turn):
    tools, sid = make_session(turn)
    expected = round(metrics.compute_rank_score(tools.definition, tools.get_state(sid)), 2)
    agent = RecordingAgent([("preview_team_power", {"operations": []}), ("end_turn", {"hunts": []})])
    trace = run_llm_turn(
        agent, tools, sid,
        config=LlmRunConfig(max_tool_calls_per_turn=1, archive_dir=None),
    )
    assert trace.status == "completed"
    assert f"当前终局评分：{expected:.2f}" in trace.prompt
    messages, schemas = agent.requests[0]
    assert any(f"当前终局评分：{expected:.2f}" in message["content"] for message in messages)
    for schema in schemas:
        parameters = schema["parameters"]
        assert "session_id" not in parameters.get("required", ())
        assert "session_id" not in parameters.get("properties", {})
    preview_schema = next(schema for schema in schemas if schema["name"] == "preview_team_power")
    assert set(preview_schema["parameters"]["properties"]) == {"operations"}
    assert trace.tool_calls[0].arguments == {"operations": []}
    preview = trace.tool_calls[0].result
    assert preview["current_rank_score"] == expected
    assert preview["projected_rank_score"] == expected
    assert "turn_start_rank_score" not in preview
    assert preview["rank_score_delta"] == 0
    assert preview["tool_budget"]["used"] == 1
    assert any("相对调用前）：+0.00" in message.get("content", "") for message in trace.messages)
    assert trace.tool_calls[-1].result["tool_budget"]["used"] == 1


def test_delta_compares_plan_to_current_state_not_turn_start():
    tools, sid = make_session(31)
    baseline = round(metrics.compute_rank_score(tools.definition, tools.get_state(sid)), 2)
    harness = TurnToolHarness(tools, sid, max_tool_calls=10, endgame_start_turn=31)
    assert harness.call_tool("recruit_adventurer", {"candidate_id": 2})["ok"]
    before_preview = tools.get_state(sid)
    expected = round(metrics.compute_rank_score(tools.definition, before_preview), 2)
    preview = harness.call_tool("preview_team_power", {"operations": []})
    assert tools.get_state(sid) == before_preview
    assert preview["current_rank_score"] == expected > baseline
    assert preview["projected_rank_score"] == expected
    assert preview["rank_score_delta"] == 0
    text = _format_tool_result_for_model("preview_team_power", preview)
    assert "真实状态未改变" in text
    assert "累计变化" not in text

    dismissal = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "dismiss_adventurer", "arguments": {"adventurer_id": 2}},
    ]})
    assert dismissal["current_rank_score"] == expected
    assert dismissal["projected_rank_score"] == baseline
    assert dismissal["rank_score_delta"] == round(baseline - expected, 2) < 0
    assert tools.get_state(sid) is before_preview
    assert harness.call_tool("dismiss_adventurer", {"adventurer_id": 2})["ok"]
    restored = harness.call_tool("preview_team_power", {"operations": []})
    assert restored["rank_score_delta"] == 0
    assert restored["current_rank_score"] == baseline

    assert harness.call_tool("dismiss_adventurer", {"adventurer_id": 1})["ok"]
    empty = harness.call_tool("preview_team_power", {"operations": []})
    assert empty["current_rank_score"] == 0
    assert empty["projected_rank_score"] == 0
    assert empty["rank_score_delta"] == 0
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]

    next_turn = TurnToolHarness(tools, sid, max_tool_calls=1, endgame_start_turn=31)
    next_preview = next_turn.call_tool("preview_team_power", {"operations": []})
    assert next_preview["current_rank_score"] == 0
    assert next_preview["rank_score_delta"] == 0


def test_empty_team_still_shows_zero_automatic_baseline():
    tools, sid = make_session(31)
    adventurer_id = tools.get_state(sid).adventurers[0].adventurer_id
    assert tools.dismiss_adventurer(sid, adventurer_id)["ok"]
    agent = RecordingAgent([("end_turn", {"hunts": []})])
    trace = run_llm_turn(
        agent, tools, sid,
        config=LlmRunConfig(max_tool_calls_per_turn=0, archive_dir=None),
    )
    assert trace.status == "completed"
    assert "当前终局评分：0.00" in trace.prompt
    assert trace.tool_calls[0].result["tool_budget"]["used"] == 0


def test_team_power_preview_is_still_rejected_before_endgame():
    tools, sid = make_session(30)
    harness = TurnToolHarness(tools, sid, max_tool_calls=1, endgame_start_turn=31)
    result = harness.call_tool("preview_team_power", {"operations": []})
    assert result["ok"] is False
    assert "仅在终局阶段" in result["error"]
    assert "rank_score" not in result
    assert "turn_start_rank_score" not in result


def test_failed_team_preview_does_not_report_a_fabricated_delta(monkeypatch):
    tools, sid = make_session(31)
    harness = TurnToolHarness(tools, sid, max_tool_calls=1, endgame_start_turn=31)
    monkeypatch.setattr(tools, "preview_team_power", lambda session_id, operations: {"ok": False, "error": "评分失败"})
    result = harness.call_tool("preview_team_power", {"operations": []})
    assert result["ok"] is False
    assert "rank_score_delta" not in result
    assert result["tool_budget"]["used"] == 1
