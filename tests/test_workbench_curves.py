from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api import archive_io
from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench_curves import CurveStore
from test_workbench import manager


def completed_turn(number, *, score=100, gold=50, xp=60, rewards=((10, 20),)):
    return {"turn": number, "status": "completed", "rank_score": score,
            "observation_before": {"gold": 100, "experience_pool": 0},
            "steps": [{"type": "tool_result", "name": "end_turn", "result": {
                "ok": True, "turn_result": {"battles": [{"reward": {"gold": g, "experience": e}} for g, e in rewards]}},
                "observation_after": {"gold": gold, "experience_pool": xp, "turn": number + 1}}]}


def replay(*turns):
    return {"kind": "llm_replay", "status": "running", "turns": list(turns), "final_observation": {"max_turns": 35}}


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_slow_projection_does_not_hold_the_replay_handle_and_next_read_sees_replacement(tmp_path, monkeypatch):
    path = tmp_path / "run" / "replay.json"
    temporary = path.with_suffix(".next")
    save(path, replay(completed_turn(1)))
    save(temporary, replay(completed_turn(1), completed_turn(2)))
    parsing, release = Event(), Event()
    original = archive_io.msgspec.json.decode

    def parse(text, *args, **kwargs):
        parsing.set()
        assert release.wait(5), "测试必须释放解析线程"
        return original(text, *args, **kwargs)

    monkeypatch.setattr(archive_io.msgspec.json, "decode", parse)
    store = CurveStore()
    results = []
    reader = Thread(target=lambda: results.append(store.read(path)))
    reader.start()
    try:
        assert parsing.wait(5)
        # Windows 下，若仍持有普通读取句柄，这里的原子替换会抛 PermissionError。
        temporary.replace(path)
    finally:
        release.set()
        reader.join(timeout=5)
    assert not reader.is_alive()
    assert len(results[0]["points"]) == 1
    assert len(store.read(path)["points"]) == 2


def test_curve_api_uses_completed_history_and_returns_no_credentials_or_full_observations(tmp_path):
    m = manager(tmp_path)
    path = m.archive_root / "sample" / "replay.json"
    payload = replay(completed_turn(1, score=0, gold=50, xp=8),
                     {"turn": 2, "status": "failed", "rank_score": 99999, "steps": []},
                     completed_turn(2, score=200, gold=2, xp=3, rewards=((40, 80),)))
    payload.update(agent={"config": {"api_key": "never-return-this", "model": "fixture"}},
                   data={"preset": "full", "protocol_hash": "protocol", "settings_hash": "settings", "data_hash": "data"})
    save(path, payload)
    original = path.read_bytes()
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        response = client.get("/api/workbench/curves", params={"run_id": "sample"})
        assert response.status_code == 200
        data = response.json()
        assert data["points"][0] == {"turn": 1, "rank_score": 0, "gold": 50, "experience_pool": 8, "gold_income": 10, "experience_income": 20}
        assert data["points"][1]["gold_income"] == 50
        assert len(data["points"]) == 2
        assert data["conditions"]["protocol_hash"] == "protocol"
        assert "never-return-this" not in response.text
        assert "adventurers" not in response.text
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_job_and_archive_point_to_the_same_authoritative_resumed_history(tmp_path):
    m = manager(tmp_path)
    m.jobs["old"] = {"id": "old", "archive_id": "same", "status": "interrupted", "config": {}}
    m.jobs["new"] = {"id": "new", "archive_id": "same", "status": "running", "config": {}}
    path = m.archive_root / "same" / "replay.json"
    save(path, replay(completed_turn(1)))
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        first = client.get("/api/workbench/curves", params={"job_id": "old"}).json()
        save(path, replay(completed_turn(1), completed_turn(2)))
        second = client.get("/api/workbench/curves", params={"job_id": "new"}).json()
        archive = client.get("/api/workbench/curves", params={"run_id": "same"}).json()
        assert len(first["points"]) == 1
        assert len(second["points"]) == 2
        assert second["points"] == archive["points"]
        assert second["points"][-1]["gold_income"] == 20
        assert second["status"] == "running"
        m.jobs["new"]["checkpoint_error"] = "fixture disk error"
        warned = client.get("/api/workbench/curves", params={"job_id": "new"}).json()
        assert "存档写入失败" in warned["warning"]


def test_unstarted_job_and_invalid_paths_are_explicit_not_zero_filled(tmp_path):
    m = manager(tmp_path)
    m.jobs["waiting"] = {"id": "waiting", "archive_id": None, "status": "queued", "config": {}}
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        empty = client.get("/api/workbench/curves", params={"job_id": "waiting"})
        assert empty.status_code == 200 and empty.json()["points"] == []
        assert empty.json()["warning"]
        assert client.get("/api/workbench/curves").status_code == 422
        assert client.get("/api/workbench/curves", params={"job_id": "waiting", "run_id": "other"}).status_code == 422
        assert client.get("/api/workbench/curves", params={"job_id": "missing"}).status_code == 404
        for value in ("..", "../outside", "a/b", "a\\b"):
            assert client.get("/api/workbench/curves", params={"run_id": value}).status_code == 400
        assert client.get("/api/workbench/curves", params={"run_id": "missing"}).status_code == 404


def test_cache_reuses_projection_and_labels_stale_data_without_rewriting(tmp_path, monkeypatch):
    path = tmp_path / "sample" / "replay.json"
    save(path, replay(completed_turn(1)))
    reads = []
    original_read = Path.read_bytes

    def read(target, *args, **kwargs):
        if target == path:
            reads.append(target)
        return original_read(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read)
    store = CurveStore()
    first = deepcopy(store.read(path))
    assert store.read(path) == first
    assert len(reads) == 1
    path.write_text('{"partial":', encoding="utf-8")
    broken = path.read_bytes()
    stale = store.read(path)
    assert stale["points"] == first["points"] and stale["warning"]
    assert path.read_bytes() == broken
    save(path, replay(completed_turn(1), completed_turn(2)))
    assert len(store.read(path)["points"]) == 2
    assert store.read(path)["warning"] is None


def test_missing_rewards_and_gaps_never_become_zero_or_counterfactual_income(tmp_path):
    path = tmp_path / "legacy" / "replay.json"
    first = completed_turn(1, score=0)
    del first["steps"][0]["observation_after"]
    second = completed_turn(2, score=None)
    second["observation_before"] = {"gold": 50, "experience_pool": 60}
    del second["steps"][0]["result"]["turn_result"]
    save(path, replay(first, second, completed_turn(4)))
    result = CurveStore().read(path)
    assert result["points"][0]["gold"] == 50
    assert result["points"][0]["rank_score"] == 0
    assert result["points"][1]["rank_score"] is None
    assert result["points"][1]["gold_income"] is None
    assert result["points"][2]["gold_income"] is None
    assert [point["turn"] for point in result["points"]] == [1, 2, 4]


@pytest.mark.parametrize("payload", [[], {"kind": "other"}, {"kind": "llm_replay", "turns": []}, {"kind": "llm_replay", "turns": [], "final_observation": {"max_turns": False}}])
def test_unsupported_archive_is_reported_without_repair_or_rescoring(tmp_path, payload):
    m = manager(tmp_path)
    path = m.archive_root / "invalid" / "replay.json"
    save(path, payload)
    before = path.read_bytes()
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        assert client.get("/api/workbench/curves", params={"run_id": "invalid"}).status_code == 422
    assert path.read_bytes() == before
