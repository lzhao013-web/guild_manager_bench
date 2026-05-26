from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol, Sequence

from guild_manager_bench.bench.llm.archive import (
    LlmRunArchiveWriter,
    resume_llm_run_archive,
    start_llm_run_archive,
)
from guild_manager_bench.bench.llm.formatting import skill_summary
from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import DEFAULT_OBJECTIVE, build_turn_prompt
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.llm.trace import (
    LlmGameRun,
    ModelResponseRecord,
    ToolCallRecord,
    TurnTrace,
)
from guild_manager_bench.bench.metrics import score_final_state
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import describe_data_source, verify_data_source


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
    usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "assistant_metadata", dict(self.assistant_metadata))
        object.__setattr__(self, "usage", dict(self.usage))


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
    archive_dir: str | Path | None = "runs/llm"
    game_seed: int | None = None
    scoring_seed: int | None = None

    def __post_init__(self) -> None:
        _require_non_negative("max_tool_calls_per_turn", self.max_tool_calls_per_turn)
        _require_positive("max_empty_responses", self.max_empty_responses)
        _require_positive("max_end_turn_attempts", self.max_end_turn_attempts)
        _require_positive("max_invalid_tool_responses", self.max_invalid_tool_responses)
        _require_positive("max_model_steps_per_turn", self.max_model_steps_per_turn)
        _require_optional_int("game_seed", self.game_seed)
        _require_optional_int("scoring_seed", self.scoring_seed)


