from __future__ import annotations

import json
import re
from concurrent.futures import ProcessPoolExecutor
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
from guild_manager_bench.bench.llm.formatting import skill_summary, skill_summary_lines
from guild_manager_bench.bench.llm.harness import (
    MemoStore,
    TurnToolHarness,
    memo_entries_from_tool_steps,
)
from guild_manager_bench.bench.llm.prompts import (
    DEFAULT_OBJECTIVE,
    build_endgame_system_prompt,
    build_system_prompt,
    build_turn_prompt,
)
from guild_manager_bench.bench.llm.refs import build_numeric_refs, display_ref
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.bench.llm.trace import (
    LlmGameRun,
    ModelResponseRecord,
    ToolCallRecord,
    TurnTrace,
)
from guild_manager_bench.bench.metrics import compute_rank_score, monster_combat_power, score_final_state
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatStats
from guild_manager_bench.game.presets import describe_data_source, verify_data_source
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition
from guild_manager_bench.game.state import MATERIAL_NAMES, GameDefinition


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
    max_model_steps_per_turn: int = 50
    max_turn_retries: int = 2
    archive_dir: str | Path | None = "runs/llm"
    game_seed: int | None = None
    scoring_seed: int | None = None

    def __post_init__(self) -> None:
        _require_non_negative("max_tool_calls_per_turn", self.max_tool_calls_per_turn)
        _require_positive("max_empty_responses", self.max_empty_responses)
        _require_positive("max_end_turn_attempts", self.max_end_turn_attempts)
        _require_positive("max_model_steps_per_turn", self.max_model_steps_per_turn)
        _require_non_negative("max_turn_retries", self.max_turn_retries)
        _require_optional_int("game_seed", self.game_seed)
        _require_optional_int("scoring_seed", self.scoring_seed)


def run_llm_game(
    agent: LlmTurnAgent,
    *,
    data_dir: str | Path = "data/presets/default",
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
        memo_store = MemoStore()
        archive_writer = _start_archive(config, agent, session_id, data_source)
        initial_observation = session["observation"]
    else:
        session_id = resume["session_id"]
        traces = list(resume["traces"])
        memo_store = MemoStore(entries=list(resume["memo_entries"]))
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
    turn_retry_count = 0

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

    system_prompt = build_system_prompt(
        objective=config.objective,
        max_tool_calls=config.max_tool_calls_per_turn,
        max_battle_preview_per_turn=tools.definition.llm_tools.max_battle_preview_per_turn,
        turn_recovery_rules=initial_observation.get("turn_recovery_rules"),
    )

    try:
        with ProcessPoolExecutor() as rank_executor:
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
                        stats=_compute_run_stats(run, definition),
                    )
                    _emit(emit, "run_completed", run=_run_summary(run))
                    return run

                # 回合级别重试循环：对无异动回合失败自动重试
                memo_snapshot = memo_store.snapshot()
                turn_trace = run_llm_turn(
                    agent,
                    tools,
                    session_id,
                    config=config,
                    memo_store=memo_store,
                    event_sink=emit,
                    trace_update=update_replay,
                    system_prompt=system_prompt,
                )
                if (
                    turn_trace.status == "failed"
                    and _is_retryable(turn_trace)
                    and turn_retry_count < config.max_turn_retries
                ):
                    turn_retry_count += 1
                    traces.append(turn_trace)
                    _emit(
                        emit,
                        "turn_retry",
                        turn=observation["turn"],
                        retry_count=turn_retry_count,
                        total_allowed=config.max_turn_retries,
                        reason=turn_trace.failure_reason,
                    )
                    memo_store.restore(memo_snapshot)
                    continue

                traces.append(turn_trace)
                if turn_trace.status == "completed":
                    turn_trace.rank_score = compute_rank_score(
                        tools.definition,
                        tools.get_state(session_id),
                        executor=rank_executor,
                    )
                    turn_retry_count = 0
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
                        stats=_compute_run_stats(run, definition),
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
    memo_store: MemoStore | None = None,
    event_sink: EventSink | None = None,
    trace_update: Callable[[TurnTrace], None] | None = None,
    system_prompt: str | None = None,
) -> TurnTrace:
    """运行单个游戏回合，直到 end_turn 成功或判定失败。"""

    config = config or LlmRunConfig()
    memo_store = memo_store or MemoStore()
    observation = tools.get_observation(session_id)["observation"]
    previous_turn_event = _previous_turn_event(tools, session_id)
    prompt = build_turn_prompt(
        observation,
        previous_turn_event=previous_turn_event,
        memo_entries=memo_store.consume(),
        endgame_start_turn=tools.definition.llm_tools.endgame_start_turn,
    )
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    endgame_system_prompt = _endgame_system_prompt_for_turn(tools, observation)
    if endgame_system_prompt:
        messages.append({"role": "system", "content": endgame_system_prompt})
    messages.append({"role": "user", "content": prompt})
    turn_trace = TurnTrace(
        turn=observation["turn"],
        prompt=prompt,
        messages=messages,
        observation_before=observation,
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
        max_battle_preview_per_turn=tools.definition.llm_tools.max_battle_preview_per_turn,
        endgame_start_turn=tools.definition.llm_tools.endgame_start_turn,
        memo_store=memo_store,
    )

    empty_responses = 0
    failed_end_turns = 0

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
            if empty_responses == config.max_empty_responses - 1:
                retry = _retry_message_empty_response_last()
            elif empty_responses >= 2:
                retry = _retry_message_empty_response_urgent()
            else:
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
            # Capture observation snapshot after successful write operations
            if call.name in _STATE_MUTATING_TOOL_NAMES and result.get("ok") is True:
                obs_snapshot = tools.get_observation(session_id)["observation"]
                result = dict(result)
                result["_observation_after"] = obs_snapshot
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
        if turn.get("status") != "completed":
            continue
        # 每个 turn 复用同一个 harness，使增量 refs 更新在同一回合内累积
        harness = TurnToolHarness(
            tools, session_id,
            max_tool_calls=1_000_000,
            endgame_start_turn=tools.definition.llm_tools.endgame_start_turn,
        )
        for step in _sequence(turn.get("steps")):
            if isinstance(step, Mapping):
                _replay_confirmed_tool_result(harness, step)

    return {
        "session_id": session_id,
        "replay": replay,
        "traces": _traces_from_replay(replay),
        "memo_entries": memo_entries_from_tool_steps(_sequence(replay.get("turns"))),
    }


