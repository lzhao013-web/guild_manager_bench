from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.llm.trace import LlmGameRun, TurnTrace


@dataclass(frozen=True, slots=True)
class LlmRunArchive:
    """一次 LLM benchmark 留档产物。"""

    directory: Path
    trace_jsonl_path: Path
    replay_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "directory": str(self.directory),
            "trace_jsonl_path": str(self.trace_jsonl_path),
            "replay_path": str(self.replay_path),
        }


class LlmRunArchiveWriter:
    """增量写入 LLM benchmark 留档。"""

    def __init__(
        self,
        base_dir: str | Path,
        *,
        session_id: str,
        config: Mapping[str, Any],
        agent: Mapping[str, Any],
        data_source: Mapping[str, Any] | None = None,
        directory: str | Path | None = None,
        created_at: str | None = None,
        resume_history: Sequence[Mapping[str, Any]] = (),
        append_existing: bool = False,
    ) -> None:
        self.created_at = created_at or _timestamp()
        self.updated_at = self.created_at
        self.session_id = session_id
        self.config = dict(config)
        self.agent = dict(agent)
        self.data_source = dict(data_source or {})
        self.resume_history = [dict(item) for item in resume_history]
        self.directory = (
            Path(directory)
            if directory is not None
            else Path(base_dir) / _run_directory_name(self.created_at, session_id)
        )
        self.directory.mkdir(parents=True, exist_ok=append_existing)
        self.trace_jsonl_path = self.directory / "trace.jsonl"
        self.replay_path = self.directory / "replay.json"
        self.archive = LlmRunArchive(
            directory=self.directory,
            trace_jsonl_path=self.trace_jsonl_path,
            replay_path=self.replay_path,
        )
        record_type = "archive_resumed" if append_existing else "archive_started"
        payload = {
            "archive": self.archive.to_dict(),
            "session_id": session_id,
            "config": self.config,
            "agent": self.agent,
            "data": self.data_source,
        }
        if append_existing:
            payload["resume_index"] = len(self.resume_history)
        self.append_record(record_type, payload)

    def append_event(self, event: Mapping[str, Any]) -> None:
        """把一个运行事件追加到 trace.jsonl。"""

        self.append_record("event", {"event": event})

    def append_record(self, record_type: str, payload: Mapping[str, Any]) -> None:
        """追加一个 JSONL record。"""

        now = _timestamp()
        self.updated_at = now
        record = {
            "schema_version": 1,
            "kind": "llm_trace_record",
            "record_type": record_type,
            "created_at": now,
            **_json_safe(payload),
        }
        with self.trace_jsonl_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False))
            file.write("\n")

    def write_replay(
        self,
        *,
        status: str,
        turns: Sequence[TurnTrace],
        final_observation: Mapping[str, Any] | None = None,
        failure_reason: str | None = None,
        score: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> None:
        """原子更新 replay.json。"""

        now = _timestamp()
        self.updated_at = now
        replay = build_llm_run_replay(
            status=status,
            session_id=self.session_id,
            turns=turns,
            created_at=self.created_at,
            updated_at=now,
            config=self.config,
            agent=self.agent,
            data_source=self.data_source,
            resume_history=self.resume_history,
            final_observation=final_observation,
            failure_reason=failure_reason,
            score=score,
            stats=stats,
        )
        _write_json_atomic(self.replay_path, replay)


def start_llm_run_archive(
    base_dir: str | Path,
    *,
    session_id: str,
    config: Mapping[str, Any],
    agent: Mapping[str, Any],
    data_source: Mapping[str, Any] | None = None,
) -> LlmRunArchiveWriter:
    """创建一次 LLM run 的增量留档 writer。"""

    return LlmRunArchiveWriter(
        base_dir,
        session_id=session_id,
        config=config,
        agent=agent,
        data_source=data_source,
    )


def resume_llm_run_archive(
    directory: str | Path,
    *,
    session_id: str,
    created_at: str | None,
    config: Mapping[str, Any],
    agent: Mapping[str, Any],
    data_source: Mapping[str, Any] | None = None,
    resume_history: Sequence[Mapping[str, Any]] = (),
) -> LlmRunArchiveWriter:
    """打开已有 LLM run 归档，继续追加 trace 并更新原 replay。"""

    return LlmRunArchiveWriter(
        Path(directory).parent,
        session_id=session_id,
        config=config,
        agent=agent,
        data_source=data_source,
        directory=directory,
        created_at=created_at,
        resume_history=resume_history,
        append_existing=True,
    )


def write_llm_run_archive(
    base_dir: str | Path,
    run: LlmGameRun,
    *,
    config: Mapping[str, Any],
    agent: Mapping[str, Any],
    data_source: Mapping[str, Any] | None = None,
) -> LlmRunArchive:
    """兼容旧调用：写入一份最终 replay 和 trace snapshot。"""

    writer = start_llm_run_archive(
        base_dir,
        session_id=run.session_id,
        config=config,
        agent=agent,
        data_source=data_source,
    )
    writer.append_record("run_snapshot", {"run": run.to_dict()})
    writer.write_replay(
        status=run.status,
        turns=run.turns,
        final_observation=run.final_observation,
        failure_reason=run.failure_reason,
        score=run.score,
    )
    run.archive_dir = str(writer.archive.directory)
    return writer.archive


def build_llm_run_replay(
    *,
    status: str,
    session_id: str,
    turns: Sequence[TurnTrace],
    created_at: str,
    updated_at: str,
    config: Mapping[str, Any],
    agent: Mapping[str, Any],
    data_source: Mapping[str, Any] | None = None,
    resume_history: Sequence[Mapping[str, Any]] = (),
    final_observation: Mapping[str, Any] | None = None,
    failure_reason: str | None = None,
    score: Mapping[str, Any] | None = None,
    stats: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成足以复原 LLM 操作流程的精简 replay。"""

    return {
        "schema_version": 1,
        "kind": "llm_replay",
        "created_at": created_at,
        "updated_at": updated_at,
        "session_id": session_id,
        "status": status,
        "failure_reason": failure_reason,
        "config": dict(config),
        "agent": dict(agent),
        "data": dict(data_source or {}),
        "resume_history": [dict(item) for item in resume_history],
        "turns": [_turn_replay(turn) for turn in turns],
        "final_observation": (
            None
            if final_observation is None
            else dict(final_observation)
        ),
        "score": None if score is None else dict(score),
        "stats": None if stats is None else dict(stats),
    }


def _turn_replay(turn: TurnTrace) -> dict[str, Any]:
    data: dict[str, Any] = {
        "turn": turn.turn,
        "status": turn.status,
        "failure_reason": turn.failure_reason,
        "prompt": turn.prompt,
        "steps": _message_steps(
            turn.messages,
            turn.tool_calls,
            turn.model_responses,
        ),
    }
    timing_usage = _aggregate_timing_usage(turn.model_responses)
    if timing_usage:
        data["timing_usage"] = timing_usage
    if turn.observation_before is not None:
        data["observation_before"] = turn.observation_before
    if turn.rank_score is not None:
        data["rank_score"] = turn.rank_score
    return data


def _aggregate_timing_usage(
    model_responses: Sequence[Any],
) -> dict[str, Any] | None:
    """从 model_responses 聚合回合级 timing 和 usage 汇总。"""
    total_ms: float = 0.0
    total_input: int = 0
    total_output: int = 0
    has_data = False
    for record in model_responses:
        to_dict = getattr(record, "to_dict", None)
        data = to_dict() if callable(to_dict) else record
        if not isinstance(data, Mapping):
            continue
        timing = data.get("timing")
        if isinstance(timing, Mapping):
            ms = timing.get("duration_ms")
            if isinstance(ms, (int, float)):
                total_ms += ms
                has_data = True
        usage = data.get("usage")
        if isinstance(usage, Mapping):
            inp = usage.get("input_tokens", usage.get("prompt_tokens"))
            out = usage.get("output_tokens", usage.get("completion_tokens"))
            if isinstance(inp, (int, float)):
                total_input += int(inp)
                has_data = True
            if isinstance(out, (int, float)):
                total_output += int(out)
                has_data = True
    if not has_data:
        return None
    return {
        "duration_ms": round(total_ms),
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


def _message_steps(
    messages: list[dict[str, Any]],
    tool_calls: Sequence[Any] = (),
    model_responses: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    calls_by_id: dict[str, dict[str, Any]] = {}
    tool_results_by_id: dict[str, dict[str, Any]] = {}
    tool_results_queue: list[dict[str, Any]] = []
    used_tool_result_ids: set[str] = set()
    response_records = _record_dicts(model_responses)
    response_index = 0
    for record in tool_calls:
        to_dict = getattr(record, "to_dict", None)
        data = to_dict() if callable(to_dict) else record
        if not isinstance(data, Mapping):
            continue
        item = dict(data)
        tool_results_queue.append(item)
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            tool_results_by_id[call_id] = item
    seen_turn_prompt = False
    for message in messages:
        role = message.get("role")
        if role == "system":
            steps.append(
                {
                    "type": "system_prompt",
                    "content": message.get("content", ""),
                }
            )
            continue
        if role == "user" and not seen_turn_prompt:
            seen_turn_prompt = True
            steps.append(
                {
                    "type": "turn_prompt",
                    "content": message.get("content", ""),
                }
            )
            continue
        if role == "user":
            steps.append(
                {
                    "type": "retry_prompt",
                    "content": message.get("content", ""),
                }
            )
            continue
        if role == "assistant":
            tool_calls = [
                dict(call)
                for call in message.get("tool_calls", [])
                if isinstance(call, Mapping)
            ]
            for call in tool_calls:
                call_id = call.get("id")
                if isinstance(call_id, str):
                    calls_by_id[call_id] = call
            step = {
                "type": "assistant",
                "content": message.get("content", ""),
                "tool_calls": tool_calls,
            }
            reasoning_content = message.get("reasoning_content")
            if isinstance(reasoning_content, str):
                step["reasoning_content"] = reasoning_content
            if response_index < len(response_records):
                response_record = response_records[response_index]
                timing = response_record.get("timing")
                if isinstance(timing, Mapping) and timing:
                    step["timing"] = dict(timing)
                usage = response_record.get("usage")
                if isinstance(usage, Mapping) and usage:
                    step["usage"] = dict(usage)
            response_index += 1
            steps.append(step)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            call = calls_by_id.get(call_id, {}) if isinstance(call_id, str) else {}
            record = (
                tool_results_by_id.get(call_id)
                if isinstance(call_id, str)
                else None
            )
            if isinstance(call_id, str):
                used_tool_result_ids.add(call_id)
            while record is None and tool_results_queue:
                candidate = tool_results_queue.pop(0)
                candidate_id = candidate.get("call_id")
                if isinstance(candidate_id, str) and candidate_id in used_tool_result_ids:
                    continue
                record = candidate
            if record is None:
                record = {}
            content = message.get("content", "")
            arguments = record.get("arguments")
            if not isinstance(arguments, Mapping):
                arguments = call.get("arguments", {})
            step = {
                "type": "tool_result",
                "call_id": call_id,
                "name": message.get("name") or call.get("name") or record.get("name"),
                "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
                "content": content,
            }
            # Attach intermediate observation snapshot from write tool results
            result_data = record.get("result")
            if isinstance(result_data, Mapping):
                obs_after = result_data.get("_observation_after")
                if isinstance(obs_after, Mapping):
                    step["observation_after"] = dict(obs_after)
                # Preserve structured result for reliable stat reconstruction
                # after resume.  Only keep essential fields to avoid bloat.
                compact = _compact_tool_result(result_data)
                if compact:
                    step["result"] = compact
            legacy_result = record.get("result")
            if isinstance(legacy_result, Mapping) and not isinstance(content, str) and "result" not in step:
                step["result"] = dict(legacy_result)
            steps.append(step)
    return steps


def _record_dicts(records: Sequence[Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for record in records:
        to_dict = getattr(record, "to_dict", None)
        data = to_dict() if callable(to_dict) else record
        if isinstance(data, Mapping):
            values.append(dict(data))
    return values


def _compact_tool_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract essential fields from a tool result for replay persistence.

    Preserves ``ok`` status and ``turn_result`` (for ``end_turn`` calls) so
    that ``_compute_run_stats`` can reconstruct accurate stats after a resume.
    Returns ``None`` if nothing worth preserving is found.
    """
    compact: dict[str, Any] = {}
    ok = result.get("ok")
    if isinstance(ok, bool):
        compact["ok"] = ok
    error = result.get("error")
    if isinstance(error, str) and error:
        compact["error"] = error
    turn_result = result.get("turn_result")
    if isinstance(turn_result, Mapping):
        compact["turn_result"] = dict(turn_result)
    return compact if compact else None


def _json_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    # 使用唯一临时文件名，避免并发写入冲突
    import uuid

    temp_path = path.with_suffix(f"{path.suffix}.tmp.{uuid.uuid4().hex[:8]}")
    temp_path.write_text(
        json.dumps(_json_safe(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _replace_file(temp_path, path)


def _replace_file(src: Path, dst: Path, retries: int = 5, delay: float = 0.3) -> None:
    """替换文件，在 Windows 上自动重试以应对文件被短暂占用的情况。"""
    for attempt in range(retries):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                # 最后一次尝试：先删除目标文件再重命名
                try:
                    dst.unlink(missing_ok=True)
                    src.replace(dst)
                except PermissionError:
                    raise


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _run_directory_name(created_at: str, session_id: str) -> str:
    return f"{created_at}_{_safe_path_part(session_id)}"


def _safe_path_part(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    cleaned = "".join(char if char in allowed else "_" for char in value)
    return cleaned[:80] or "session"