def run_llm_game(
    agent: LlmTurnAgent,
    *,
    data_dir: str | Path = "data",
    session_id: str | None = None,
    config: LlmRunConfig | None = None,
    event_sink: EventSink | None = None,
    resume_archive_dir: str | Path | None = None,
    data_source: Mapping[str, Any] | None = None,
) -> LlmGameRun:
    """运行一整局 LLM agent benchmark。"""

    config = config or LlmRunConfig()
    definition = _definition_for_config(data_dir, config)
    data_source = (
        dict(data_source)
        if data_source is not None
        else describe_data_source(data_dir)
    )
    data_source = _data_source_with_effective_seeds(data_source, definition)
    tools = GuildManagerTools(definition)
    resume = (
        None
        if resume_archive_dir is None
        else _restore_from_replay_archive(tools, resume_archive_dir, data_source)
    )
    if resume is None:
        session = tools.start_session(session_id)
        session_id = session["session_id"]
        traces: list[TurnTrace] = []
        archive_writer = _start_archive(config, agent, session_id, data_source)
        initial_observation = session["observation"]
    else:
        session_id = resume["session_id"]
        traces = list(resume["traces"])
        archive_writer = _resume_archive(
            config,
            agent,
            resume_archive_dir,
            resume["replay"],
            data_source,
        )
        initial_observation = tools.get_observation(session_id)["observation"]

    def emit(event: dict[str, Any]) -> None:
        if archive_writer is not None and _should_archive_event(event):
            archive_writer.append_event(event)
        if event_sink is not None:
            event_sink(event)

    if archive_writer is not None:
        if resume is None:
            _emit(emit, "run_archived", archive=archive_writer.archive.to_dict())
        else:
            _emit(
                emit,
                "run_resumed",
                archive=archive_writer.archive.to_dict(),
                session_id=session_id,
                restored_turns=len(traces),
                restored_observation=initial_observation,
            )
        _write_replay(
            archive_writer,
            status="running",
            traces=traces,
            final_observation=initial_observation,
        )

    active_turn_trace: TurnTrace | None = None

    def update_replay(active_turn: TurnTrace | None = None) -> None:
        nonlocal active_turn_trace
        if archive_writer is None:
            return
        active_turn_trace = active_turn
        _write_replay(
            archive_writer,
            status="running",
            traces=traces,
            active_turn=active_turn,
            final_observation=tools.get_observation(session_id)["observation"],
        )

    _emit(
        emit,
        "run_started",
        session_id=session_id,
        config=_config_to_dict(config),
    )

    try:
        while True:
            observation = tools.get_observation(session_id)["observation"]
            if observation["finished"]:
                score = _score_final_state(tools, session_id)
                run = LlmGameRun(
                    status="completed",
                    session_id=session_id,
                    final_observation=observation,
                    turns=traces,
                    score=score,
                )
                run.archive_dir = (
                    None
                    if archive_writer is None
                    else str(archive_writer.archive.directory)
                )
                _write_replay(
                    archive_writer,
                    status="completed",
                    traces=traces,
                    final_observation=observation,
                    score=score,
                )
                _emit(emit, "run_completed", run=_run_summary(run))
                return run

            turn_trace = run_llm_turn(
                agent,
                tools,
                session_id,
                config=config,
                event_sink=emit,
                trace_update=update_replay,
            )
            traces.append(turn_trace)
            active_turn_trace = None
            update_replay()
            if turn_trace.status == "failed":
                final_observation = tools.get_observation(session_id)["observation"]
                run = LlmGameRun(
                    status="failed",
                    session_id=session_id,
                    final_observation=final_observation,
                    turns=traces,
                    failure_reason=turn_trace.failure_reason,
                )
                run.archive_dir = (
                    None
                    if archive_writer is None
                    else str(archive_writer.archive.directory)
                )
                _write_replay(
                    archive_writer,
                    status="failed",
                    traces=traces,
                    final_observation=final_observation,
                    failure_reason=turn_trace.failure_reason,
                )
                _emit(emit, "run_failed", run=_run_summary(run))
                return run
    except Exception as exc:
        if archive_writer is not None:
            try:
                _write_replay(
                    archive_writer,
                    status="interrupted",
                    traces=traces,
                    active_turn=active_turn_trace,
                    final_observation=tools.get_observation(session_id)["observation"],
                    failure_reason=str(exc),
                )
                archive_writer.append_record(
                    "run_exception",
                    {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            except Exception:
                pass
        raise


def run_llm_turn(
    agent: LlmTurnAgent,
    tools: GuildManagerTools,
    session_id: str,
    *,
    config: LlmRunConfig | None = None,
    event_sink: EventSink | None = None,
    trace_update: Callable[[TurnTrace], None] | None = None,
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
    _notify_trace_update(trace_update, turn_trace)
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
        request_messages = _json_safe(turn_trace.messages)
        request_tools = _json_safe(schemas)
        _emit(
            event_sink,
            "model_request",
            turn=observation["turn"],
            step=step_index,
            request={"messages": request_messages, "tools": request_tools},
            tool_names=[schema["name"] for schema in schemas],
            message_count=len(turn_trace.messages),
        )
        started_at = _timestamp_iso()
        started = perf_counter()
        response = _agent_response(
            agent,
            messages=tuple(turn_trace.messages),
            tools=schemas,
            event_sink=event_sink,
        )
        duration_ms = round((perf_counter() - started) * 1000, 3)
        timing = {
            "started_at": started_at,
            "ended_at": _timestamp_iso(),
            "duration_ms": duration_ms,
        }
        response = _response_with_call_ids(response, len(turn_trace.tool_calls))
        usage = _json_safe(response.usage)
        response_record = ModelResponseRecord(
            step=step_index,
            request_messages=request_messages,
            request_tools=request_tools,
            text=response.text,
            tool_calls=[call.to_dict() for call in response.tool_calls],
            assistant_metadata=dict(response.assistant_metadata),
            timing=timing,
            usage=usage if isinstance(usage, dict) else {},
            raw=_json_safe(response.raw),
        )
        turn_trace.model_responses.append(response_record)
        turn_trace.messages.append(_assistant_message(response))
        _notify_trace_update(trace_update, turn_trace)
        _emit(
            event_sink,
            "model_response",
            turn=observation["turn"],
            step=step_index,
            text=response.text,
            tool_calls=[call.to_dict() for call in response.tool_calls],
            assistant_metadata=dict(response.assistant_metadata),
            timing=timing,
            usage=usage if isinstance(usage, dict) else {},
            raw=_json_safe(response.raw),
        )

        if not response.tool_calls:
            empty_responses += 1
            if empty_responses >= config.max_empty_responses:
                return _fail_turn(
                    turn_trace,
                    "empty_response_limit",
                    event_sink,
                    trace_update,
                )
            retry = _retry_message_empty_response()
            turn_trace.messages.append(retry)
            _notify_trace_update(trace_update, turn_trace)
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
                    call_id=call.call_id,
                )
            )
            tool_message = _tool_message(call, result)
            turn_trace.messages.append(tool_message)
            _notify_trace_update(trace_update, turn_trace)
            _emit(
                event_sink,
                "tool_result",
                turn=observation["turn"],
                name=call.name,
                arguments=dict(call.arguments),
                call_id=call.call_id,
                content=tool_message["content"],
                result=result,
            )

            if call.name == "end_turn":
                if result.get("ok") is True:
                    turn_trace.complete()
                    _notify_trace_update(trace_update, turn_trace)
                    _emit(event_sink, "turn_completed", trace=turn_trace.to_dict())
                    return turn_trace
                failed_end_turns += 1
                if failed_end_turns >= config.max_end_turn_attempts:
                    return _fail_turn(
                        turn_trace,
                        "end_turn_attempt_limit",
                        event_sink,
                        trace_update,
                    )
                retry = _retry_message_end_turn_failed(result)
                turn_trace.messages.append(retry)
                _notify_trace_update(trace_update, turn_trace)
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
                    return _fail_turn(
                        turn_trace,
                        _protocol_failure_reason(result),
                        event_sink,
                        trace_update,
                    )

        if turn_harness.budget.exhausted and not turn_harness.ended:
            retry = _retry_message_budget_exhausted()
            turn_trace.messages.append(retry)
            _notify_trace_update(trace_update, turn_trace)
            _emit(
                event_sink,
                "retry",
                turn=observation["turn"],
                reason="budget_exhausted",
                message=retry["content"],
            )

    return _fail_turn(turn_trace, "model_step_limit", event_sink, trace_update)


def _previous_turn_event(
    tools: GuildManagerTools,
    session_id: str,
) -> dict[str, Any] | None:
    events = tools.get_events(session_id)["events"]
    for event in reversed(events):
        if event["type"] == "turn_ended":
            return event
    return None


def _restore_from_replay_archive(
    tools: GuildManagerTools,
    archive_dir: str | Path,
    data_source: Mapping[str, Any],
) -> dict[str, Any]:
    replay = _read_replay(Path(archive_dir) / "replay.json")
    data = replay.get("data")
    verify_data_source(data if isinstance(data, Mapping) else None, data_source)
    session_id = replay.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("replay session_id must be a non-empty string")

    tools.start_session(session_id)
    for turn in _sequence(replay.get("turns")):
        if not isinstance(turn, Mapping):
            continue
        for step in _sequence(turn.get("steps")):
            if isinstance(step, Mapping):
                _replay_confirmed_tool_result(tools, session_id, step)

    return {
        "session_id": session_id,
        "replay": replay,
        "traces": _traces_from_replay(replay),
    }


def _read_replay(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"replay not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != "llm_replay":
        raise ValueError("replay must be an llm_replay JSON object")
    return data


def _replay_confirmed_tool_result(
    tools: GuildManagerTools,
    session_id: str,
    step: Mapping[str, Any],
) -> None:
    if step.get("type") != "tool_result":
        return
    name = step.get("name")
    if name not in _STATE_MUTATING_TOOL_NAMES:
        return
    if not _replay_tool_step_succeeded(step):
        return

    arguments = _replay_tool_arguments(step)
    arguments["session_id"] = session_id
    replayed = tools.call_tool(str(name), arguments)
    if replayed.get("ok") is not True:
        raise ValueError(
            f"failed to replay confirmed tool result {name}: {replayed.get('error')}"
        )


def _replay_tool_step_succeeded(step: Mapping[str, Any]) -> bool:
    content = step.get("content")
    if isinstance(content, str):
        stripped = content.lstrip()
        if stripped.startswith("OK "):
            return True
        if stripped.startswith("FAIL "):
            return False
        parsed = _parse_json_mapping(stripped)
        if parsed is not None and parsed.get("ok") is True:
            return True
    result = step.get("result")
    return isinstance(result, Mapping) and result.get("ok") is True


def _replay_tool_arguments(step: Mapping[str, Any]) -> dict[str, Any]:
    arguments = step.get("arguments")
    if isinstance(arguments, Mapping) and arguments:
        return dict(arguments)

    result = step.get("result")
    if not isinstance(result, Mapping):
        content = step.get("content")
        result = _parse_json_mapping(content) if isinstance(content, str) else None
    if not isinstance(result, Mapping):
        return {}

    event = result.get("event")
    action = event.get("action") if isinstance(event, Mapping) else None
    if isinstance(action, Mapping):
        data = dict(action)
        data.pop("type", None)
        return data
    return {}


def _parse_json_mapping(value: str) -> Mapping[str, Any] | None:
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, Mapping) else None


def _traces_from_replay(replay: Mapping[str, Any]) -> list[TurnTrace]:
    traces: list[TurnTrace] = []
    for turn in _sequence(replay.get("turns")):
        if isinstance(turn, Mapping):
            traces.append(_trace_from_replay_turn(turn))
    return traces


def _trace_from_replay_turn(turn: Mapping[str, Any]) -> TurnTrace:
    turn_number = turn.get("turn")
    prompt = str(turn.get("prompt") or "")
    trace = TurnTrace(
        turn=turn_number if isinstance(turn_number, int) else 0,
        prompt=prompt,
        messages=[],
    )
    calls_by_id: dict[str, dict[str, Any]] = {}
    for step in _sequence(turn.get("steps")):
        if not isinstance(step, Mapping):
            continue
        step_type = step.get("type")
        if step_type == "turn_prompt":
            content = str(step.get("content") or prompt)
            trace.messages.append({"role": "user", "content": content})
            if not trace.prompt:
                trace.prompt = content
            continue
        if step_type == "retry_prompt":
            trace.messages.append(
                {"role": "user", "content": str(step.get("content") or "")}
            )
            continue
        if step_type == "assistant":
            tool_calls = [
                dict(call)
                for call in _sequence(step.get("tool_calls"))
                if isinstance(call, Mapping)
            ]
            for call in tool_calls:
                call_id = call.get("id")
                if isinstance(call_id, str):
                    calls_by_id[call_id] = call
            message = {
                "role": "assistant",
                "content": str(step.get("content") or ""),
                "tool_calls": tool_calls,
            }
            reasoning_content = step.get("reasoning_content")
            if isinstance(reasoning_content, str):
                message["reasoning_content"] = reasoning_content
            trace.messages.append(message)
            usage = step.get("usage")
            timing = step.get("timing")
            assistant_metadata: dict[str, Any] = {}
            if isinstance(reasoning_content, str):
                assistant_metadata["reasoning_content"] = reasoning_content
            trace.model_responses.append(
                ModelResponseRecord(
                    text=message["content"],
                    tool_calls=tool_calls,
                    assistant_metadata=assistant_metadata,
                    timing=dict(timing) if isinstance(timing, Mapping) else {},
                    usage=dict(usage) if isinstance(usage, Mapping) else {},
                )
            )
            continue
        if step_type == "tool_result":
            call_id = step.get("call_id")
            call = calls_by_id.get(call_id, {}) if isinstance(call_id, str) else {}
            name = step.get("name") or call.get("name") or ""
            arguments = step.get("arguments")
            if not isinstance(arguments, Mapping):
                arguments = call.get("arguments", {})
            if not isinstance(arguments, Mapping):
                arguments = {}
            result = step.get("result")
            if not isinstance(result, Mapping):
                result = {}
            trace.tool_calls.append(
                ToolCallRecord(
                    name=str(name),
                    arguments=dict(arguments),
                    result=dict(result),
                    call_id=call_id if isinstance(call_id, str) else None,
                )
            )
            trace.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": str(
                        step.get("content")
                        or json.dumps(result, ensure_ascii=False, sort_keys=True)
                    ),
                }
            )

    status = turn.get("status")
    if status == "completed":
        trace.complete()
    elif status == "failed":
        trace.fail(str(turn.get("failure_reason") or "unknown"))
    return trace


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


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
        "content": _format_tool_result_for_model(call.name, result),
    }


