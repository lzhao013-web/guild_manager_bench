import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from guild_manager_bench.bench.llm import (
    GuildManagerTools, LlmAgentResponse, LlmRunConfig, LlmToolCall, run_llm_turn,
)
from guild_manager_bench.bench.llm.trace import LlmGameRun
from guild_manager_bench.cli_output import RunReporter
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import describe_data_source


DATA = Path(__file__).resolve().parents[1] / "data" / "presets" / "default"


@pytest.fixture
def recorded_run():
    definition = load_game_definition(DATA)
    definition = replace(definition, rules=replace(definition.rules, max_turns=1))
    tools = GuildManagerTools(definition)
    initial = tools.start_session("display-test")["observation"]
    data = describe_data_source(DATA)
    data.update(game_seed=initial["seed"], scoring_seed=initial["scoring"]["seed"])
    events = [{
        "type": "run_started", "session_id": "display-test", "data": data,
        "config": {"max_tool_calls_per_turn": 3}, "observation": initial,
    }]
    candidate = initial["recruit_candidates"][0]

    class Agent:
        def __init__(self):
            self.index = 0

        def respond(self, **kwargs):
            responses = [
                LlmToolCall("get_monsters", {}),
                LlmToolCall("recruit_adventurer", {"candidate_id": candidate["candidate_id"]}),
                LlmToolCall("equip_item", {"adventurer_id": 1, "equipment_instance_id": "missing-item"}),
                LlmToolCall("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]}),
            ]
            call = responses[self.index]
            self.index += 1
            return LlmAgentResponse(
                text="模型正文 [bold]保持原样[/bold]",
                assistant_metadata={"reasoning_content": "API提供的推理内容"},
                tool_calls=(call,), usage={"prompt_tokens": 100, "completion_tokens": 20},
            )

    agent = Agent()
    trace = run_llm_turn(
        agent, tools, "display-test", config=LlmRunConfig(max_tool_calls_per_turn=3),
        event_sink=events.append,
    )
    assert trace.status == "completed"
    trace.rank_score = 42.25
    events += [
        {"type": "scoring_started", "scope": "turn", "turn": 1},
        {"type": "turn_scored", "turn": 1, "rank_score": 42.25, "duration_ms": 12},
    ]
    final = tools.get_observation("display-test")["observation"]
    run = LlmGameRun(
        status="completed", session_id="display-test", final_observation=final,
        turns=[trace], score={
            "rank_score": 42.25, "chosen_win_rate": 0.5,
            "rank_score_per_adventurer": [{"name": candidate["name"], "rank_score": 42.25, "rank_score_share": 1}],
        },
    )
    return events, run, candidate, agent


def reporter_for(stream, *, mode="watch", width=100, terminal=False, **kwargs):
    console = Console(file=stream, width=width, force_terminal=terminal, color_system="standard" if terminal else None)
    reporter = RunReporter(console, mode=mode, provider="openai", **kwargs)
    reporter.configure(SimpleNamespace(model="model-name", timeout=42, reasoning_effort="high", api_key="secret-key", base_url="https://example.com/v1"), streaming=True)
    return reporter


def test_watch_output_describes_game_changes_and_actual_battles(recorded_run):
    events, run, candidate, agent = recorded_run
    stream = io.StringIO()
    reporter = reporter_for(stream)
    for event in events:
        reporter.on_event(event)
    reporter.finish(run, wall_time=12)
    output = stream.getvalue()
    assert "公会经营实况" in output
    assert "超时 42s" in output and "推理强度 high" in output
    assert candidate["name"] in output
    assert f"(-{candidate['recruit_gold']})" in output
    assert "讨伐战报" in output and "资源净变化" in output
    assert "Rank Score 42.2" in output
    assert "终局 Arena 胜率" in output and "讨伐胜率" in output
    assert "未找到装备" in output
    assert "金币支出" in output
    assert "prompt_tokens" not in output and "stats json" not in output
    assert "模型正文" not in output and "API提供的推理内容" not in output
    assert "\x1b" not in output
    assert agent.index == 4
    assert "失败 1 次" in output


@pytest.mark.parametrize("mode", ["quiet", "json"])
def test_final_only_modes_do_not_print_live_events(recorded_run, mode):
    events, run, _, _ = recorded_run
    stream = io.StringIO()
    reporter = reporter_for(stream, mode=mode, width=32)
    for event in events:
        reporter.on_event(event)
    assert stream.getvalue() == ""
    reporter.finish(run, wall_time=12)
    output = stream.getvalue()
    if mode == "json":
        report = json.loads(output)
        assert report["status"] == "completed"
        assert report["stats"]["model_interaction"]["total_turns_completed"] == 1
        assert report["usage_coverage"] == {"responses": 4, "input": 4, "output": 4}
        assert report["stats"]["token_usage"]["input_tokens"] == 400
        assert report["gold_spent"] > 0
        assert report["score_history"] == [{"turn": 1, "rank_score": 42.25}]
        assert "secret-key" not in output
    else:
        assert "公会经营完成" in output
        assert "公会经营实况" not in output
        assert "正在等待模型响应" not in output


@pytest.mark.parametrize("option, shown, hidden", [
    ("show_model_text", "模型正文", "API提供的推理内容"),
    ("show_reasoning", "API提供的推理内容", "模型正文"),
])
def test_model_content_is_opt_in_and_printed_once_per_response(recorded_run, option, shown, hidden):
    events, _, _, _ = recorded_run
    response = next(e for e in events if e["type"] == "model_response")
    stream = io.StringIO()
    reporter = reporter_for(stream, **{option: True})
    reporter.on_event(events[0])
    reporter.on_event({"type": "model_delta", "text": response["text"]})
    reporter.on_event({"type": "model_reasoning_delta", "text": response["assistant_metadata"]["reasoning_content"]})
    reporter.on_event(response)
    output = stream.getvalue()
    assert shown in output
    assert hidden not in output
    assert output.count("保持原样") <= 1
    assert output.count("API提供的推理内容") <= 1
    if option == "show_model_text":
        assert "[bold]保持原样[/bold]" in output


def test_verbose_exposes_parameters_and_results_but_not_model_content(recorded_run):
    events, _, _, _ = recorded_run
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="verbose")
    for event in events:
        reporter.on_event(event)
    output = stream.getvalue()
    assert "可讨伐目标" in output and "工具返回" in output
    assert "candidate_id" in output and "equipment_instance_id" in output
    assert "API提供的推理内容" not in output
    assert "模型正文" not in output


