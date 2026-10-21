"""显式恢复计划：分支重跑、版本确认及来源记录，不修改冻结运行包。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import FunctionType
from typing import Any
from uuid import uuid4

from guild_manager_bench.bench.protocols import CONFIG_FIELDS

INHERITED_FIELDS = (*CONFIG_FIELDS, "game_seed", "scoring_seed")
VERSION_FIELDS = ("preset", "benchmark_version", "protocol_hash", "settings_hash", "data_hash", "game_seed", "scoring_seed")
MIXED_WARNING = "跨版本执行：历史记录与当前规则不完全一致，不应作为同版本标准评测比较。"


class RecoveryConfirmationRequired(ValueError):
    def __init__(self, items: list[dict[str, Any]]):
        super().__init__("存档与当前版本不一致，请确认风险后执行")
        self.detail = {"code": "version_confirmation_required", "message": str(self), "recoveries": items}


def read_recovery_archive(directory: Path) -> tuple[dict[str, Any], str]:
    raw = (directory / "replay.json").read_bytes()
    replay = json.loads(raw)
    if (not isinstance(replay, dict) or replay.get("kind") != "llm_replay"
            or not isinstance(replay.get("session_id"), str) or not replay["session_id"]
            or not isinstance(replay.get("turns"), list)):
        raise ValueError("存档不是可恢复的 LLM 回放")
    for key in ("data", "config", "agent", "final_observation"):
        if key in replay and not isinstance(replay[key], dict):
            raise ValueError(f"存档 {key} 记录损坏，不能恢复")
    maximum = replay.get("final_observation", {}).get("max_turns")
    if "max_turns" in replay.get("final_observation", {}) and (type(maximum) is not int or maximum < 1):
        raise ValueError("存档回合上限损坏，不能恢复")
    for turn in replay["turns"]:
        if (not isinstance(turn, dict) or type(turn.get("turn")) is not int or turn["turn"] < 1
                or not isinstance(turn.get("steps"), list)):
            raise ValueError("存档回合记录损坏，不能恢复")
    return replay, hashlib.sha256(raw).hexdigest()


def inherited_config(replay: dict[str, Any]) -> dict[str, Any]:
    values = {key: replay.get("config", {})[key] for key in INHERITED_FIELDS if key in replay.get("config", {})}
    for key in ("game_seed", "scoring_seed"):
        if type(replay.get("data", {}).get(key)) is int:
            values[key] = replay["data"][key]
    return values


def runtime_context(data_dir: str | Path, config):
    from guild_manager_bench.bench.llm import runner
    from guild_manager_bench.bench.protocols import full_context, protocol_metadata
    from guild_manager_bench.game.presets import describe_data_source
    if describe_data_source(data_dir).get("preset") == "full":
        return full_context(data_dir, config)
    definition = runner._definition_for_config(data_dir, config)
    source = runner._data_source_with_effective_seeds(describe_data_source(data_dir), definition)
    source.update(protocol_metadata(data_dir, definition, config))
    return runner, Path(data_dir), config, source


def recoverable_turns(replay: dict[str, Any]) -> int:
    completed = [turn["turn"] for turn in replay["turns"] if turn.get("status") == "completed"]
    next_turn = 1
    for number in completed:
        if number != next_turn:
            break
        next_turn += 1
    return next_turn


@dataclass
class RecoveryPlan:
    directory: Path
    replay: dict[str, Any]
    from_turn: int | None
    current: dict[str, Any]
    token: str
    differences: list[dict[str, Any]]
    max_rerun_turn: int
    confirmed: bool = False

    @property
    def mixed(self) -> bool:
        return bool(self.differences) or self.replay.get("data", {}).get("protocol_status") == "mixed"

    def public(self, index: int = 0) -> dict[str, Any]:
        return {"index": index, "run_id": self.directory.name, "from_turn": self.from_turn,
                "max_rerun_turn": self.max_rerun_turn, "differences": self.differences,
                "confirmation_token": self.token}

    def authorize(self, confirmation: str | None) -> None:
        if self.differences and confirmation != self.token:
            raise RecoveryConfirmationRequired([self.public()])
        if confirmation is not None and confirmation != self.token:
            raise ValueError("存档已变化，请重新检查后提交")
        self.confirmed = True

    def validate(self, current: dict[str, Any]) -> None:
        if self.differences and not self.confirmed:
            raise RecoveryConfirmationRequired([self.public()])
        if any(current.get(key) != self.current.get(key) for key in VERSION_FIELDS):
            raise ValueError("排队期间版本已变化，请重新提交并确认，尚未请求模型")
        if self.mixed:
            current["protocol_status"] = "mixed"

    def prefix(self) -> dict[str, Any]:
        if self.from_turn is None:
            return self.replay
        prefix = {**self.replay, "session_id": uuid4().hex,
                  "turns": [turn for turn in self.replay["turns"] if turn["turn"] < self.from_turn]}
        for key in ("score", "stats", "failure_reason", "final_observation"):
            prefix.pop(key, None)
        return prefix

    def restore(self, tools, runtime, current: dict[str, Any]) -> dict[str, Any]:
        self.validate(current)
        replay = self.prefix()
        session_id = replay["session_id"]
        tools.start_session(session_id)
        for turn in replay["turns"]:
            if turn.get("status") != "completed":
                continue
            if tools.get_observation(session_id)["observation"]["turn"] != turn["turn"]:
                raise ValueError(f"无法恢复第 {turn['turn']} 回合：历史回合不连续或新规则已提前结束游戏，尚未请求模型")
            harness = runtime.TurnToolHarness(tools, session_id, max_tool_calls=1_000_000,
                                             endgame_start_turn=tools.definition.llm_tools.endgame_start_turn)
            for step in turn["steps"]:
                runtime._replay_confirmed_tool_result(harness, step)
        observation = tools.get_observation(session_id)["observation"]
        if self.from_turn is not None and (observation["turn"] != self.from_turn or observation["finished"]):
            raise ValueError(f"历史操作无法恢复到第 {self.from_turn} 回合开局，尚未请求模型")
        return {"session_id": session_id, "replay": replay, "traces": runtime._traces_from_replay(replay),
                "memo_entries": runtime.memo_entries_from_tool_steps(replay["turns"])}

    def writer(self, runtime, config, agent, archive_dir, replay, current):
        if self.from_turn is None:
            writer = runtime._resume_archive(config, agent, archive_dir, replay, current)
        else:
            writer = runtime._start_archive(config, agent, replay["session_id"], current)
            if writer is not None and (self.directory / "trace.jsonl").exists():
                # 保留原始证据；随后 run_resumed 会在本分支中标记未继承的后续尝试。
                with (self.directory / "trace.jsonl").open("rb") as source, writer.trace_jsonl_path.open("ab") as target:
                    for line in source:
                        if not line.endswith(b"\n"):
                            break
                        target.write(line)
        if writer is not None:
            provenance = {"operation": "rerun" if self.from_turn is not None else "resume",
                          "source_run_id": self.directory.name, "from_turn": self.from_turn,
                          "archived_data": self.replay.get("data", {}), "current_data": dict(current),
                          "forced_version": bool(self.differences), "confirmation_token": self.token}
            if self.from_turn is None:
                writer.resume_history[-1].update(provenance)
            else:
                writer.resume_history.append(provenance)
                writer.append_record("branch_started", provenance)
        return writer

    def run_frozen(self, runtime, agent, **kwargs):
        # 冻结入口无恢复注入参数。复制函数入口并仅替换两项持久化依赖，
        # 不改冻结文件或模块全局，也不替换游戏、工具、评分和模型调用函数。
        original = runtime.run_llm_game
        namespace = {**original.__globals__,
                     "_restore_from_replay_archive": lambda tools, directory, data: self.restore(tools, runtime, data),
                     "_resume_archive": lambda config, agent, directory, replay, data: self.writer(runtime, config, agent, directory, replay, data)}
        entry = FunctionType(original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__)
        entry.__kwdefaults__ = original.__kwdefaults__
        return entry(agent, **kwargs)


def inspect_recovery(directory: Path, data_dir: str | Path, from_turn: int | None = None) -> RecoveryPlan:
    from guild_manager_bench.bench.llm.runner import LlmRunConfig
    replay, revision = read_recovery_archive(directory)
    runtime, effective_dir, config, current = runtime_context(data_dir, LlmRunConfig(**inherited_config(replay)))
    definition = runtime._definition_for_config(effective_dir, config)
    archived = replay.get("data", {})
    if archived.get("preset") and archived["preset"] != current.get("preset"):
        raise ValueError("恢复必须选择原存档的 preset，版本确认不能跨 preset")
    next_turn = recoverable_turns(replay)
    maximum = min(next_turn, replay.get("final_observation", {}).get("max_turns", definition.rules.max_turns), definition.rules.max_turns)
    if from_turn is None:
        if replay.get("status") == "completed":
            raise ValueError("已完成存档不能续跑，请使用从指定回合重跑")
        if sum(turn.get("status") == "completed" for turn in replay["turns"]) != next_turn - 1:
            raise ValueError("存档已完成回合不连续，不能续跑")
    elif not 1 <= from_turn <= maximum:
        raise ValueError(f"重跑起始回合必须在 1 到 {maximum} 之间")
    differences = [{"field": key, "archived": archived.get(key), "current": current.get(key)} for key in VERSION_FIELDS
                   if archived.get(key) != current.get(key) or key in ("benchmark_version", "protocol_hash", "settings_hash", "data_hash") and not archived.get(key)]
    token = hashlib.sha256(json.dumps({"revision": revision, "current": {key: current.get(key) for key in VERSION_FIELDS},
                                      "run_id": directory.name, "from_turn": from_turn}, sort_keys=True).encode()).hexdigest()
    return RecoveryPlan(directory, replay, from_turn, current, token, differences, maximum)
