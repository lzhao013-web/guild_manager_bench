import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    build_turn_prompt,
    run_llm_game,
    run_llm_turn,
)
from guild_manager_bench.game.state import LlmToolRules


class StaticAgent:
    def __init__(self, response: LlmAgentResponse) -> None:
        self.response = response

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        return self.response


class SequenceAgent:
    def __init__(self, responses: Sequence[LlmAgentResponse]) -> None:
        self.responses = list(responses)
        self.index = 0

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        if self.index >= len(self.responses):
            return self.responses[-1]
        response = self.responses[self.index]
        self.index += 1
        return response


def test_run_llm_game_completes_when_agent_ends_each_turn() -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=None),
    )

    assert run.status == "completed"
    assert run.final_observation["finished"] is True
    assert len(run.turns) == run.final_observation["max_turns"]
    assert all(turn.status == "completed" for turn in run.turns)


def test_illegal_action_error_is_returned_to_model_before_recovery() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("illegal-action")["session_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(
                tool_calls=(LlmToolCall("craft_equipment", {"recipe_id": "missing"}),)
            ),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, session_id, config=LlmRunConfig())

    assert trace.status == "completed"
    assert trace.tool_calls[0].result["ok"] is False
    assert "unknown recipe" in trace.tool_calls[0].result["error"]
    assert "unknown recipe" in trace.messages[2]["content"]


def test_empty_responses_fail_after_retry_limit() -> None:
    agent = StaticAgent(LlmAgentResponse(text="I will think."))

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_empty_responses=2, archive_dir=None),
    )

    assert run.status == "failed"
    assert run.failure_reason == "empty_response_limit"


def test_empty_response_counter_resets_after_tool_call() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("empty-reset")["session_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(text="thinking"),
            LlmAgentResponse(tool_calls=(LlmToolCall("get_observation", {}),)),
            LlmAgentResponse(text="thinking again"),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(
        agent,
        tools,
        session_id,
        config=LlmRunConfig(max_empty_responses=2),
    )

    assert trace.status == "completed"


def test_invalid_end_turn_fails_after_attempt_limit() -> None:
    agent = StaticAgent(
        LlmAgentResponse(
            tool_calls=(
                LlmToolCall(
                    "end_turn",
                    {
                        "hunts": [
                            {
                                "adventurer_id": "vanguard",
                                "monster_id": "turn_1_monster_1",
                            },
                            {
                                "adventurer_id": "vanguard",
                                "monster_id": "turn_1_monster_2",
                            },
                        ]
                    },
                ),
            )
        )
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_end_turn_attempts=2, archive_dir=None),
    )

    assert run.status == "failed"
    assert run.failure_reason == "end_turn_attempt_limit"
    assert "duplicate adventurer hunt" in run.turns[0].tool_calls[-1].result["error"]


def test_budget_exhaustion_without_end_turn_fails() -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("get_observation", {}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(
            max_tool_calls_per_turn=1,
            max_invalid_tool_responses=2,
            archive_dir=None,
        ),
    )

    assert run.status == "failed"
    assert run.failure_reason == "tool_budget_exhausted_without_end_turn"