def _format_tool_result_for_model(name: str, result: Mapping[str, Any]) -> str:
    ok = result.get("ok")
    lines: list[str] = []
    if ok is False:
        lines.append(f"FAIL {name}: {result.get('error', 'unknown error')}")
        event = result.get("event")
        if isinstance(event, Mapping) and event.get("summary"):
            lines.append(f"event: {event['summary']}")
        return "\n".join(_append_budget_lines(lines, result))

    lines.append(f"OK {name}")
    if name == "get_observation" and isinstance(result.get("observation"), Mapping):
        _append_observation_lines(lines, result["observation"])
    elif name == "get_events" and isinstance(result.get("events"), Sequence):
        _append_events_lines(lines, result["events"])
    elif name == "preview_battle" and isinstance(result.get("preview"), Mapping):
        _append_battle_preview_lines(lines, result["preview"])
    else:
        event = result.get("event")
        if isinstance(event, Mapping):
            summary = event.get("summary")
            if summary:
                lines[0] = f"OK {name}: {summary}"
            _append_changes_lines(lines, event.get("changes"))
            has_event_battles = _append_battles_lines(lines, event.get("battles"))
        else:
            has_event_battles = False
        turn_result = result.get("turn_result")
        if isinstance(turn_result, Mapping):
            if not has_event_battles:
                _append_battles_lines(lines, turn_result.get("battles"))
            crafted = turn_result.get("crafted_equipment_ids")
            purchased = turn_result.get("purchased_upgrade_ids")
            if crafted:
                lines.append(f"crafted_equipment_ids: {_join_values(crafted)}")
            if purchased:
                lines.append(f"purchased_upgrade_ids: {_join_values(purchased)}")

    return "\n".join(_append_budget_lines(lines, result))


