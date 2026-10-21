import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from guild_manager_bench.api.workbench_lock import FileLease
from guild_manager_bench.bench.llm.recovery import RecoveryConfirmationRequired
from test_workbench import manager, model_server, wait_for
from test_run_recovery import source_archive, serial_scoring


def test_http_check_confirmation_info_and_queue_are_separate(tmp_path):
    m = manager(tmp_path)
    m.root.mkdir(parents=True)
    source = source_archive(m.archive_root, turns=1)
    path = source / "replay.json"
    replay = json.loads(path.read_text(encoding="utf-8"))
    replay["data"]["protocol_hash"] = "old"
    replay["agent"]["config"] = {"api_key": "must-not-appear", "model": "source-model"}
    path.write_text(json.dumps(replay), encoding="utf-8")
    original = path.read_bytes()
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        info = client.get(f"/api/workbench/recoveries/{source.name}")
        assert info.status_code == 200 and info.json()["max_rerun_turn"] == 2
        assert "must-not-appear" not in info.text
        spec = {"preset": "default", "model": "target-model", "base_url": "http://127.0.0.1:1/v1", "rerun_run_id": source.name, "rerun_from_turn": 1}
        body = {"name": "构造分支", "runs": [spec]}
        check = client.post("/api/workbench/batches/check", json=body)
        assert check.status_code == 200 and not m.jobs
        required = client.post("/api/workbench/batches", json=body)
        assert required.status_code == 409 and not m.jobs
        assert required.json()["detail"]["code"] == "version_confirmation_required"
        spec["version_confirmation"] = check.json()["recoveries"][0]["confirmation_token"]
        with FileLease(source / "workbench.lock"):
            locked = client.post("/api/workbench/batches", json=body)
            assert locked.status_code == 422 and not m.jobs
        queued = client.post("/api/workbench/batches", json=body)
        assert queued.status_code == 201
        job = queued.json()["jobs"][0]
        assert job["config"]["rerun_run_id"] == source.name and job["archive_id"] is None
        m.stop(job["id"])
        assert path.read_bytes() == original


@pytest.mark.parametrize("maximum", [None, True, "35", 0])
def test_corrupt_turn_limit_is_not_a_forceable_version_difference(tmp_path, maximum):
    from guild_manager_bench.bench.llm.recovery import inspect_recovery
    source = source_archive(tmp_path, turns=1)
    path = source / "replay.json"
    replay = json.loads(path.read_text(encoding="utf-8"))
    replay["final_observation"]["max_turns"] = maximum
    path.write_text(json.dumps(replay), encoding="utf-8")
    with pytest.raises(ValueError, match="上限损坏"):
        inspect_recovery(source, manager(tmp_path).presets["default"].data_dir, 1)


def test_queue_rechecks_source_before_any_model_request(tmp_path):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path)
            await m.start()
            try:
                source = source_archive(m.archive_root, turns=1)
                path = source / "replay.json"
                spec = RunSpec(preset="default", model="must-not-call", base_url=url, rerun_run_id=source.name, rerun_from_turn=1)
                job = m.create_batch(CreateBatch(name="排队后源变化", runs=[spec]))["jobs"][0]
                replay = json.loads(path.read_text(encoding="utf-8"))
                replay["updated_at"] = "source-replaced-after-queue"
                path.write_text(json.dumps(replay), encoding="utf-8")
                original = path.read_bytes()
                await wait_for(lambda: job["status"] == "failed", timeout=30)
                assert "排队期间" in job["error"]
                assert calls == [] and path.read_bytes() == original
            finally:
                await m.close()
        asyncio.run(scenario())


@pytest.mark.parametrize("fields", [
    {"rerun_run_id": "../escape", "rerun_from_turn": 1},
    {"rerun_run_id": "parent"},
    {"rerun_from_turn": 1},
    {"rerun_run_id": "parent", "rerun_from_turn": True},
    {"rerun_run_id": "parent", "rerun_from_turn": 1, "resume_run_id": "parent"},
    {"version_confirmation": "a" * 64},
])
def test_invalid_recovery_request_cannot_enter_queue(fields):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunSpec(preset="default", **fields)