def test_run_llm_turn_emits_debug_events() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("debug-events")["session_id"]
    events: list[dict[str, Any]] = []
    agent = SequenceAgent(
        (
            LlmAgentResponse(tool_calls=(LlmToolCall("get_observation", {}),)),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, session_id, event_sink=events.append)

    assert trace.status == "completed"
    event_types = [event["type"] for event in events]
    assert "turn_started" in event_types
    assert "model_request" in event_types
    model_response = next(event for event in events if event["type"] == "model_response")
    assert model_response["assistant_metadata"] == {}
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "turn_completed" in event_types
    model_requests = [event for event in events if event["type"] == "model_request"]
    tool_message = model_requests[1]["request"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"].startswith("OK get_observation")
    assert not tool_message["content"].lstrip().startswith("{")
    assert "seed 20260524" in tool_message["content"]
    assert "scoring_seed 20260526" in tool_message["content"]
    assert "adventurers:" in tool_message["content"]
    assert "skills power_strike active" in tool_message["content"]
    assert "effects dmgx2" in tool_message["content"]


def test_turn_prompt_includes_compact_skill_summaries() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    observation = tools.start_session("skill-prompt")["observation"]

    prompt = build_turn_prompt(observation, max_tool_calls=3)

    assert "技能 power_strike active" in prompt
    assert "随机种子：游戏 20260524，评分 20260526" in prompt
    assert "effects dmgx1.8" in prompt


def test_preview_battle_tool_returns_compact_model_visible_text() -> None:
    base_tools = GuildManagerTools.from_data_dir(_data_dir())
    tools = GuildManagerTools(
        replace(
            base_tools.definition,
            llm_tools=LlmToolRules(expose_battle_preview=True),
        )
    )
    observation = tools.start_session("preview-text")["observation"]
    monster_id = observation["monsters"][0]["monster_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(
                tool_calls=(
                    LlmToolCall(
                        "preview_battle",
                        {
                            "adventurer_id": "vanguard",
                            "monster_id": monster_id,
                        },
                    ),
                )
            ),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, "preview-text")

    assert trace.status == "completed"
    tool_message = next(
        message
        for message in trace.messages
        if message.get("role") == "tool" and message.get("name") == "preview_battle"
    )
    assert tool_message["content"].startswith("OK preview_battle")
    assert "resources:" in tool_message["content"]
    assert "combat:" in tool_message["content"]
    assert "budget: 1/" in tool_message["content"]
    assert not tool_message["content"].lstrip().startswith("{")


def test_run_llm_game_archives_trace_and_replay(tmp_path) -> None:
    agent = StaticAgent(
        LlmAgentResponse(
            tool_calls=(LlmToolCall("end_turn", {"hunts": []}),),
            raw={"id": "response_1"},
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
            },
        )
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=tmp_path),
    )

    assert run.status == "completed"
    assert run.score is not None
    assert 0 <= run.score["score"] <= 100
    assert run.archive_dir is not None
    archive_dir = Path(run.archive_dir)
    trace_path = archive_dir / "trace.jsonl"
    replay_path = archive_dir / "replay.json"
    assert trace_path.exists()
    assert replay_path.exists()

    trace_lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay = json.loads(replay_path.read_text(encoding="utf-8"))

    assert trace_lines[0]["record_type"] == "archive_started"
    assert trace_lines[0]["agent"]["type"] == "StaticAgent"
    assert "secret" not in json.dumps(trace_lines, ensure_ascii=False)
    request_event = next(
        record["event"]
        for record in trace_lines
        if record.get("event", {}).get("type") == "model_request"
    )
    response_event = next(
        record["event"]
        for record in trace_lines
        if record.get("event", {}).get("type") == "model_response"
    )
    assert request_event["request"]["messages"][0]["role"] == "user"
    assert request_event["request"]["tools"]
    assert response_event["raw"] == {"id": "response_1"}
    assert response_event["usage"]["total_tokens"] == 17
    assert response_event["timing"]["duration_ms"] >= 0

    assert replay["kind"] == "llm_replay"
    assert replay["status"] == "completed"
    assert replay["score"]["score"] == run.score["score"]
    assert replay["score"]["mode"] == "endgame_arena"
    assert replay["data"]["data_hash"]
    assert replay["data"]["game_seed"] == 20260524
    assert replay["data"]["scoring_seed"] == 20260526
    assert replay["turns"][0]["steps"][0]["type"] == "turn_prompt"
    assert replay["turns"][0]["steps"][1]["type"] == "assistant"
    assert replay["turns"][0]["steps"][1]["usage"]["total_tokens"] == 17
    assert replay["turns"][0]["steps"][1]["timing"]["duration_ms"] >= 0
    assert replay["turns"][0]["steps"][2]["type"] == "tool_result"
    assert replay["turns"][0]["steps"][2]["name"] == "end_turn"
    assert replay["turns"][0]["steps"][2]["content"].startswith("OK end_turn")
    assert "result" not in replay["turns"][0]["steps"][2]
    assert run.turns[0].messages[-1]["content"] == replay["turns"][0]["steps"][2]["content"]


def test_run_llm_game_can_override_seeds_and_archives_effective_values(tmp_path) -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(
            max_tool_calls_per_turn=2,
            archive_dir=tmp_path,
            game_seed=11,
            scoring_seed=22,
        ),
    )

    archive_dir = Path(run.archive_dir)
    replay = _read_json(archive_dir / "replay.json")

    assert run.final_observation["seed"] == 11
    assert run.final_observation["scoring"]["seed"] == 22
    assert run.score["seed"] == 22
    assert replay["data"]["game_seed"] == 11
    assert replay["data"]["scoring_seed"] == 22
    assert replay["config"]["game_seed"] == 11
    assert replay["config"]["scoring_seed"] == 22

    with pytest.raises(ValueError, match="game_seed"):
        run_llm_game(
            agent,
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path, game_seed=12, scoring_seed=22),
            resume_archive_dir=archive_dir,
        )