def _append_battle_preview_lines(lines: list[str], preview: Mapping[str, Any]) -> None:
    outcome = "win" if preview.get("won") else "loss"
    adventurer_resources = preview.get("adventurer_resources")
    monster_resources = preview.get("monster_resources")
    before = (
        adventurer_resources.get("before")
        if isinstance(adventurer_resources, Mapping)
        else {}
    )
    after = (
        adventurer_resources.get("after")
        if isinstance(adventurer_resources, Mapping)
        else {}
    )
    monster_after = (
        monster_resources.get("after")
        if isinstance(monster_resources, Mapping)
        else {}
    )
    reward = preview.get("reward")
    reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
    lines[0] = (
        "OK preview_battle: "
        f"{preview.get('adventurer_id')} vs {preview.get('monster_id')} {outcome}"
    )
    lines.append(
        "resources: "
        f"adventurer HP {before.get('current_hp')} -> {after.get('current_hp')}, "
        f"MP {before.get('current_mp')} -> {after.get('current_mp')}; "
        f"monster HP left {monster_after.get('current_hp')}"
    )
    lines.append(
        "combat: "
        f"outcome {preview.get('outcome')}; reason {preview.get('reason')}; "
        f"actions {preview.get('actions_taken')}; time {preview.get('time_elapsed')}"
    )
    lines.append(f"reward_if_win: {reward_text}")


