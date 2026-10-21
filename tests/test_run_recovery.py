"""真实游戏恢复与本地假模型验证，不访问付费接口。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm import LlmAgentResponse, LlmRunConfig, LlmToolCall, run_llm_game
from guild_manager_bench.bench.llm import runner
from guild_manager_bench.bench import metrics, protocols
from guild_manager_bench.bench.llm.recovery import RecoveryConfirmationRequired, inherited_config, inspect_recovery
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from guild_manager_bench.api.workbench_trajectories import TrajectoryStore
from guild_manager_bench.api.workbench_curves import CurveStore
from test_workbench import manager, model_server, wait_for

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def serial_scoring(monkeypatch):
    monkeypatch.setattr(runner, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(metrics, "ProcessPoolExecutor", ThreadPoolExecutor)
    frozen = protocols.full_runtime()
    monkeypatch.setattr(frozen, "ProcessPoolExecutor", ThreadPoolExecutor)
    from guild_manager_bench._frozen.full_v1.bench import metrics as frozen_metrics
    monkeypatch.setattr(frozen_metrics, "ProcessPoolExecutor", ThreadPoolExecutor)


class EndAgent:
    def __init__(self, stop_after=None):
        self.calls = 0
        self.stop_after = stop_after
        self.prompts = []

    def respond(self, *, messages, tools):
        if self.stop_after is not None and self.calls >= self.stop_after:
            raise RuntimeError("fixture stop")
        self.calls += 1
        self.prompts.append(json.dumps(messages, ensure_ascii=False))
        calls = []
        if self.stop_after is not None:
            calls.append(LlmToolCall("write_memo", {"content": f"memo-turn-{self.calls}"}))
        calls.append(LlmToolCall("end_turn", {"hunts": []}))
        return LlmAgentResponse(tool_calls=tuple(calls))


def source_archive(root, preset="default", turns=3):
    with pytest.raises(RuntimeError, match="fixture stop"):
        run_llm_game(EndAgent(turns), data_dir=ROOT / "data/presets" / preset, config=LlmRunConfig(archive_dir=root))
    return next(root.iterdir())


def continue_plan(plan, root, agent):
    config = LlmRunConfig(**inherited_config(plan.replay), archive_dir=root)
    return run_llm_game(agent, data_dir=ROOT / "data/presets" / plan.current["preset"], config=config,
                        resume_archive_dir=plan.directory, recovery=plan, data_source=plan.current)


def test_rerun_creates_branch_keeps_prefix_memo_and_does_not_touch_original(tmp_path):
    source = source_archive(tmp_path)
    original = {p: p.read_bytes() for p in source.iterdir()}
    plan = inspect_recovery(source, ROOT / "data/presets/default", 2)
    assert plan.differences == []
    plan.authorize(None)
    agent = EndAgent()
    result = continue_plan(plan, tmp_path, agent)
    assert result.status == "completed"
    assert agent.calls == 7
    assert "memo-turn-1" in agent.prompts[0]
    assert "memo-turn-2" not in agent.prompts[0] and "memo-turn-3" not in agent.prompts[0]
    assert result.archive_dir != str(source)
    child = json.loads((Path(result.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    assert [t["turn"] for t in child["turns"] if t["status"] == "completed"] == list(range(1, 9))
    assert child["session_id"] != plan.replay["session_id"]
    assert child["resume_history"][-1]["from_turn"] == 2
    assert child["resume_history"][-1]["source_run_id"] == source.name
    assert all(path.read_bytes() == raw for path, raw in original.items())
    history = TrajectoryStore().read(Path(result.archive_dir) / "trace.jsonl", archived=True)
    rows = history["entries"]
    assert any(row["turn"] == 1 and row["lifecycle"] == "confirmed" for row in rows)
    assert any(row["turn"] == 2 and row["lifecycle"] == "rolled_back" for row in rows)
    assert any(row["turn"] == 2 and row["lifecycle"] == "confirmed" for row in rows)


def test_completed_archive_can_rerun_from_one_but_cannot_resume(tmp_path):
    parent = run_llm_game(EndAgent(), data_dir=ROOT / "data/presets/default", config=LlmRunConfig(archive_dir=tmp_path))
    directory = Path(parent.archive_dir)
    original = (directory / "replay.json").read_bytes()
    with pytest.raises(ValueError, match="已完成"):
        inspect_recovery(directory, ROOT / "data/presets/default")
    plan = inspect_recovery(directory, ROOT / "data/presets/default", 1)
    agent = EndAgent()
    child = continue_plan(plan, tmp_path, agent)
    assert child.status == "completed" and agent.calls == 8
    assert child.turns[0].turn == 1 and len(child.turns) == 8
    assert (directory / "replay.json").read_bytes() == original


def test_force_requires_exact_confirmation_and_records_both_versions(tmp_path):
    source = source_archive(tmp_path, turns=1)
    path = source / "replay.json"
    replay = json.loads(path.read_text(encoding="utf-8"))
    replay["data"].update(protocol_hash="old-protocol", data_hash="0" * 64)
    path.write_text(json.dumps(replay), encoding="utf-8")
    plan = inspect_recovery(source, ROOT / "data/presets/default")
    with pytest.raises(RecoveryConfirmationRequired):
        plan.authorize(None)
    agent = EndAgent()
    with pytest.raises(RecoveryConfirmationRequired):
        continue_plan(plan, tmp_path, agent)
    assert agent.calls == 0
    with pytest.raises(RecoveryConfirmationRequired):
        plan.authorize("f" * 64)
    plan.authorize(plan.token)
    result = continue_plan(plan, tmp_path, EndAgent())
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert result.archive_dir == str(source)
    assert updated["data"]["protocol_status"] == "mixed"
    assert updated["resume_history"][-1]["archived_data"]["protocol_hash"] == "old-protocol"
    assert updated["resume_history"][-1]["forced_version"] is True
    store = CurveStore()
    assert "跨版本" in store.read(path)["warning"]
    assert "跨版本" in store.read(path)["warning"]


def test_force_full_uses_frozen_engine_without_mutating_frozen_functions(tmp_path):
    source = source_archive(tmp_path, "full", 1)
    path = source / "replay.json"
    replay = json.loads(path.read_text(encoding="utf-8"))
    replay["data"].update(protocol_hash="legacy-full", data_hash="1" * 64)
    path.write_text(json.dumps(replay), encoding="utf-8")
    frozen = protocols.full_runtime()
    restore, writer = frozen._restore_from_replay_archive, frozen._resume_archive
    original = path.read_bytes()
    plan = inspect_recovery(source, ROOT / "data/presets/full", 2)
    plan.authorize(plan.token)
    result = continue_plan(plan, tmp_path, EndAgent())
    assert result.status == "completed"
    assert path.read_bytes() == original
    assert frozen._restore_from_replay_archive is restore and frozen._resume_archive is writer
    protocols.verify_full_release()
    child = json.loads((Path(result.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    assert child["data"]["protocol_hash"] == protocols.FULL_PROTOCOL_HASH
    assert child["data"]["protocol_status"] == "mixed"


def test_illegal_history_cannot_be_forced_and_never_calls_model(tmp_path):
    source = source_archive(tmp_path, turns=1)
    path = source / "replay.json"
    replay = json.loads(path.read_text(encoding="utf-8"))
    replay["turns"][0]["steps"].insert(0, {"type": "tool_result", "name": "purchase_upgrade", "arguments": {"upgrade_id": "nonexistent"}, "result": {"ok": True}})
    path.write_text(json.dumps(replay), encoding="utf-8")
    original = path.read_bytes()
    plan = inspect_recovery(source, ROOT / "data/presets/default", 2)
    plan.authorize(plan.token)
    agent = EndAgent()
    with pytest.raises(ValueError, match="failed to replay"):
        continue_plan(plan, tmp_path, agent)
    assert agent.calls == 0 and path.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("turn", [0, 5, 99])
def test_rerun_range_is_bounded_by_recoverable_prefix(tmp_path, turn):
    source = source_archive(tmp_path)
    with pytest.raises(ValueError, match="起始回合"):
        inspect_recovery(source, ROOT / "data/presets/default", turn)


def test_workbench_version_check_never_enqueues_until_confirmation(tmp_path):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        try:
            source = source_archive(m.archive_root, turns=1)
            path = source / "replay.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["data"]["protocol_hash"] = "old"
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = path.read_bytes()
            spec = RunSpec(preset="default", model="test", base_url="http://127.0.0.1:1/v1", rerun_run_id=source.name, rerun_from_turn=1)
            request = CreateBatch(name="分支确认", runs=[spec])
            check = m.check_recoveries(request)["recoveries"][0]
            assert check["differences"] and m.jobs == {}
            with pytest.raises(RecoveryConfirmationRequired):
                m.create_batch(request)
            assert m.jobs == {} and path.read_bytes() == original
            job = m.create_batch(CreateBatch(name="确认分支", runs=[spec.model_copy(update={"version_confirmation": check["confirmation_token"]})]))["jobs"][0]
            assert job["archive_id"] is None and job["config"]["resume_run_id"] is None
            assert job["config"]["rerun_run_id"] == source.name
            m.stop(job["id"])
            assert path.read_bytes() == original
        finally:
            await m.close()
    asyncio.run(scenario())


def test_real_worker_reruns_selected_turn_without_overwriting_original(tmp_path):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path)
            await m.start()
            try:
                source = source_archive(m.archive_root)
                original = (source / "replay.json").read_bytes()
                spec = RunSpec(preset="default", model="fast-rerun", base_url=url, rerun_run_id=source.name, rerun_from_turn=3)
                job = m.create_batch(CreateBatch(name="真实隔离分支", runs=[spec]))["jobs"][0]
                await wait_for(lambda: job["status"] in {"completed", "failed"}, timeout=50)
                assert job["status"] == "completed", job["error"]
                assert len(calls) == 6
                assert job["calls"] == 8
                assert job["archive_id"] != source.name
                assert (source / "replay.json").read_bytes() == original
                assert [p["turn"] for p in job["scores"]] == list(range(1, 9))
            finally:
                await m.close()
        asyncio.run(scenario())