def test_run_llm_game_archives_stream_final_without_delta_chunks(tmp_path) -> None:
    class StreamingAgent:
        def respond_stream(self, *, messages, tools, event_sink):
            event_sink({"type": "model_reasoning_delta", "text": "thinking"})
            event_sink({"type": "model_delta", "text": "done"})
            event_sink(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "call_id": "call_stream",
                    "name": "end_turn",
                    "arguments_delta": "{}",
                }
            )
            event_sink(
                {
                    "type": "model_stream_completed",
                    "text": "done",
                    "tool_calls": [
                        {
                            "id": "call_stream",
                            "name": "end_turn",
                            "arguments": {"hunts": []},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                        "total_tokens": 10,
                    },
                    "chunk_count": 3,
                }
            )
            return LlmAgentResponse(
                text="done",
                tool_calls=(LlmToolCall("end_turn", {"hunts": []}, call_id="call_stream"),),
                raw={"stream": True, "chunk_count": 3},
                usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            )

    run = run_llm_game(
        StreamingAgent(),
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=tmp_path),
    )

    assert run.status == "completed"
    archive_dir = Path(run.archive_dir)
    trace_lines = [
        _read_json_line(line)
        for line in (archive_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [
        record["event"]["type"]
        for record in trace_lines
        if record.get("event", {}).get("type")
    ]
    assert "model_delta" not in event_types
    assert "model_reasoning_delta" not in event_types
    assert "tool_call_delta" not in event_types
    assert "model_stream_completed" in event_types
    replay = _read_json(archive_dir / "replay.json")
    assert replay["turns"][0]["steps"][1]["usage"]["total_tokens"] == 10


def test_run_llm_game_archives_interrupted_run_incrementally(tmp_path) -> None:
    class ExplodingAgent:
        def respond(self, *, messages, tools):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        run_llm_game(
            ExplodingAgent(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(archive_dirs) == 1
    replay = _read_json(archive_dirs[0] / "replay.json")
    trace_lines = [
        _read_json_line(line)
        for line in (archive_dirs[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert replay["status"] == "interrupted"
    assert replay["failure_reason"] == "model unavailable"
    assert replay["turns"][0]["steps"][0]["type"] == "turn_prompt"
    assert any(
        record.get("event", {}).get("type") == "model_request"
        for record in trace_lines
    )
    assert trace_lines[-1]["record_type"] == "run_exception"


def test_run_llm_game_resumes_interrupted_archive_in_place(tmp_path) -> None:
    class EndOnceThenExplode:
        def __init__(self) -> None:
            self.calls = 0

        def respond(self, *, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
            raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError, match="connection dropped"):
        run_llm_game(
            EndOnceThenExplode(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    trace_path = archive_dir / "trace.jsonl"
    replay_path = archive_dir / "replay.json"
    before_trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    interrupted_replay = _read_json(replay_path)

    assert interrupted_replay["status"] == "interrupted"
    assert interrupted_replay["final_observation"]["turn"] == 2

    run = run_llm_game(
        StaticAgent(LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))),
        data_dir=_data_dir(),
        config=LlmRunConfig(archive_dir=tmp_path),
        resume_archive_dir=archive_dir,
    )

    after_trace_lines = [
        _read_json_line(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay = _read_json(replay_path)

    assert run.status == "completed"
    assert run.archive_dir == str(archive_dir)
    assert len(after_trace_lines) > len(before_trace_lines)
    assert any(record["record_type"] == "archive_resumed" for record in after_trace_lines)
    assert any(
        record.get("event", {}).get("type") == "run_resumed"
        for record in after_trace_lines
    )
    assert replay["status"] == "completed"
    assert replay["score"]["score"] == run.score["score"]
    assert replay["session_id"] == interrupted_replay["session_id"]
    assert replay["resume_history"]
    assert replay["final_observation"]["finished"] is True


def test_run_llm_game_refuses_resume_when_data_hash_changed(tmp_path) -> None:
    class ExplodingAgent:
        def respond(self, *, messages, tools):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        run_llm_game(
            ExplodingAgent(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    replay_path = archive_dir / "replay.json"
    replay = _read_json(replay_path)
    replay["data"]["data_hash"] = "0" * 64
    replay_path.write_text(json.dumps(replay), encoding="utf-8")

    with pytest.raises(ValueError, match="data_hash"):
        run_llm_game(
            StaticAgent(LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
            resume_archive_dir=archive_dir,
        )


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_line(line: str) -> dict[str, Any]:
    return json.loads(line)