def _append_observation_lines(lines: list[str], observation: Mapping[str, Any]) -> None:
    scoring = observation.get("scoring")
    scoring_seed = scoring.get("seed") if isinstance(scoring, Mapping) else None
    lines.append(
        "state: "
        f"turn {observation.get('turn')}/{observation.get('max_turns')}; "
        f"seed {observation.get('seed')}; "
        f"scoring_seed {scoring_seed}; "
        f"gold {observation.get('gold')}; "
        f"exp_pool {observation.get('experience_pool')}; "
        f"materials {_mapping_inline(observation.get('materials'))}"
    )
    lines.append("adventurers:")
    for adventurer in _sequence(observation.get("adventurers")):
        if isinstance(adventurer, Mapping):
            resources = adventurer.get("resources", {})
            stats = adventurer.get("effective_stats", {})
            if not isinstance(resources, Mapping):
                resources = {}
            if not isinstance(stats, Mapping):
                stats = {}
            equipment = adventurer.get("equipment") or ()
            equipment_text = _equipment_inline(equipment)
            lines.append(
                "- "
                f"{adventurer.get('adventurer_id')} {adventurer.get('name')} "
                f"Lv{adventurer.get('level')} "
                f"HP {resources.get('current_hp')}/{stats.get('hp')} "
                f"MP {resources.get('current_mp')}/{stats.get('mp')} "
                f"ATK {stats.get('attack')} DEF {stats.get('defense')} SPD {stats.get('speed')} "
                f"equipment {equipment_text} "
                f"skills {skill_summary(adventurer.get('skills'))}"
            )

    monsters = [
        monster
        for monster in _sequence(observation.get("monsters"))
        if isinstance(monster, Mapping)
    ]
    if monsters:
        lines.append("monsters:")
        for monster in monsters:
            stats = monster.get("stats", {})
            if not isinstance(stats, Mapping):
                stats = {}
            reward = monster.get("reward", {})
            reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
            lines.append(
                "- "
                f"{monster.get('monster_id')} {monster.get('name')} "
                f"HP {stats.get('hp')} ATK {stats.get('attack')} DEF {stats.get('defense')} SPD {stats.get('speed')} "
                f"skills {skill_summary(monster.get('skills'))} "
                f"reward {reward_text}"
            )

    recipes = [
        recipe
        for recipe in _sequence(observation.get("crafting_recipes"))
        if isinstance(recipe, Mapping)
    ]
    if recipes:
        lines.append("crafting_recipes:")
        for recipe in recipes:
            availability = "can" if recipe.get("can_craft") else "cannot"
            missing = recipe.get("missing")
            missing_text = (
                f"; missing {_mapping_inline(missing)}"
                if isinstance(missing, Mapping) and missing
                else ""
            )
            lines.append(
                "- "
                f"{recipe.get('recipe_id')} {recipe.get('name')} -> {recipe.get('output_name')} "
                f"stats {_mapping_inline(recipe.get('output_stats'))} "
                f"skills {skill_summary(recipe.get('output_skills'))} "
                f"cost gold {recipe.get('gold_cost')} materials {_mapping_inline(recipe.get('material_costs'))} "
                f"{availability}{missing_text}"
            )

    inventory = [
        item
        for item in _sequence(observation.get("equipment_inventory"))
        if isinstance(item, Mapping)
    ]
    if inventory:
        lines.append("equipment_inventory:")
        for item in inventory:
            equipped = item.get("equipped_by") or "free"
            lines.append(
                "- "
                f"{item.get('instance_id')} {item.get('name')} slot {item.get('slot')} "
                f"stats {_mapping_inline(item.get('stats'))} "
                f"skills {skill_summary(item.get('skills'))} "
                f"equipped_by {equipped}"
            )

    upgrades = [
        upgrade
        for upgrade in _sequence(observation.get("global_upgrades"))
        if isinstance(upgrade, Mapping)
    ]
    if upgrades:
        lines.append("global_upgrades:")
        for upgrade in upgrades:
            state = (
                "unlocked"
                if upgrade.get("unlocked")
                else "can"
                if upgrade.get("can_purchase")
                else "cannot"
            )
            lines.append(
                "- "
                f"{upgrade.get('upgrade_id')} {upgrade.get('name')} "
                f"gold {upgrade.get('gold_cost')} "
                f"stats {_mapping_inline(upgrade.get('stats'))} "
                f"skills {skill_summary(upgrade.get('skills'))} {state}"
            )