def test_debug_redacts_configured_api_key_and_preserves_literal_markup(recorded_run):
    events, _, _, _ = recorded_run
    response = dict(next(e for e in events if e["type"] == "model_response"))
    response["text"] = "secret-key [red]literal[/red] \x1b[2J"
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="debug")
    reporter.on_event(events[0])
    reporter.on_event(response)
    output = stream.getvalue()
    assert "secret-key" not in output
    assert "[已隐藏]" in output
    assert "[red]literal[/red]" in output
    assert "model_response" in output
    assert "API提供的推理内容" in output
    assert "\x1b" not in output


def test_narrow_terminal_wraps_chinese_and_long_names(recorded_run):
    events, run, _, _ = recorded_run
    run.final_observation["adventurers"][0]["name"] = "很长的冒险者名称" * 20
    stream = io.StringIO()
    reporter = reporter_for(stream, width=40)
    for event in events:
        reporter.on_event(event)
    reporter.finish(run, wall_time=12)
    from rich.cells import cell_len
    assert all(cell_len(line) <= 40 for line in stream.getvalue().splitlines())


def test_live_status_tracks_budget_and_restores_cursor_on_interrupt(recorded_run):
    events, _, _, _ = recorded_run
    stream = io.StringIO()
    reporter = reporter_for(stream, terminal=True)
    with pytest.raises(KeyboardInterrupt):
        with reporter:
            for event in events:
                reporter.on_event(event)
            reporter.on_event({"type": "model_request", "step": 5})
            reporter.on_event({"type": "model_delta", "text": "正在生成"})
            reporter.live.refresh()
            raise KeyboardInterrupt
    output = stream.getvalue()
    assert "正在接收模型输出" in output
    assert "非 end_turn 预算 3/3" in output
    assert output.endswith("\x1b[?25h") or "\x1b[?25h" in output
    assert reporter.live is None


def test_resume_counts_completed_turns_not_failed_attempts_and_keeps_score_delta(recorded_run):
    events, run, _, _ = recorded_run
    from guild_manager_bench.bench.llm.runner import _compute_run_stats
    stats = _compute_run_stats(run)
    stats["model_interaction"]["total_turns_failed"] = 2
    stream = io.StringIO()
    reporter = reporter_for(stream)
    reporter.on_event(events[0])
    reporter.on_event({
        "type": "run_resumed", "archive": {"directory": "runs/path with space"},
        "restored_turns": 3, "stats": stats, "last_rank_score": 40.0,
        "restored_observation": {"turn": 2},
    })
    for event in events[1:]:
        reporter.on_event(event)
    reporter.finish(run, wall_time=2)
    output = stream.getvalue()
    assert "已完成 1 回合" in output
    assert "历史失败尝试 2 次" in output
    assert "40.0 → 42.2 (+2.2)" in output
    assert "含续跑历史" in output
    command = reporter.resume_command()
    assert "--provider openai" in command
    assert "--reasoning-effort high" in command
    assert "--game-seed" in command and "--scoring-seed" in command
    assert "'runs/path with space'" in command
    assert "secret-key" not in command


