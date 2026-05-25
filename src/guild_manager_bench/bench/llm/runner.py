from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import DEFAULT_OBJECTIVE, build_turn_prompt
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.llm.trace import (
    LlmGameRun,
    ModelResponseRecord,
    ToolCallRecord,
    TurnTrace,
)


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    """模型请求执行的一个工具调用。"""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "arguments": dict(self.arguments),
        }
        if self.call_id is not None:
            data["id"] = self.call_id
        return data


@dataclass(frozen=True, slots=True)
class LlmAgentResponse:
    """模型对当前消息和工具 schema 的一次响应。"""

    text: str = ""
    tool_calls: Sequence[LlmToolCall] = ()
    raw: Any = None
    assistant_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "assistant_metadata", dict(self.assistant_metadata))


class LlmTurnAgent(Protocol):
    """模型适配器协议。"""

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        """根据当前消息和工具 schema 返回下一次模型响应。"""


EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class LlmRunConfig:
    """LLM benchmark 跑局配置。"""

    objective: str = DEFAULT_OBJECTIVE
    max_tool_calls_per_turn: int = 20
    max_empty_responses: int = 2
    max_end_turn_attempts: int = 3
    max_invalid_tool_responses: int = 3
    max_model_steps_per_turn: int = 50

    def __post_init__(self) -> None:
        _require_non_negative("max_tool_calls_per_turn", self.max_tool_calls_per_turn)
        _require_positive("max_empty_responses", self.max_empty_responses)
        _require_positive("max_end_turn_attempts", self.max_end_turn_attempts)
        _require_positive("max_invalid_tool_responses", self.max_invalid_tool_responses)
        _require_positive("max_model_steps_per_turn", self.max_model_steps_per_turn)


def run_llm_game(
    agent: LlmTurnAgent,
    *,
    data_dir: str | Path = "data",
    session_id: str | None = None,
    config: LlmRunConfig | None = None,
    event_sink: EventSink | None = None,
) -> LlmGameRun:
    """运行一整局 LLM agent benchmark。"""

    config = config or LlmRunConfig()
    tools = GuildManagerTools.from_data_dir(data_dir)
    session = tools.start_session(session_id)
    session_id = session["session_id"]
    traces: list[TurnTrace] = []
    _emit(
        event_sink,
        "run_started",
        session_id=session_id,
        config=_config_to_dict(config),
    )

    while True:
        observation = tools.get_observation(session_id)["observation"]
        if observation["finished"]:
            run = LlmGameRun(
                status="completed",
                session_id=session_id,
                final_observation=observation,
                turns=traces,
            )
            _emit(event_sink, "run_completed", run=_run_summary(run))
            return run

        turn_trace = run_llm_turn(
            agent,
            tools,
            session_id,
            config=config,
            event_sink=event_sink,
        )
        traces.append(turn_trace)
        if turn_trace.status == "failed":
            run = LlmGameRun(
                status="failed",
                session_id=session_id,
                final_observation=tools.get_observation(session_id)["observation"],
                turns=traces,
                failure_reason=turn_trace.failure_reason,
            )
            _emit(event_sink, "run_failed", run=_run_summary(run))
            return run