def _append_events_lines(lines: list[str], events: Sequence[Any]) -> None:
    if not events:
        lines.append("events: none")
        return
    lines.append("events:")
    for event in events:
        if isinstance(event, Mapping):
            lines.append(
                "- "
                f"#{event.get('sequence')} T{event.get('turn')} {event.get('type')}: "
                f"{event.get('summary', '')}"
            )


def _append_changes_lines(lines: list[str], changes: Any) -> None:
    values = [
        change
        for change in _sequence(changes)
        if isinstance(change, Mapping)
    ]
    if not values:
        return
    lines.append("changes:")
    for change in values:
        label = change.get("label") or change.get("field") or change.get("type") or "change"
        if "before" in change:
            lines.append(f"- {label}: {change.get('before')} -> {change.get('after')}")
        else:
            lines.append(f"- {label}: {change.get('after')}")


def _append_battles_lines(lines: list[str], battles: Any) -> bool:
    values = [
        battle
        for battle in _sequence(battles)
        if isinstance(battle, Mapping)
    ]
    if not values:
        return False
    lines.append("battles:")
    for battle in values:
        outcome = "win" if battle.get("won") else "loss"
        reward = battle.get("reward")
        reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
        lines.append(
            "- "
            f"{battle.get('adventurer_id')} vs {battle.get('monster_id')}: {outcome}; "
            f"reward {reward_text}"
        )
    return True