def test_json_abort_has_no_human_output_and_does_not_claim_missing_checkpoint(recorded_run):
    events, _, _, _ = recorded_run
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="json")
    reporter.on_event(events[0])
    reporter.on_event({"type": "run_archived", "archive": {"directory": "runs/failed"}})
    reporter.on_event({"type": "checkpoint_started"})
    reporter.on_event({"type": "checkpoint_failed", "error": "permission denied"})
    reporter.abort("secret-key unavailable")
    output = json.loads(stream.getvalue())
    assert output["resume_command"] is None
    assert output["status"] == "failed"
    assert output["failure_reason"] == "[已隐藏] unavailable"


def test_missing_usage_is_not_presented_as_measured_zero(recorded_run):
    events, run, _, _ = recorded_run
    for turn in run.turns:
        for record in turn.model_responses:
            record.usage = {}
    stream = io.StringIO()
    reporter = reporter_for(stream)
    reporter.on_event(events[0])
    reporter.finish(run, wall_time=1)
    output = stream.getvalue()
    assert "API 未提供" in output
    assert "usage 0/4" not in output


@pytest.mark.parametrize("usage, expected", [
    ({"input_tokens": 0, "output_tokens": 0, "prompt_tokens_details": {"cached_tokens": 12}}, {"read_tokens": 48}),
    ({"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 10}}, {"read_tokens": 40}),
    ({"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}, {"read_tokens": 40, "created_tokens": 20}),
])
def test_json_reports_provider_cache_fields_and_measured_zero(recorded_run, usage, expected):
    events, run, _, _ = recorded_run
    for turn in run.turns:
        for record in turn.model_responses:
            record.usage = usage
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="json")
    reporter.on_event(events[0])
    reporter.finish(run, wall_time=1)
    report = json.loads(stream.getvalue())
    assert report["cache_usage"] == expected
    assert report["usage_coverage"] == {"responses": 4, "input": 4, "output": 4}


def test_old_archive_without_spending_snapshots_reports_unknown(recorded_run):
    events, run, _, _ = recorded_run
    for call in run.turns[0].tool_calls:
        call.result.pop("_observation_after", None)
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="json")
    reporter.on_event(events[0])
    reporter.finish(run, wall_time=1)
    report = json.loads(stream.getvalue())
    assert report["gold_spent"] is None


def test_successful_zero_cost_actions_are_not_treated_as_missing(recorded_run):
    events, run, _, _ = recorded_run
    run.turns[0].tool_calls = [c for c in run.turns[0].tool_calls if c.name == "end_turn"]
    stream = io.StringIO()
    reporter = reporter_for(stream, mode="json")
    reporter.on_event(events[0])
    reporter.finish(run, wall_time=1)
    assert json.loads(stream.getvalue())["gold_spent"] == 0


def test_resume_command_omits_credentials_embedded_in_endpoint(recorded_run):
    events, _, _, _ = recorded_run
    reporter = RunReporter(Console(file=io.StringIO()), provider="openai")
    reporter.configure(SimpleNamespace(
        model="model-name", timeout=42, api_key="secret-key",
        base_url="https://user:password@example.com/v1?api_key=secret-key",
    ), streaming=True)
    reporter.on_event(events[0])
    reporter.on_event({"type": "run_archived", "archive": {"directory": "runs/test"}})
    reporter.on_event({"type": "checkpoint_started"})
    reporter.on_event({"type": "checkpoint_completed"})
    command = reporter.resume_command()
    assert "password" not in command and "secret-key" not in command
    assert "--base-url" not in command


def test_cp936_output_replaces_unencodable_model_text_without_stopping_game(recorded_run):
    events, _, _, _ = recorded_run
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk")
    reporter = reporter_for(stream, show_model_text=True)
    response = dict(next(e for e in events if e["type"] == "model_response"))
    response["text"] = "模型正文：\U0001f9d9"
    reporter.on_event(events[0])
    reporter.on_event(response)
    stream.flush()
    assert "模型正文：?" in raw.getvalue().decode("gbk")
