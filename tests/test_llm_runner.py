from pathlib import Path
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    run_llm_game,
    run_llm_turn,
)


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
        config=LlmRunConfig(max_tool_calls_per_turn=2),
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
        config=LlmRunConfig(max_empty_responses=2),
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
        config=LlmRunConfig(max_end_turn_attempts=2),
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
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "turn_completed" in event_types


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