def run_llm_turn(
    agent: LlmTurnAgent,
    tools: GuildManagerTools,
    session_id: str,
    *,
    config: LlmRunConfig | None = None,
    event_sink: EventSink | None = None,
) -> TurnTrace:
    """运行单个游戏回合，直到 end_turn 成功或判定失败。"""

    config = config or LlmRunConfig()
    observation = tools.get_observation(session_id)["observation"]
    previous_turn_event = _previous_turn_event(tools, session_id)
    prompt = build_turn_prompt(
        observation,
        objective=config.objective,
        max_tool_calls=config.max_tool_calls_per_turn,
        previous_turn_event=previous_turn_event,
    )
    turn_trace = TurnTrace(
        turn=observation["turn"],
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    _emit(
        event_sink,
        "turn_started",
        turn=observation["turn"],
        prompt=prompt,
        observation=observation,
    )
    turn_harness = TurnToolHarness(
        tools,
        session_id,
        max_tool_calls=config.max_tool_calls_per_turn,
    )

    empty_responses = 0
    failed_end_turns = 0
    invalid_tool_responses = 0

    for step_index in range(1, config.max_model_steps_per_turn + 1):
        schemas = tuple(turn_harness.tool_schemas())
        _emit(
            event_sink,
            "model_request",
            turn=observation["turn"],
            step=step_index,
            tool_names=[schema["name"] for schema in schemas],
            message_count=len(turn_trace.messages),
        )
        response = _agent_response(
            agent,
            messages=tuple(turn_trace.messages),
            tools=schemas,
            event_sink=event_sink,
        )
        response = _response_with_call_ids(response, len(turn_trace.tool_calls))
        response_record = ModelResponseRecord(
            text=response.text,
            tool_calls=[call.to_dict() for call in response.tool_calls],
        )
        turn_trace.model_responses.append(response_record)
        turn_trace.messages.append(_assistant_message(response))
        _emit(
            event_sink,
            "model_response",
            turn=observation["turn"],
            step=step_index,
            text=response.text,
            tool_calls=[call.to_dict() for call in response.tool_calls],
        )

        if not response.tool_calls:
            empty_responses += 1
            if empty_responses >= config.max_empty_responses:
                return _fail_turn(turn_trace, "empty_response_limit", event_sink)
            retry = _retry_message_empty_response()
            turn_trace.messages.append(retry)
            _emit(event_sink, "retry", turn=observation["turn"], reason="empty_response", message=retry["content"])
            continue
        empty_responses = 0

        for call in response.tool_calls:
            _emit(
                event_sink,
                "tool_call",
                turn=observation["turn"],
                name=call.name,
                arguments=dict(call.arguments),
                call_id=call.call_id,
            )
            result = turn_harness.call_tool(call.name, call.arguments)
            turn_trace.tool_calls.append(
                ToolCallRecord(
                    name=call.name,
                    arguments=dict(call.arguments),
                    result=result,
                )
            )
            turn_trace.messages.append(_tool_message(call, result))
            _emit(
                event_sink,
                "tool_result",
                turn=observation["turn"],
                name=call.name,
                arguments=dict(call.arguments),
                call_id=call.call_id,
                result=result,
            )

            if call.name == "end_turn":
                if result.get("ok") is True:
                    turn_trace.complete()
                    _emit(event_sink, "turn_completed", trace=turn_trace.to_dict())
                    return turn_trace
                failed_end_turns += 1
                if failed_end_turns >= config.max_end_turn_attempts:
                    return _fail_turn(turn_trace, "end_turn_attempt_limit", event_sink)
                retry = _retry_message_end_turn_failed(result)
                turn_trace.messages.append(retry)
                _emit(
                    event_sink,
                    "retry",
                    turn=observation["turn"],
                    reason="end_turn_failed",
                    message=retry["content"],
                )
                continue

            if _is_protocol_error(result):
                invalid_tool_responses += 1
                if invalid_tool_responses >= config.max_invalid_tool_responses:
                    return _fail_turn(turn_trace, _protocol_failure_reason(result), event_sink)

        if turn_harness.budget.exhausted and not turn_harness.ended:
            retry = _retry_message_budget_exhausted()
            turn_trace.messages.append(retry)
            _emit(
                event_sink,
                "retry",
                turn=observation["turn"],
                reason="budget_exhausted",
                message=retry["content"],
            )

    return _fail_turn(turn_trace, "model_step_limit", event_sink)


def _previous_turn_event(
    tools: GuildManagerTools,
    session_id: str,
) -> dict[str, Any] | None:
    events = tools.get_events(session_id)["events"]
    for event in reversed(events):
        if event["type"] == "turn_ended":
            return event
    return None


def _assistant_message(response: LlmAgentResponse) -> dict[str, Any]:
    message = {
        "role": "assistant",
        "content": response.text,
        "tool_calls": [call.to_dict() for call in response.tool_calls],
    }
    reasoning_content = response.assistant_metadata.get("reasoning_content")
    if isinstance(reasoning_content, str):
        message["reasoning_content"] = reasoning_content
    return message


def _tool_message(call: LlmToolCall, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.call_id,
        "name": call.name,
        "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
    }


def _response_with_call_ids(
    response: LlmAgentResponse,
    previous_tool_call_count: int,
) -> LlmAgentResponse:
    tool_calls = []
    for index, call in enumerate(response.tool_calls, start=1):
        call_id = call.call_id or f"call_{previous_tool_call_count + index}"
        tool_calls.append(
            LlmToolCall(
                name=call.name,
                arguments=call.arguments,
                call_id=call_id,
            )
        )
    return LlmAgentResponse(
        text=response.text,
        tool_calls=tuple(tool_calls),
        assistant_metadata=response.assistant_metadata,
        raw=response.raw,
    )


def _agent_response(
    agent: LlmTurnAgent,
    *,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    event_sink: EventSink | None,
) -> LlmAgentResponse:
    respond_stream = getattr(agent, "respond_stream", None)
    if callable(respond_stream):
        return respond_stream(messages=messages, tools=tools, event_sink=event_sink)
    return agent.respond(messages=messages, tools=tools)


def _retry_message_empty_response() -> dict[str, str]:
    return {
        "role": "user",
        "content": "你本回合还没有调用任何工具。必须调用工具推进游戏，并最终成功调用 end_turn。",
    }


def _retry_message_end_turn_failed(result: Mapping[str, Any]) -> dict[str, str]:
    return {
        "role": "user",
        "content": f"end_turn 调用失败：{result.get('error', 'unknown error')}。请修正讨伐列表后再次调用 end_turn。",
    }


def _retry_message_budget_exhausted() -> dict[str, str]:
    return {
        "role": "user",
        "content": "你的非 end_turn 工具调用预算已经耗尽。现在只能调用 end_turn。",
    }


def _fail_turn(
    turn_trace: TurnTrace,
    reason: str,
    event_sink: EventSink | None = None,
) -> TurnTrace:
    turn_trace.fail(reason)
    _emit(event_sink, "turn_failed", trace=turn_trace.to_dict())
    return turn_trace


def _emit(event_sink: EventSink | None, event_type: str, **payload: Any) -> None:
    if event_sink is None:
        return
    event_sink({"type": event_type, **payload})


def _config_to_dict(config: LlmRunConfig) -> dict[str, Any]:
    return {
        "objective": config.objective,
        "max_tool_calls_per_turn": config.max_tool_calls_per_turn,
        "max_empty_responses": config.max_empty_responses,
        "max_end_turn_attempts": config.max_end_turn_attempts,
        "max_invalid_tool_responses": config.max_invalid_tool_responses,
        "max_model_steps_per_turn": config.max_model_steps_per_turn,
    }


def _run_summary(run: LlmGameRun) -> dict[str, Any]:
    return {
        "status": run.status,
        "session_id": run.session_id,
        "turns": len(run.turns),
        "failure_reason": run.failure_reason,
        "final_observation": dict(run.final_observation),
    }


def _is_protocol_error(result: Mapping[str, Any]) -> bool:
    if result.get("ok") is not False:
        return False
    if result.get("event", {}).get("type") == "action_rejected":
        return False
    return True


def _protocol_failure_reason(result: Mapping[str, Any]) -> str:
    budget = result.get("tool_budget")
    if isinstance(budget, Mapping) and budget.get("end_turn_required") is True:
        return "tool_budget_exhausted_without_end_turn"
    return "invalid_tool_call_limit"


def _require_positive(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be >= 1")


def _require_non_negative(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be >= 0")