def _append_budget_lines(lines: list[str], result: Mapping[str, Any]) -> list[str]:
    budget = result.get("tool_budget")
    if isinstance(budget, Mapping):
        suffix = "; end_turn only" if budget.get("end_turn_required") else ""
        lines.append(
            "budget: "
            f"{budget.get('used')}/{budget.get('max_tool_calls')} used, "
            f"{budget.get('remaining')} remaining{suffix}"
        )
    return lines


def _equipment_inline(equipment: Any) -> str:
    items = [
        item
        for item in _sequence(equipment)
        if isinstance(item, Mapping)
    ]
    if not items:
        return "none"
    return ", ".join(
        f"{item.get('instance_id')}:{item.get('name')}"
        for item in items
    )


def _reward_inline(reward: Mapping[str, Any]) -> str:
    return (
        "{"
        f"gold:{reward.get('gold', 0)}, "
        f"exp:{reward.get('experience', 0)}, "
        f"materials:{_mapping_inline(reward.get('materials'))}"
        "}"
    )


def _mapping_inline(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "{}"
    return "{" + ", ".join(f"{key}:{item}" for key, item in value.items()) + "}"


def _join_values(value: Any) -> str:
    return ", ".join(str(item) for item in _sequence(value))


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
        usage=response.usage,
        raw=response.raw,
    )


def _timestamp_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _should_archive_event(event: Mapping[str, Any]) -> bool:
    return event.get("type") not in {
        "model_delta",
        "model_reasoning_delta",
        "tool_call_delta",
    }


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
    trace_update: Callable[[TurnTrace], None] | None = None,
) -> TurnTrace:
    turn_trace.fail(reason)
    _notify_trace_update(trace_update, turn_trace)
    _emit(event_sink, "turn_failed", trace=turn_trace.to_dict())
    return turn_trace


def _notify_trace_update(
    trace_update: Callable[[TurnTrace], None] | None,
    turn_trace: TurnTrace,
) -> None:
    if trace_update is not None:
        trace_update(turn_trace)


def _emit(event_sink: EventSink | None, event_type: str, **payload: Any) -> None:
    if event_sink is None:
        return
    event_sink({"type": event_type, **payload})


def _start_archive(
    config: LlmRunConfig,
    agent: LlmTurnAgent,
    session_id: str,
    data_source: Mapping[str, Any],
) -> LlmRunArchiveWriter | None:
    if config.archive_dir is None:
        return None
    return start_llm_run_archive(
        config.archive_dir,
        session_id=session_id,
        config=_config_to_dict(config),
        agent=_agent_metadata(agent),
        data_source=data_source,
    )


def _resume_archive(
    config: LlmRunConfig,
    agent: LlmTurnAgent,
    archive_dir: str | Path,
    replay: Mapping[str, Any],
    data_source: Mapping[str, Any],
) -> LlmRunArchiveWriter | None:
    if config.archive_dir is None:
        return None
    session_id = replay.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("replay session_id must be a non-empty string")
    resume_history = [
        dict(item)
        for item in _sequence(replay.get("resume_history"))
        if isinstance(item, Mapping)
    ]
    resume_history.append(
        {
            "resumed_from_status": replay.get("status"),
            "resumed_from_updated_at": replay.get("updated_at"),
            "config": _config_to_dict(config),
            "agent": _agent_metadata(agent),
            "data": dict(data_source),
        }
    )
    created_at = replay.get("created_at")
    return resume_llm_run_archive(
        archive_dir,
        session_id=session_id,
        created_at=created_at if isinstance(created_at, str) else None,
        config=_config_to_dict(config),
        agent=_agent_metadata(agent),
        data_source=data_source,
        resume_history=resume_history,
    )