def _read_replay(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"replay not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != "llm_replay":
        raise ValueError("replay must be an llm_replay JSON object")
    return data


def _replay_confirmed_tool_result(
    harness: TurnToolHarness,
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
    replayed = harness.call_tool(str(name), arguments)
    if replayed.get("ok") is not True:
        raise ValueError(
            f"failed to replay confirmed tool result {name}: {replayed.get('error')}"
        )


def _replay_tool_step_succeeded(step: Mapping[str, Any]) -> bool:
    content = step.get("content")
    if isinstance(content, str):
        stripped = content.lstrip()
        if stripped.startswith("成功 ") or stripped.startswith("OK "):
            return True
        if stripped.startswith("失败 ") or stripped.startswith("FAIL "):
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
        observation_before=(
            dict(turn.get("observation_before"))
            if isinstance(turn.get("observation_before"), Mapping)
            else None
        ),
    )
    calls_by_id: dict[str, dict[str, Any]] = {}
    for step in _sequence(turn.get("steps")):
        if not isinstance(step, Mapping):
            continue
        step_type = step.get("type")
        if step_type == "system_prompt":
            trace.messages.append(
                {"role": "system", "content": str(step.get("content") or "")}
            )
            continue
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
    rank_score = turn.get("rank_score")
    if isinstance(rank_score, (int, float)):
        trace.rank_score = float(rank_score)
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
        lines.append(f"失败 {name}：{result.get('error', '未知错误')}")
        event = result.get("event")
        if isinstance(event, Mapping) and event.get("summary"):
            lines.append(f"事件: {event['summary']}")
        return "\n".join(_append_budget_lines(lines, result))

    lines.append(f"成功 {name}")
    if name == "get_party":
        _append_party_result_lines(lines, result, _result_refs(result))
    elif name == "get_monsters":
        _append_monsters_result_lines(lines, result, _result_refs(result))
    elif name == "get_crafting":
        _append_crafting_result_lines(lines, result, _result_refs(result))
    elif name == "get_inventory":
        _append_inventory_result_lines(lines, result, _result_refs(result))
    elif name == "get_upgrades":
        _append_upgrades_result_lines(lines, result, _result_refs(result))
    elif name == "get_recruitment":
        _append_recruitment_result_lines(lines, result, _result_refs(result))
    elif name == "get_events" and isinstance(result.get("events"), Sequence):
        _append_events_lines(lines, result["events"])
    elif name == "preview_battle" and isinstance(result.get("preview"), Mapping):
        _append_battle_preview_lines(lines, result["preview"], _result_refs(result))
    elif name == "preview_team_power" and result.get("ok") is True:
        _append_team_power_preview_lines(lines, result, _result_refs(result))
    elif name == "write_memo" and isinstance(result.get("memo"), Mapping):
        memo = result["memo"]
        lines[0] = (
            "成功 write_memo："
            f"已记录备忘 {memo.get('count')} 条，仅下回合出现在提示词中，之后自动消失"
        )
        dropped = memo.get("dropped_oldest")
        if isinstance(dropped, int) and dropped:
            lines.append(f"已丢弃最早 {dropped} 条备忘")
    else:
        event = result.get("event")
        if isinstance(event, Mapping):
            summary = event.get("summary")
            if summary:
                lines[0] = f"成功 {name}：{summary}"
            _append_changes_lines(lines, event.get("changes"), _result_refs(result))
            has_event_battles = _append_battles_lines(
                lines,
                event.get("battles"),
                _result_refs(result),
            )
        else:
            has_event_battles = False
        recruited = result.get("recruited_adventurer")
        if isinstance(recruited, Mapping):
            lines.append("新冒险者详情:")
            _append_adventurer_lines(lines, [recruited], _result_refs(result))
        turn_result = result.get("turn_result")
        if isinstance(turn_result, Mapping):
            if not has_event_battles:
                _append_battles_lines(
                    lines,
                    turn_result.get("battles"),
                    _result_refs(result),
                )
            crafted = turn_result.get("crafted_equipment_ids")
            purchased = turn_result.get("purchased_upgrade_ids")
            recruited = turn_result.get("recruited_adventurer_ids")
            if crafted:
                lines.append(
                    "新装备: "
                    f"{_join_refs(crafted, 'equipment', _result_refs(result))}"
                )
            if purchased:
                lines.append(
                    "已购买升级: "
                    f"{_join_refs(purchased, 'upgrade', _result_refs(result))}"
                )
            if recruited:
                lines.append(
                    "新冒险者: "
                    f"{_join_refs(recruited, 'adventurer', _result_refs(result))}"
                )

    return "\n".join(_append_budget_lines(lines, result))


def _append_battle_preview_lines(
    lines: list[str],
    preview: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    outcome = "胜" if preview.get("won") else "负"
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
        "成功 preview_battle："
        f"冒险者 {display_ref(refs, 'adventurer', preview.get('adventurer_id'))} "
        f"{preview.get('adventurer_name')} vs "
        f"怪物 {display_ref(refs, 'monster', preview.get('monster_id'))} "
        f"{preview.get('monster_name')} {outcome}"
    )
    lines.append(
        "资源: "
        f"冒险者 HP {before.get('current_hp')} -> {after.get('current_hp')}, "
        f"MP {before.get('current_mp')} -> {after.get('current_mp')}; "
        f"怪物剩余 HP {monster_after.get('current_hp')}"
    )
    lines.append(
        "战斗: "
        f"结果 {_combat_outcome_zh(preview.get('outcome'))}；"
        f"原因 {_combat_reason_zh(preview.get('reason'))}；"
        f"动作数 {preview.get('actions_taken')}; 耗时 {preview.get('time_elapsed')}"
    )
    lines.append(f"胜利奖励: {reward_text}")


def _append_team_power_preview_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    rank_score = result.get("rank_score", 0)
    lines[0] = f"成功 preview_team_power：队伍终局战力评分 rank_score = {rank_score}"
    per_adventurer = result.get("per_adventurer")
    if isinstance(per_adventurer, list):
        lines.append("冒险者贡献分解:")
        for entry in per_adventurer:
            if not isinstance(entry, Mapping):
                continue
            adv_id = entry.get("adventurer_id", "")
            ref = display_ref(refs, "adventurer", adv_id)
            score = entry.get("rank_score", 0)
            share = entry.get("rank_score_share", 0)
            lines.append(
                f"  - {ref}: rank_score {score}, 贡献占比 {round(share * 100, 1)}%"
            )


def _append_observation_lines(lines: list[str], observation: Mapping[str, Any]) -> None:
    refs = build_numeric_refs(observation)
    scoring = observation.get("scoring")
    scoring_seed = scoring.get("seed") if isinstance(scoring, Mapping) else None
    lines.append(
        "状态: "
        f"回合 {observation.get('turn')}/{observation.get('max_turns')}; "
        f"seed {observation.get('seed')}; "
        f"评分seed {scoring_seed}; "
        f"金币 {observation.get('gold')}; "
        f"经验池 {observation.get('experience_pool')}; "
        f"材料 {_mapping_inline(observation.get('materials'))}"
    )
    lines.append("冒险者:")
    for adventurer in _sequence(observation.get("adventurers")):
        if isinstance(adventurer, Mapping):
            resources = adventurer.get("resources", {})
            stats = adventurer.get("effective_stats", {})
            if not isinstance(resources, Mapping):
                resources = {}
            if not isinstance(stats, Mapping):
                stats = {}
            equipment = adventurer.get("equipment") or ()
            equipment_text = _equipment_inline(equipment, refs)
            exp_text = _experience_inline(adventurer)
            growth_text = _stat_modifier_inline(adventurer.get("stat_growth_per_level"))
            lines.append(
                "- "
                f"{display_ref(refs, 'adventurer', adventurer.get('adventurer_id'))} "
                f"{adventurer.get('name')} "
                f"Lv{adventurer.get('level')} "
                f"EXP {exp_text} "
                f"每级属性成长 {growth_text} "
                f"HP {resources.get('current_hp')}/{stats.get('hp')} "
                f"MP {resources.get('current_mp')}/{stats.get('mp')} "
                f"攻击 {stats.get('attack')} 防御 {stats.get('defense')} 速度 {stats.get('speed')} "
                f"装备 {equipment_text}"
            )
            _append_skill_lines(lines, adventurer.get("skills"), indent="  ")
            lines.append(f"  升级可学会技能 {_level_skill_unlocks_inline(adventurer.get('level_skill_unlocks'))}")

    monsters = [
        monster
        for monster in _sequence(observation.get("monsters"))
        if isinstance(monster, Mapping)
    ]
    if monsters:
        lines.append("怪物:")
        for monster in monsters:
            stats = monster.get("stats", {})
            if not isinstance(stats, Mapping):
                stats = {}
            reward = monster.get("reward", {})
            reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
            lines.append(
                "- "
                f"{display_ref(refs, 'monster', monster.get('monster_id'))} "
                f"{monster.get('name')} "
                f"HP {stats.get('hp')} 攻击 {stats.get('attack')} 防御 {stats.get('defense')} 速度 {stats.get('speed')} "
                f"奖励 {reward_text}"
            )
            _append_skill_lines(lines, monster.get("skills"), indent="  ")

    recipes = [
        recipe
        for recipe in _sequence(observation.get("crafting_recipes"))
        if isinstance(recipe, Mapping)
    ]
    if recipes:
        lines.append("制作配方:")
        for recipe in recipes:
            availability = "可制作" if recipe.get("can_craft") else "不可制作"
            missing = recipe.get("missing")
            missing_text = _missing_suffix_zh(missing, refs)
            lines.append(
                "- "
                f"{display_ref(refs, 'recipe', recipe.get('recipe_id'))} "
                f"{recipe.get('name')} -> {recipe.get('output_name')} "
                f"槽位 {recipe.get('output_slot')} "
                f"属性 {_mapping_inline(recipe.get('output_stats'))} "
                f"成本 金币 {recipe.get('gold_cost')} 材料 {_mapping_inline(recipe.get('material_costs'))} "
                f"{availability}{missing_text}"
            )
            allowed_class_names = recipe.get("output_allowed_class_names")
            if allowed_class_names:
                lines.append(f"  职业限制 {', '.join(allowed_class_names)}")
            _append_skill_lines(lines, recipe.get("output_skills"), label="产物技能", indent="  ")

    inventory = [
        item
        for item in _sequence(observation.get("equipment_inventory"))
        if isinstance(item, Mapping)
    ]
    if inventory:
        lines.append("装备库存:")
        for item in inventory:
            equipped = (
                display_ref(refs, "adventurer", item.get("equipped_by"))
                if item.get("equipped_by")
                else "空闲"
            )
            allowed_names = item.get("allowed_class_names")
            class_text = f" 职业限制 {', '.join(allowed_names)}" if allowed_names else ""
            lines.append(
                "- "
                f"{display_ref(refs, 'equipment', item.get('instance_id'))} "
                f"{item.get('name')} 槽位 {item.get('slot')} "
                f"属性 {_mapping_inline(item.get('stats'))} "
                f"装备者 {equipped}"
                f"{class_text}"
            )
            _append_skill_lines(lines, item.get("skills"), indent="  ")

    upgrades = [
        upgrade
        for upgrade in _sequence(observation.get("global_upgrades"))
        if isinstance(upgrade, Mapping)
    ]
    if upgrades:
        lines.append("全局升级:")
        for upgrade in upgrades:
            state = (
                "已解锁"
                if upgrade.get("unlocked")
                else "可购买"
                if upgrade.get("can_purchase")
                else "不可购买"
            )
            lines.append(
                "- "
                f"{display_ref(refs, 'upgrade', upgrade.get('upgrade_id'))} "
                f"{upgrade.get('name')} "
                f"金币 {upgrade.get('gold_cost')} "
                f"属性 {_mapping_inline(upgrade.get('stats'))} "
                f"{state}"
            )
            _append_skill_lines(lines, upgrade.get("skills"), indent="  ")


def _append_party_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    rules = result.get("experience_rules")
    if not isinstance(rules, Mapping):
        rules = {}
    lines.append(
        "队伍: "
        f"回合 {result.get('turn')}/{result.get('max_turns')}; "
        f"经验池 {result.get('experience_pool')}; "
        f"升级需求 {rules.get('base_required_experience')}+"
        f"{rules.get('required_experience_growth')}/级; "
        f"最高等级 {rules.get('max_level')}"
    )
    _append_adventurer_lines(lines, result.get("adventurers"), refs)


def _append_monsters_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    lines.append(f"怪物: 回合 {result.get('turn')}/{result.get('max_turns')}")
    _append_monster_lines(lines, result.get("monsters"), refs)


def _append_crafting_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    lines.append(
        "制作资源: "
        f"金币 {result.get('gold')}; "
        f"材料 {_mapping_inline(result.get('materials'))}"
    )
    _append_recipe_lines(lines, result.get("crafting_recipes"), refs)


def _append_inventory_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    _append_inventory_lines(lines, result.get("equipment_inventory"), refs)


def _append_upgrades_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    lines.append(f"升级资源: 金币 {result.get('gold')}")
    _append_upgrade_lines(lines, result.get("global_upgrades"), refs)


def _append_recruitment_result_lines(
    lines: list[str],
    result: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    lines.append(
        "招募: "
        f"回合 {result.get('turn')}/{result.get('max_turns')}; "
        f"金币 {result.get('gold')}; "
        f"队伍 {result.get('party_size')}/{result.get('party_size_limit')}"
    )
    _append_recruit_candidate_lines(lines, result.get("recruit_candidates"), refs)


def _append_adventurer_lines(
    lines: list[str],
    adventurers: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        adventurer
        for adventurer in _sequence(adventurers)
        if isinstance(adventurer, Mapping)
    ]
    if not values:
        lines.append("冒险者: 无")
        return
    lines.append("冒险者:")
    for adventurer in values:
        resources = adventurer.get("resources", {})
        stats = adventurer.get("effective_stats", {})
        if not isinstance(resources, Mapping):
            resources = {}
        if not isinstance(stats, Mapping):
            stats = {}
        exp_text = _experience_inline(adventurer)
        growth_text = _stat_modifier_inline(adventurer.get("stat_growth_per_level"))
        equipment_text = _equipment_slots_inline(adventurer.get("equipment_slots"), refs)
        if equipment_text == "无":
            equipment_text = _equipment_inline(adventurer.get("equipment"), refs)
        lines.append(
            "- "
            f"{display_ref(refs, 'adventurer', adventurer.get('adventurer_id'))} "
            f"{adventurer.get('name')} "
            f"Lv{adventurer.get('level')} "
            f"EXP {exp_text} "
            f"每级属性成长 {growth_text} "
            f"HP {resources.get('current_hp')}/{stats.get('hp')} "
            f"MP {resources.get('current_mp')}/{stats.get('mp')} "
            f"攻击 {stats.get('attack')} 防御 {stats.get('defense')} 速度 {stats.get('speed')} "
            f"战后回血 {stats.get('recovery')} 战后回魔 {stats.get('mp_recovery')} "
            f"装备 {equipment_text}"
        )
        _append_skill_lines(lines, adventurer.get("skills"), indent="  ")
        lines.append(f"  升级可学会技能 {_level_skill_unlocks_inline(adventurer.get('level_skill_unlocks'))}")


def _append_recruit_candidate_lines(
    lines: list[str],
    candidates: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        candidate
        for candidate in _sequence(candidates)
        if isinstance(candidate, Mapping)
    ]
    if not values:
        lines.append("招募候选: 无")
        return
    lines.append("招募候选:")
    for candidate in values:
        availability = "可招募" if candidate.get("can_recruit") else "不可招募"
        missing = candidate.get("missing")
        missing_text = _missing_suffix_zh(missing, refs)
        stats = candidate.get("base_stats")
        if not isinstance(stats, Mapping):
            stats = {}
        lines.append(
            "- "
            f"{display_ref(refs, 'recruit', candidate.get('candidate_id'))} "
            f"{candidate.get('name')} "
            f"模板 {candidate.get('template_id')} "
            f"费用 {candidate.get('recruit_gold')} "
            f"HP {stats.get('hp')} MP {stats.get('mp')} "
            f"攻击 {stats.get('attack')} 防御 {stats.get('defense')} "
            f"速度 {stats.get('speed')} 战后回血 {stats.get('recovery')} 战后回魔 {stats.get('mp_recovery')} "
            f"每级属性成长 {_stat_modifier_inline(candidate.get('stat_growth_per_level'))} "
            f"{availability}{missing_text}"
        )
        _append_skill_lines(lines, candidate.get("skills"), indent="  ")
        lines.append(
            f"  升级可学会技能 {_level_skill_unlocks_inline(candidate.get('level_skill_unlocks'))}"
        )


def _append_monster_lines(
    lines: list[str],
    monsters: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        monster
        for monster in _sequence(monsters)
        if isinstance(monster, Mapping)
    ]
    if not values:
        lines.append("怪物: 无")
        return
    for monster in values:
        stats = monster.get("stats", {})
        if not isinstance(stats, Mapping):
            stats = {}
        reward = monster.get("reward", {})
        reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
        lines.append(
            "- "
            f"{display_ref(refs, 'monster', monster.get('monster_id'))} "
            f"{monster.get('name')} "
            f"HP {stats.get('hp')} MP {stats.get('mp')} "
            f"攻击 {stats.get('attack')} 防御 {stats.get('defense')} "
            f"速度 {stats.get('speed')} 战后回血 {stats.get('recovery')} 战后回魔 {stats.get('mp_recovery')} "
            f"奖励 {reward_text}"
        )
        _append_skill_lines(lines, monster.get("skills"), indent="  ")


def _append_recipe_lines(
    lines: list[str],
    recipes: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        recipe
        for recipe in _sequence(recipes)
        if isinstance(recipe, Mapping)
    ]
    if not values:
        lines.append("制作配方: 无")
        return
    lines.append("制作配方:")
    for recipe in values:
        availability = "可制作" if recipe.get("can_craft") else "不可制作"
        missing = recipe.get("missing")
        missing_text = _missing_suffix_zh(missing, refs)
        lines.append(
            "- "
            f"{display_ref(refs, 'recipe', recipe.get('recipe_id'))} "
            f"{recipe.get('name')} -> {recipe.get('output_name')} "
            f"槽位 {recipe.get('output_slot')} "
            f"属性 {_mapping_inline(recipe.get('output_stats'))} "
            f"成本 金币 {recipe.get('gold_cost')} 材料 {_mapping_inline(recipe.get('material_costs'))} "
            f"{availability}{missing_text}"
        )
        allowed_class_names = recipe.get("output_allowed_class_names")
        if allowed_class_names:
            lines.append(f"  职业限制 {', '.join(allowed_class_names)}")
        _append_skill_lines(lines, recipe.get("output_skills"), label="产物技能", indent="  ")


def _append_inventory_lines(
    lines: list[str],
    inventory: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        item
        for item in _sequence(inventory)
        if isinstance(item, Mapping)
    ]
    if not values:
        lines.append("装备库存: 无")
        return
    free_items = [item for item in values if not item.get("equipped_by")]
    equipped_items = [item for item in values if item.get("equipped_by")]
    lines.append("装备库存:")
    for item in free_items:
        lines.append(
            "- "
            f"{display_ref(refs, 'equipment', item.get('instance_id'))} "
            f"{item.get('name')} 槽位 {item.get('slot')} "
            f"属性 {_mapping_inline(item.get('stats'))} "
            f"空闲"
        )
        allowed_names = item.get("allowed_class_names")
        if allowed_names:
            lines.append(f"  职业限制 {', '.join(allowed_names)}")
        _append_skill_lines(lines, item.get("skills"), indent="  ")
    for item in equipped_items:
        equipped = display_ref(refs, "adventurer", item.get("equipped_by"))
        lines.append(
            f"- {item.get('name')}({display_ref(refs, 'equipment', item.get('instance_id'))}, {item.get('slot')}) [装备者 {equipped}]"
        )


def _format_missing_zh(
    missing: Mapping[str, Any],
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    parts = []
    for key, value in missing.items():
        if key == "gold":
            if isinstance(value, (int, float)) and value:
                parts.append(f"金币 {value}")
        elif key == "already_unlocked":
            parts.append("已解锁")
        elif key == "required_upgrade_ids":
            upgrade_refs = refs.get("upgrade", {})
            names = []
            for uid in _sequence(value):
                ref = upgrade_refs.get(str(uid))
                if ref is not None:
                    names.append(str(ref))
                else:
                    names.append(str(uid))
            parts.append(f"前置升级 {', '.join(names)}")
        elif key == "party_size_limit":
            if isinstance(value, Mapping):
                current = value.get("current", "?")
                limit = value.get("limit", "?")
                parts.append(f"队伍空位（当前 {current}/{limit}）")
            else:
                parts.append(f"队伍空位（{value}）")
        else:
            parts.append(f"{_mapping_key(key)} {value}")
    return "；".join(parts)


def _missing_suffix_zh(
    missing: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    if not isinstance(missing, Mapping) or not missing:
        return ""
    missing_text = _format_missing_zh(missing, refs)
    if not missing_text:
        return ""
    return f"；缺少 {missing_text}"


def _append_upgrade_lines(
    lines: list[str],
    upgrades: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        upgrade
        for upgrade in _sequence(upgrades)
        if isinstance(upgrade, Mapping)
    ]
    if not values:
        lines.append("全局升级: 无")
        return
    lines.append("全局升级:")
    for upgrade in values:
        if upgrade.get("unlocked"):
            lines.append(
                "- "
                f"{display_ref(refs, 'upgrade', upgrade.get('upgrade_id'))} "
                f"{upgrade.get('name')} 已完成"
            )
            continue
        state = (
            "可购买"
            if upgrade.get("can_purchase")
            else "不可购买"
        )
        missing = upgrade.get("missing")
        missing_text = ""
        if isinstance(missing, Mapping) and missing:
            missing_str = _format_missing_zh(missing, refs)
            if missing_str:
                missing_text = f"；缺少 {missing_str}"
        lines.append(
            "- "
            f"{display_ref(refs, 'upgrade', upgrade.get('upgrade_id'))} "
            f"{upgrade.get('name')} "
            f"金币 {upgrade.get('gold_cost')} "
            f"属性 {_mapping_inline(upgrade.get('stats'))} "
            f"队伍上限+{upgrade.get('party_size_bonus', 0)} "
            f"{state}{missing_text}"
        )
        _append_skill_lines(lines, upgrade.get("skills"), indent="  ")


def _append_events_lines(lines: list[str], events: Sequence[Any]) -> None:
    if not events:
        lines.append("事件: 无")
        return
    lines.append("事件:")
    for event in events:
        if isinstance(event, Mapping):
            lines.append(
                "- "
                f"#{event.get('sequence')} T{event.get('turn')} {event.get('type')}: "
                f"{event.get('summary', '')}"
            )


def _experience_inline(adventurer: Mapping[str, Any]) -> str:
    current = adventurer.get("experience")
    next_level = adventurer.get("next_level")
    if not isinstance(next_level, Mapping):
        return str(current)
    if next_level.get("max_level"):
        return f"{current}/MAX"
    return f"{current}/{next_level.get('required')}"


def _append_skill_lines(
    lines: list[str],
    skills: Any,
    *,
    label: str = "技能",
    indent: str = "",
) -> None:
    values = skill_summary_lines(skills)
    if not values:
        lines.append(f"{indent}{label}: 无")
        return
    lines.append(f"{indent}{label}:")
    for value in values:
        lines.append(f"{indent}  - {value}")


def _level_skill_unlocks_inline(unlocks: Any) -> str:
    values = [
        unlock
        for unlock in _sequence(unlocks)
        if isinstance(unlock, Mapping) and not unlock.get("unlocked")
    ]
    if not values:
        return "无"
    parts = []
    for unlock in values:
        skills = " / ".join(skill_summary_lines(unlock.get("skills"))) or "无"
        parts.append(
            f"Lv{unlock.get('level')} {skills}"
        )
    return "; ".join(parts)


def _append_changes_lines(
    lines: list[str],
    changes: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    values = [
        change
        for change in _sequence(changes)
        if isinstance(change, Mapping)
    ]
    if not values:
        return
    lines.append("变化:")
    for change in values:
        label = change.get("label") or change.get("field") or change.get("type") or "change"
        if "before" in change:
            lines.append(
                f"- {label}: "
                f"{_change_value_text(change.get('before'), refs)} -> "
                f"{_change_value_text(change.get('after'), refs)}"
            )
        else:
            lines.append(f"- {label}: {_change_value_text(change.get('after'), refs)}")


def _append_battles_lines(
    lines: list[str],
    battles: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> bool:
    values = [
        battle
        for battle in _sequence(battles)
        if isinstance(battle, Mapping)
    ]
    if not values:
        return False
    lines.append("战斗:")
    for battle in values:
        outcome = "胜" if battle.get("won") else "负"
        reward = battle.get("reward")
        reward_text = _reward_inline(reward) if isinstance(reward, Mapping) else "{}"
        lines.append(
            "- "
            f"{_battle_participant_text(battle, 'adventurer', refs)} vs "
            f"{_battle_participant_text(battle, 'monster', refs)}: {outcome}; "
            f"奖励 {reward_text}"
        )
    return True


def _change_value_text(
    value: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    if isinstance(value, str):
        return _replace_known_ids(value, refs)
    return str(value)


def _replace_known_ids(
    value: str,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    text = value
    labels = {
        "adventurer": "冒险者",
        "monster": "怪物",
        "recipe": "配方",
        "upgrade": "升级",
        "recruit": "招募候选",
        "equipment": "装备",
    }
    for category, category_refs in refs.items():
        label = labels.get(category, category)
        if not isinstance(category_refs, Mapping):
            continue
        for canonical_id, ref in category_refs.items():
            text = text.replace(str(canonical_id), f"{label}{ref}")
    return text


def _append_budget_lines(lines: list[str], result: Mapping[str, Any]) -> list[str]:
    budget = result.get("tool_budget")
    if isinstance(budget, Mapping):
        suffix = "；仅允许 end_turn" if budget.get("end_turn_required") else ""
        bp_used = budget.get("battle_preview_used")
        bp_limit = budget.get("battle_preview_limit")
        bp_text = (
            f"；preview_battle {bp_used}/{bp_limit}"
            if isinstance(bp_used, int) and isinstance(bp_limit, int)
            else ""
        )
        lines.append(
            "预算: "
            f"已用 {budget.get('used')}/{budget.get('max_tool_calls')}，"
            f"剩余 {budget.get('remaining')}{bp_text}{suffix}"
        )
    return lines


def _battle_participant_text(
    battle: Mapping[str, Any],
    role: str,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    name = battle.get(f"{role}_name")
    category = "adventurer" if role == "adventurer" else "monster"
    raw_id = battle.get(f"{role}_id")
    ref = refs.get(category, {}).get(str(raw_id))
    if isinstance(name, str) and name:
        return f"{ref} {name}" if ref is not None else name
    return str(ref) if ref is not None else str(raw_id)


def _equipment_inline(
    equipment: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    items = [
        item
        for item in _sequence(equipment)
        if isinstance(item, Mapping)
    ]
    if not items:
        return "无"
    return ", ".join(
        display_ref(refs, "equipment", item.get("instance_id"))
        for item in items
    )


def _equipment_slots_inline(
    slots: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    values = []
    for slot in _sequence(slots):
        if not isinstance(slot, Mapping):
            continue
        item = slot.get("item")
        if not isinstance(item, Mapping):
            continue
        values.append(
            f"{item.get('name')}(id={display_ref(refs, 'equipment', item.get('instance_id'))}, "
            f"{item.get('slot')})"
        )
    return ", ".join(values) if values else "无"


def _reward_inline(reward: Mapping[str, Any]) -> str:
    return (
        "{"
        f"金币:{reward.get('gold', 0)}, "
        f"经验:{reward.get('experience', 0)}, "
        f"材料:{_mapping_inline(reward.get('materials'))}"
        "}"
    )


def _combat_outcome_zh(value: Any) -> str:
    labels = {
        "left_win": "冒险者胜利",
        "right_win": "怪物胜利",
        "draw": "平局",
    }
    return labels.get(value, str(value))


def _combat_reason_zh(value: Any) -> str:
    labels = {
        "target_defeated": "目标被击败",
        "actor_defeated": "行动者被击败",
        "status_defeated_actor": "行动者被状态效果击败",
        "both_defeated": "双方同时被击败",
        "max_actions_reached": "达到最大行动次数",
        "no_combatant_can_act": "双方都无法行动",
        "right_already_defeated": "右侧已被击败",
        "left_already_defeated": "左侧已被击败",
        "both_already_defeated": "双方已被击败",
    }
    return labels.get(value, str(value))


def _mapping_inline(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "{}"
    return "{" + ", ".join(f"{_mapping_key(key)}:{item}" for key, item in value.items()) + "}"


def _stat_modifier_inline(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "无"
    parts = []
    for key in ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery"):
        amount = value.get(key, 0)
        if isinstance(amount, int | float) and amount:
            parts.append(f"{_mapping_key(key)}+{_number_inline(amount)}")
    return " ".join(parts) if parts else "无"


def _number_inline(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _mapping_key(value: Any) -> str:
    labels = {
        "hp": "HP",
        "mp": "MP",
        "attack": "攻击",
        "defense": "防御",
        "speed": "速度",
        "recovery": "战后回血",
        "mp_recovery": "战后回魔",
        **MATERIAL_NAMES,
    }
    return labels.get(value, str(value))


def _join_values(value: Any) -> str:
    return ", ".join(str(item) for item in _sequence(value))


def _join_refs(
    value: Any,
    category: str,
    refs: Mapping[str, Mapping[str, int]],
) -> str:
    return ", ".join(
        display_ref(refs, category, item)
        for item in _sequence(value)
    )


def _result_refs(result: Mapping[str, Any]) -> Mapping[str, Mapping[str, int]]:
    refs = result.get("_llm_refs")
    return refs if isinstance(refs, Mapping) else {}


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


def _endgame_system_prompt_for_turn(
    tools: GuildManagerTools,
    observation: Mapping[str, Any],
) -> str | None:
    endgame_start_turn = tools.definition.llm_tools.endgame_start_turn
    if endgame_start_turn is None:
        return None
    turn = observation.get("turn")
    max_turns = observation.get("max_turns")
    if not isinstance(turn, int) or not isinstance(max_turns, int):
        return None
    if turn < endgame_start_turn:
        return None
    return build_endgame_system_prompt(turn, max_turns)


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


def _is_retryable(turn: TurnTrace) -> bool:
    """回合是否可安全重试：没有任何成功执行的改变游戏状态的工具调用。"""
    return not any(
        call.name in _STATE_MUTATING_TOOL_NAMES and call.ok is True
        for call in turn.tool_calls
    )


def _retry_message_empty_response() -> dict[str, str]:
    return {
        "role": "user",
        "content": "你本回合还没有调用任何工具。必须调用工具推进游戏，并最终成功调用 end_turn。",
    }


def _retry_message_empty_response_urgent() -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "你已经连续多次没有调用任何工具了。必须立刻调用工具（如 get_party、get_monsters 等查询工具），"
            "然后调用 end_turn 提交讨伐列表结束本回合。否则本回合将失败。"
        ),
    }


def _retry_message_empty_response_last() -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "这是本回合最后一次机会。如果你还不调用任何工具并调用 end_turn，本回合将判定失败。"
            "请立即调用工具。"
        ),
    }


def _retry_message_end_turn_failed(result: Mapping[str, Any]) -> dict[str, str]:
    return {
        "role": "user",
        "content": f"end_turn 调用失败：{result.get('error', '未知错误')}。请修正讨伐列表后再次调用 end_turn。",
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
    stats: Mapping[str, Any] | None = None,
) -> None:
    if archive_writer is None:
        return
    turns = [t for t in traces if t.status is not None]
    if active_turn is not None and active_turn.status is not None and active_turn not in turns:
        turns.append(active_turn)
    archive_writer.write_replay(
        status=status,
        turns=turns,
        final_observation=final_observation,
        failure_reason=failure_reason,
        score=score,
        stats=stats,
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
        "max_model_steps_per_turn": config.max_model_steps_per_turn,
        "max_turn_retries": config.max_turn_retries,
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


def _compute_run_stats(
    run: LlmGameRun,
    definition: GameDefinition | None = None,
) -> dict[str, Any]:
    """从 LlmGameRun 轨迹中聚合全面的运行统计数据。"""

    # Timing accumulators
    total_duration_ms: float = 0.0

    # Token accumulators
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    # Tool call accumulators
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    calls_by_name: dict[str, int] = {}
    calls_by_name_detail: dict[str, dict[str, int]] = {}

    # Game action accumulators
    battles_total: int = 0
    battles_won: int = 0
    battles_lost: int = 0
    total_gold_earned: int = 0
    total_experience_earned: int = 0
    total_equipment_crafted: int = 0
    total_upgrades_purchased: int = 0
    total_recruits: int = 0
    total_dismissals: int = 0
    total_experience_allocated: int = 0
    total_equips: int = 0
    total_unequips: int = 0
    cumulative_gold_earned: int = 0
    cumulative_experience_earned: int = 0
    economy_curve: list[dict[str, int]] = []
    strongest_defeated_enemy: dict[str, Any] | None = None

    # Model interaction accumulators
    total_model_steps: int = 0
    total_turns_completed: int = 0
    total_turns_failed: int = 0

    for turn in run.turns:
        tool_content_by_id, tool_content_by_name = _tool_result_content_lookup(
            turn.messages
        )
        turn_gold_earned = 0
        turn_experience_earned = 0

        # Model interaction
        total_model_steps += len(turn.model_responses)
        if turn.status == "completed":
            total_turns_completed += 1
        elif turn.status == "failed":
            total_turns_failed += 1

        # Timing and token usage from model responses
        for record in turn.model_responses:
            timing = record.timing
            if isinstance(timing, dict):
                ms = timing.get("duration_ms")
                if isinstance(ms, (int, float)):
                    total_duration_ms += float(ms)

            usage = record.usage
            if isinstance(usage, dict):
                inp = usage.get("input_tokens")
                if not isinstance(inp, (int, float)):
                    inp = usage.get("prompt_tokens")
                if isinstance(inp, (int, float)):
                    input_tokens += int(inp)

                out = usage.get("output_tokens")
                if not isinstance(out, (int, float)):
                    out = usage.get("completion_tokens")
                if isinstance(out, (int, float)):
                    output_tokens += int(out)

                cr = usage.get("cache_read_input_tokens")
                if isinstance(cr, (int, float)):
                    cache_read_input_tokens += int(cr)

                cc = usage.get("cache_creation_input_tokens")
                if isinstance(cc, (int, float)):
                    cache_creation_input_tokens += int(cc)

        # Tool call stats and game actions
        for call in turn.tool_calls:
            total_tool_calls += 1
            calls_by_name[call.name] = calls_by_name.get(call.name, 0) + 1
            call_detail = calls_by_name_detail.setdefault(
                call.name,
                {"total": 0, "successful": 0, "failed": 0},
            )
            call_detail["total"] += 1

            tool_content = _tool_result_content_for_call(
                call,
                tool_content_by_id,
                tool_content_by_name,
            )
            call_ok = call.ok
            if call_ok is None:
                call_ok = _tool_content_ok(tool_content)

            if call_ok is True:
                successful_tool_calls += 1
                call_detail["successful"] += 1

                # Game action counts by tool name
                if call.name == "craft_equipment":
                    total_equipment_crafted += 1
                elif call.name == "purchase_upgrade":
                    total_upgrades_purchased += 1
                elif call.name == "allocate_experience":
                    total_experience_allocated += 1
                elif call.name == "recruit_adventurer":
                    total_recruits += 1
                elif call.name == "dismiss_adventurer":
                    total_dismissals += 1
                elif call.name == "equip_item":
                    total_equips += 1
                elif call.name == "unequip_item":
                    total_unequips += 1
                elif call.name == "end_turn":
                    stats = _battle_stats_from_tool_result(call.result)
                    if stats is None:
                        stats = _battle_stats_from_tool_content(tool_content)
                    battles_total += stats["battles_total"]
                    battles_won += stats["battles_won"]
                    battles_lost += stats["battles_lost"]
                    total_gold_earned += stats["gold_earned"]
                    total_experience_earned += stats["experience_earned"]
                    turn_gold_earned += stats["gold_earned"]
                    turn_experience_earned += stats["experience_earned"]
                    enemy = _strongest_defeated_enemy_from_tool_result(
                        call.result,
                        turn.observation_before,
                        turn.turn,
                        definition=definition,
                    )
                    if enemy is None:
                        enemy = _strongest_defeated_enemy_from_tool_content(
                            tool_content,
                            turn.observation_before,
                            turn.turn,
                            definition=definition,
                        )
                    strongest_defeated_enemy = _stronger_enemy(
                        strongest_defeated_enemy,
                        enemy,
                    )
            else:
                failed_tool_calls += 1
                call_detail["failed"] += 1

        if turn.status == "completed":
            cumulative_gold_earned += turn_gold_earned
            cumulative_experience_earned += turn_experience_earned
            economy_curve.append(
                {
                    "turn": turn.turn,
                    "gold_earned": turn_gold_earned,
                    "experience_earned": turn_experience_earned,
                    "cumulative_gold_earned": cumulative_gold_earned,
                    "cumulative_experience_earned": cumulative_experience_earned,
                }
            )

    # Build result
    token_usage: dict[str, int] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cache_read_input_tokens > 0:
        token_usage["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens > 0:
        token_usage["cache_creation_input_tokens"] = cache_creation_input_tokens

    return {
        "timing": {
            "total_duration_ms": round(total_duration_ms),
            "total_duration_seconds": round(total_duration_ms / 1000, 3),
        },
        "tool_calls": {
            "total": total_tool_calls,
            "successful": successful_tool_calls,
            "failed": failed_tool_calls,
            "by_name": calls_by_name,
            "by_name_detail": calls_by_name_detail,
        },
        "token_usage": token_usage,
        "game_actions": {
            "battles_total": battles_total,
            "battles_won": battles_won,
            "battles_lost": battles_lost,
            "total_gold_earned": total_gold_earned,
            "total_experience_earned": total_experience_earned,
            "total_equipment_crafted": total_equipment_crafted,
            "total_upgrades_purchased": total_upgrades_purchased,
            "total_recruits": total_recruits,
            "total_dismissals": total_dismissals,
            "total_experience_allocated": total_experience_allocated,
            "total_equips": total_equips,
            "total_unequips": total_unequips,
            "economy_curve": economy_curve,
            "strongest_defeated_enemy": strongest_defeated_enemy,
        },
        "model_interaction": {
            "total_model_steps": total_model_steps,
            "total_turns_completed": total_turns_completed,
            "total_turns_failed": total_turns_failed,
        },
    }


def _tool_result_content_lookup(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    by_id: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        call_id = message.get("tool_call_id")
        if isinstance(call_id, str):
            by_id[call_id] = content
        name = message.get("name")
        if isinstance(name, str):
            by_name.setdefault(name, []).append(content)
    return by_id, by_name


def _tool_result_content_for_call(
    call: ToolCallRecord,
    by_id: Mapping[str, str],
    by_name: dict[str, list[str]],
) -> str:
    if call.call_id is not None:
        content = by_id.get(call.call_id)
        if content is not None:
            return content
    values = by_name.get(call.name)
    if values:
        return values.pop(0)
    return ""


def _tool_content_ok(content: str) -> bool | None:
    stripped = content.lstrip()
    if stripped.startswith("OK ") or stripped.startswith("成功 "):
        return True
    if stripped.startswith("FAIL ") or stripped.startswith("失败 "):
        return False
    parsed = _parse_json_mapping(stripped)
    if parsed is not None:
        ok = parsed.get("ok")
        if isinstance(ok, bool):
            return ok
    return None


def _battle_stats_from_tool_result(result: Mapping[str, Any]) -> dict[str, int] | None:
    turn_result = result.get("turn_result")
    if not isinstance(turn_result, Mapping):
        return None
    battles = turn_result.get("battles")
    if not isinstance(battles, Sequence) or isinstance(battles, str):
        return None

    stats = _empty_battle_stats()
    for battle in battles:
        if not isinstance(battle, Mapping):
            continue
        stats["battles_total"] += 1
        won = _battle_won(battle)
        if won is True:
            stats["battles_won"] += 1
        elif won is False:
            stats["battles_lost"] += 1
        reward = battle.get("reward")
        if isinstance(reward, Mapping):
            gold = reward.get("gold")
            if isinstance(gold, (int, float)):
                stats["gold_earned"] += int(gold)
            exp = reward.get("experience")
            if isinstance(exp, (int, float)):
                stats["experience_earned"] += int(exp)
    return stats


def _strongest_defeated_enemy_from_tool_result(
    result: Mapping[str, Any],
    observation_before: Mapping[str, Any] | None,
    turn_number: int,
    *,
    definition: GameDefinition | None = None,
) -> dict[str, Any] | None:
    turn_result = result.get("turn_result")
    if not isinstance(turn_result, Mapping):
        return None
    battles = turn_result.get("battles")
    if not isinstance(battles, Sequence) or isinstance(battles, str):
        return None

    best: dict[str, Any] | None = None
    for battle in battles:
        if not isinstance(battle, Mapping):
            continue
        if _battle_won(battle) is not True:
            continue
        candidate = _defeated_enemy_from_battle(
            battle,
            observation_before,
            turn_number,
            definition=definition,
        )
        best = _stronger_enemy(best, candidate)
    return best


def _strongest_defeated_enemy_from_tool_content(
    content: str,
    observation_before: Mapping[str, Any] | None,
    turn_number: int,
    *,
    definition: GameDefinition | None = None,
) -> dict[str, Any] | None:
    if not content:
        return None

    best: dict[str, Any] | None = None
    for line in content.splitlines():
        if not line.lstrip().startswith("-") or " vs " not in line:
            continue
        match = re.match(
            r"^\s*-\s+(?:(\d+)\s+)?(.+?)\s+vs\s+(?:(\d+)\s+)?(.+?)[:：]\s*([^;；]+)",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue
        outcome = match.group(5).strip().lower()
        if "负" in outcome or outcome in {
            "right_win",
            "monster_win",
            "enemy_win",
            "loss",
            "lost",
            "defeat",
        }:
            continue
        if "胜" not in outcome and outcome not in {
            "left_win",
            "adventurer_win",
            "player_win",
            "win",
            "won",
            "victory",
        }:
            continue

        battle: dict[str, Any] = {"monster_name": match.group(4).strip()}
        monster_id = _monster_id_from_observation_ref(observation_before, match.group(3))
        if monster_id is not None:
            battle["monster_id"] = monster_id
        candidate = _defeated_enemy_from_battle(
            battle,
            observation_before,
            turn_number,
            definition=definition,
        )
        best = _stronger_enemy(best, candidate)
    return best


def _monster_id_from_observation_ref(
    observation: Mapping[str, Any] | None,
    ref: str | None,
) -> str | None:
    if ref is None:
        return None
    try:
        index = int(ref) - 1
    except ValueError:
        return None
    monsters = observation.get("monsters") if isinstance(observation, Mapping) else None
    if not isinstance(monsters, Sequence) or isinstance(monsters, str):
        return None
    if index < 0 or index >= len(monsters):
        return None
    monster = monsters[index]
    if not isinstance(monster, Mapping):
        return None
    monster_id = monster.get("monster_id")
    return str(monster_id) if monster_id is not None else None


def _combat_stats_from_numeric(stats: Mapping[str, int]) -> CombatStats | None:
    """从数值字典构造 CombatStats，缺少必要字段时返回 None。"""
    try:
        return CombatStats(
            hp=int(stats.get("hp", 0)),
            mp=int(stats.get("mp", 0)),
            attack=int(stats.get("attack", 0)),
            defense=int(stats.get("defense", 0)),
            speed=int(stats.get("speed", 0)),
            recovery=int(stats.get("recovery", 0)),
            mp_recovery=int(stats.get("mp_recovery", 0)),
        )
    except (ValueError, TypeError):
        return None


def _skills_from_observation(monster: Mapping[str, Any]) -> tuple[Skill, ...] | None:
    """从 observation 中的怪物数据提取技能元组。"""
    skills_data = monster.get("skills")
    if not isinstance(skills_data, list):
        return None
    skills: list[Skill] = []
    for item in skills_data:
        if not isinstance(item, Mapping):
            continue
        try:
            effects = item.get("effects", [])
            if not isinstance(effects, list):
                effects = []
            skills.append(Skill(
                skill_id=str(item.get("skill_id") or item.get("name") or "skill"),
                name=str(item.get("name") or item.get("skill_id") or "skill"),
                kind=item.get("kind", "active"),
                condition=_skill_condition_from_mapping(item.get("condition")),
                effects=tuple(
                    _skill_effect_from_mapping(e)
                    for e in effects
                    if isinstance(e, Mapping)
                ),
                mp_cost=int(item.get("mp_cost", 0)),
                priority=int(item.get("priority", 0)),
                once_per_battle=bool(item.get("once_per_battle", False)),
                free=bool(item.get("free", False)),
            ))
        except (ValueError, TypeError):
            continue
    return tuple(skills) if skills else None


def _skill_condition_from_mapping(data: Any) -> SkillCondition:
    if not isinstance(data, Mapping):
        return SkillCondition(condition_type="always")
    children = data.get("conditions", [])
    if not isinstance(children, list):
        children = []
    return SkillCondition(
        condition_type=data.get("type", "always"),
        value=data.get("value"),
        conditions=tuple(_skill_condition_from_mapping(item) for item in children),
    )


def _skill_effect_from_mapping(data: Mapping[str, Any]) -> SkillEffect:
    status_data = data.get("status")
    return SkillEffect(
        effect_type=data.get("type", "damage_multiplier"),  # type: ignore[arg-type]
        value=data.get("value", 0),
        stat=data.get("stat"),
        target=data.get("target") or "target",
        status=(
            _status_from_mapping(status_data)
            if isinstance(status_data, Mapping)
            else None
        ),
    )


def _status_from_mapping(data: Mapping[str, Any]) -> StatusDefinition:
    effects = data.get("effects", [])
    if not isinstance(effects, list):
        effects = []
    return StatusDefinition(
        status_id=str(data.get("status_id") or data.get("name") or "status"),
        name=str(data.get("name") or data.get("status_id") or "status"),
        duration=int(data.get("duration", 0)),
        effects=tuple(
            _skill_effect_from_mapping(e)
            for e in effects
            if isinstance(e, Mapping)
        ),
        polarity=data.get("polarity", "neutral"),
        stack_mode=data.get("stack_mode", "refresh"),
    )


def _defeated_enemy_from_battle(
    battle: Mapping[str, Any],
    observation_before: Mapping[str, Any] | None,
    turn_number: int,
    *,
    definition: GameDefinition | None = None,
) -> dict[str, Any] | None:
    monster = _monster_from_observation(battle, observation_before)
    stats_source = None
    if isinstance(monster, Mapping):
        stats_source = monster.get("stats")
    if not isinstance(stats_source, Mapping):
        stats_source = battle.get("monster_stats")
    if not isinstance(stats_source, Mapping):
        stats_source = battle.get("stats")
    if not isinstance(stats_source, Mapping):
        return None

    stats = _numeric_stat_map(stats_source)
    if not stats:
        return None

    reward_source = (
        monster.get("reward")
        if isinstance(monster, Mapping) and isinstance(monster.get("reward"), Mapping)
        else battle.get("reward")
    )
    reward = _numeric_stat_map(reward_source) if isinstance(reward_source, Mapping) else {}

    monster_id = (
        monster.get("monster_id")
        if isinstance(monster, Mapping)
        else battle.get("monster_id")
    )
    name = (
        battle.get("monster_name")
        or (monster.get("name") if isinstance(monster, Mapping) else None)
        or battle.get("monster")
        or monster_id
    )

    # 尝试基于战斗模拟计算怪物强度，fallback 到静态公式
    power: float | int = _monster_power(stats)
    if definition is not None and isinstance(monster, Mapping):
        combat_stats = _combat_stats_from_numeric(stats)
        monster_skills = _skills_from_observation(monster)
        if combat_stats is not None and monster_skills is not None:
            try:
                power = monster_combat_power(definition, combat_stats, monster_skills)
            except Exception:
                pass  # fallback to static formula

    result: dict[str, Any] = {
        "turn": turn_number,
        "monster_id": None if monster_id is None else str(monster_id),
        "name": None if name is None else str(name),
        "power": power,
        "stats": stats,
    }
    if reward:
        result["reward"] = reward
    if isinstance(monster, Mapping):
        for key in ("tier", "archetype_id"):
            value = monster.get(key)
            if value is not None:
                result[key] = value
    return result


def _monster_from_observation(
    battle: Mapping[str, Any],
    observation: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    monsters = observation.get("monsters") if isinstance(observation, Mapping) else None
    if not isinstance(monsters, Sequence) or isinstance(monsters, str):
        return None

    monster_id = battle.get("monster_id")
    if monster_id is not None:
        monster_id_text = str(monster_id)
        for monster in monsters:
            if (
                isinstance(monster, Mapping)
                and str(monster.get("monster_id")) == monster_id_text
            ):
                return monster

    monster_name = battle.get("monster_name") or battle.get("monster")
    if monster_name is not None:
        monster_name_text = str(monster_name)
        for monster in monsters:
            if isinstance(monster, Mapping) and monster.get("name") == monster_name_text:
                return monster
    return None


def _numeric_stat_map(values: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in values.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[str(key)] = int(value)
    return result


def _monster_power(stats: Mapping[str, int]) -> int:
    return (
        int(stats.get("hp", 0))
        + int(stats.get("mp", 0))
        + int(stats.get("attack", 0)) * 8
        + int(stats.get("defense", 0)) * 8
        + int(stats.get("speed", 0)) * 5
        + int(stats.get("recovery", 0)) * 5
        + int(stats.get("mp_recovery", 0)) * 5
    )


def _stronger_enemy(
    current: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    current_power = current.get("power")
    candidate_power = candidate.get("power")
    if not isinstance(current_power, (int, float)):
        return candidate
    if not isinstance(candidate_power, (int, float)):
        return current
    return candidate if candidate_power > current_power else current


def _battle_won(battle: Mapping[str, Any]) -> bool | None:
    won = battle.get("won")
    if isinstance(won, bool):
        return won
    outcome = (
        battle.get("outcome")
        or battle.get("result")
        or battle.get("winner")
        or battle.get("status")
    )
    if not isinstance(outcome, str):
        return None
    value = outcome.lower()
    if value in {"left_win", "adventurer_win", "player_win", "win", "won", "victory"}:
        return True
    if value in {"right_win", "monster_win", "enemy_win", "loss", "lost", "defeat"}:
        return False
    return None


def _battle_stats_from_tool_content(content: str) -> dict[str, int]:
    stats = _empty_battle_stats()
    summary = re.search(r"(\d+)\s*场战斗[,，]\s*(\d+)\s*胜\s*(\d+)\s*负", content)
    if summary:
        stats["battles_total"] += int(summary.group(1))
        stats["battles_won"] += int(summary.group(2))
        stats["battles_lost"] += int(summary.group(3))

    reward_lines = [
        line
        for line in content.splitlines()
        if "奖励" in line or "reward" in line.lower()
    ]
    battle_reward_lines = [
        line
        for line in reward_lines
        if line.lstrip().startswith("-")
    ]
    for line in battle_reward_lines or reward_lines:
        gold = re.search(r"(?:金币|gold)\s*[:=＝]\s*(\d+)", line, re.IGNORECASE)
        if gold:
            stats["gold_earned"] += int(gold.group(1))
        exp = re.search(
            r"(?:经验|experience|exp)\s*[:=＝]\s*(\d+)",
            line,
            re.IGNORECASE,
        )
        if exp:
            stats["experience_earned"] += int(exp.group(1))
    return stats


def _empty_battle_stats() -> dict[str, int]:
    return {
        "battles_total": 0,
        "battles_won": 0,
        "battles_lost": 0,
        "gold_earned": 0,
        "experience_earned": 0,
    }


def _run_summary(run: LlmGameRun) -> dict[str, Any]:
    return {
        "status": run.status,
        "session_id": run.session_id,
        "turns": len(run.turns),
        "failure_reason": run.failure_reason,
        "final_observation": dict(run.final_observation),
        "archive_dir": run.archive_dir,
        "score": None if run.score is None else dict(run.score),
        "stats": _compute_run_stats(run),
    }


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return deepcopy(value)


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
    "recruit_adventurer",
    "dismiss_adventurer",
    "equip_item",
    "unequip_item",
    "end_turn",
}


def rebuild_replay_observations(
    replay: dict[str, Any],
    *,
    data_dir: str | Path = "data/presets/default",
) -> dict[str, Any]:
    """为没有 observation_before 的旧 replay 重建回合状态快照。

    通过在全新 GameSession 上重放所有变更工具调用来重建每个回合开始时的游戏状态。
    读操作不改变状态因此跳过，只重放写操作和 end_turn。
    """

    turns = _sequence(replay.get("turns"))
    has_observations = any(
        isinstance(t, Mapping) and t.get("observation_before") is not None
        for t in turns
    )
    if has_observations or not turns:
        return dict(replay)

    tools = GuildManagerTools(load_game_definition(data_dir))
    session = tools.start_session()
    session_id = session["session_id"]

    rebuilt_turns: list[dict[str, Any]] = []
    for turn_data in turns:
        if not isinstance(turn_data, Mapping):
            continue
        if turn_data.get("status") != "completed":
            continue

        observation_before = tools.get_observation(session_id)["observation"]
        rebuilt_steps: list[dict[str, Any]] = []

        for step in _sequence(turn_data.get("steps")):
            step_copy = dict(step) if isinstance(step, Mapping) else {}
            rebuilt_steps.append(step_copy)
            if not isinstance(step, Mapping):
                continue
            if step.get("type") != "tool_result":
                continue
            name = step.get("name")
            if name not in _STATE_MUTATING_TOOL_NAMES:
                continue
            if not _replay_tool_step_succeeded(step):
                continue
            arguments = _replay_tool_arguments(step)
            harness = TurnToolHarness(
                tools, session_id,
                max_tool_calls=1_000_000,
                endgame_start_turn=tools.definition.llm_tools.endgame_start_turn,
            )
            result = harness.call_tool(str(name), arguments)
            if result.get("ok") is not True:
                raise ValueError(
                    f"重建失败: {name} 参数 {arguments}: {result.get('error')}"
                )
            # Capture observation after this mutation
            obs_after = tools.get_observation(session_id)["observation"]
            step_copy["observation_after"] = dict(obs_after)

        turn_copy = dict(turn_data)
        turn_copy["observation_before"] = dict(observation_before)
        turn_copy["steps"] = rebuilt_steps
        rebuilt_turns.append(turn_copy)

    result = dict(replay)
    result["turns"] = rebuilt_turns
    result["_observations_rebuilt"] = True
    return result