def _write_replay(
    archive_writer: LlmRunArchiveWriter | None,
    *,
    status: str,
    traces: Sequence[TurnTrace],
    active_turn: TurnTrace | None = None,
    final_observation: Mapping[str, Any] | None = None,
    failure_reason: str | None = None,
    score: Mapping[str, Any] | None = None,
) -> None:
    if archive_writer is None:
        return
    turns = list(traces)
    if active_turn is not None and active_turn not in turns:
        turns.append(active_turn)
    archive_writer.write_replay(
        status=status,
        turns=turns,
        final_observation=final_observation,
        failure_reason=failure_reason,
        score=score,
    )


def _definition_for_config(
    data_dir: str | Path,
    config: LlmRunConfig,
):
    definition = load_game_definition(data_dir)
    if config.game_seed is not None:
        definition = replace(
            definition,
            rules=replace(definition.rules, seed=config.game_seed),
        )
    if config.scoring_seed is not None:
        definition = replace(
            definition,
            scoring=replace(definition.scoring, seed=config.scoring_seed),
        )
    return definition


def _data_source_with_effective_seeds(
    data_source: Mapping[str, Any],
    definition,
) -> dict[str, Any]:
    data = dict(data_source)
    data["game_seed"] = definition.rules.seed
    data["scoring_seed"] = definition.scoring.seed
    return data


def _score_final_state(
    tools: GuildManagerTools,
    session_id: str,
) -> dict[str, Any]:
    return score_final_state(
        tools.definition,
        tools.get_state(session_id),
    ).to_dict()


def _config_to_dict(config: LlmRunConfig) -> dict[str, Any]:
    return {
        "objective": config.objective,
        "max_tool_calls_per_turn": config.max_tool_calls_per_turn,
        "max_empty_responses": config.max_empty_responses,
        "max_end_turn_attempts": config.max_end_turn_attempts,
        "max_invalid_tool_responses": config.max_invalid_tool_responses,
        "max_model_steps_per_turn": config.max_model_steps_per_turn,
        "archive_dir": None if config.archive_dir is None else str(config.archive_dir),
        "game_seed": config.game_seed,
        "scoring_seed": config.scoring_seed,
    }


def _agent_metadata(agent: LlmTurnAgent) -> dict[str, Any]:
    metadata: dict[str, Any] = {"type": type(agent).__name__}
    config = getattr(agent, "config", None)
    if config is None:
        return metadata
    safe_fields = (
        "model",
        "base_url",
        "timeout",
        "temperature",
        "top_p",
        "max_tokens",
        "tool_choice",
        "extra_body",
    )
    metadata["config"] = {
        field: _json_safe(getattr(config, field))
        for field in safe_fields
        if hasattr(config, field)
    }
    if hasattr(config, "api_key"):
        metadata["config"]["api_key_present"] = bool(getattr(config, "api_key"))
    return metadata


def _run_summary(run: LlmGameRun) -> dict[str, Any]:
    return {
        "status": run.status,
        "session_id": run.session_id,
        "turns": len(run.turns),
        "failure_reason": run.failure_reason,
        "final_observation": dict(run.final_observation),
        "archive_dir": run.archive_dir,
        "score": None if run.score is None else dict(run.score),
    }


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return deepcopy(value)


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


def _require_optional_int(name: str, value: int | None) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{name} must be an integer or None")


def _require_non_negative(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be >= 0")


_STATE_MUTATING_TOOL_NAMES = {
    "craft_equipment",
    "purchase_upgrade",
    "allocate_experience",
    "equip_item",
    "unequip_item",
    "end_turn",
}
