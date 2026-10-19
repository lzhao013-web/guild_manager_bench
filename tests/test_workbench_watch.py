"""观战只读索引的公开行为，不执行模型请求或评分。"""
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_workbench import manager

from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench_watch import WatchJournal


def append(path, *events):
    with path.open("ab") as stream:
        for event in events:
            stream.write((json.dumps({"at": "2026-10-18T10:00:00Z", "event": event}, ensure_ascii=False) + "\n").encode())


def test_history_survives_more_than_600_deltas_and_loads_one_attempt(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "turn_started", "turn": 1, "observation": {"gold": 10}},
           {"type": "model_request", "turn": 1, "step": 1, "request": {"messages": ["large input"]}})
    append(path, *({"type": "model_delta", "text": "字"} for _ in range(800)))
    append(path, {"type": "model_response", "text": "字" * 800, "raw": {"large": "payload"}},
           {"type": "tool_result", "name": "recruit_adventurer", "result": {"ok": True, "_observation_after": {"gold": 2}}},
           {"type": "turn_scored", "turn": 1, "rank_score": 0},
           {"type": "turn_started", "turn": 2, "observation": {"gold": 12}})
    journal = WatchJournal(path)
    latest = journal.read()
    assert len(latest["records"]) == 1
    first = journal.read(latest["attempts"][0]["id"])
    assert first["attempts"][0]["score"] == 0
    assert len(first["records"]) == 6
    delta = next(r for r in first["records"] if r["event"]["type"] == "model_delta")
    assert delta["event"]["text"] == "字" * 800
    assert "large input" not in json.dumps(first)
    assert "payload" not in json.dumps(first)
    assert first["records"][4]["event"]["result"]["_observation_after"]["gold"] == 2
    # 原始请求仍可通过既有事件接口的字节游标按需读取。
    request = first["records"][1]
    with path.open("rb") as stream:
        stream.seek(request["after"])
        assert json.loads(stream.readline())["event"]["request"]["messages"] == ["large input"]


def test_partial_line_restart_and_retry_are_separate_attempts(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "turn_started", "turn": 3, "observation": {"gold": 10}},
           {"type": "turn_failed", "turn": 3}, {"type": "turn_retry", "turn": 3})
    journal = WatchJournal(path)
    first = journal.read()
    assert first["attempts"][0]["status"] == "rolled_back"
    line = json.dumps({"at": "2026-10-18T10:00:01Z", "event": {"type": "turn_started", "turn": 3, "observation": {"gold": 10}}}).encode()
    with path.open("ab") as stream:
        stream.write(line[:15])
    assert journal.read()["cursor"] == first["cursor"]
    with path.open("ab") as stream:
        stream.write(line[15:] + b"\n")
    latest = journal.read()
    assert [(a["turn"], a["attempt"]) for a in latest["attempts"]] == [(3, 1), (3, 2)]
    assert latest["selected"] != first["selected"]
    assert WatchJournal(path).read() == latest
    assert journal.read(first["selected"])["records"] == first["records"]


def test_watch_api_checks_job_and_attempt_without_starting_worker(tmp_path):
    m = manager(tmp_path)
    job_id = "readonly"
    m.jobs[job_id] = {"id": job_id}
    directory = m.root / job_id
    directory.mkdir(parents=True)
    path = directory / "events.jsonl"
    append(path, {"type": "run_resumed", "restored_observation": {"gold": 4}, "last_rank_score": 9},
           {"type": "turn_started", "turn": 5, "observation": {"gold": 4}})
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        assert client.get("/api/workbench/jobs/missing/watch").status_code == 404
        assert client.get(f"/api/workbench/jobs/{job_id}/watch?attempt=-1").status_code == 422
        assert client.get(f"/api/workbench/jobs/{job_id}/watch?attempt=1").status_code == 400
        response = client.get(f"/api/workbench/jobs/{job_id}/watch").json()
        initial = response["attempts"][0]["id"]
        restored = client.get(f"/api/workbench/jobs/{job_id}/watch?attempt={initial}").json()
        assert restored["records"][0]["event"]["restored_observation"]["gold"] == 4
        assert restored["attempts"][0]["score"] == 9
        assert not m._processes


def test_multiple_viewers_share_an_index_without_repeating_attempts_or_writing_log(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, *({"type": "turn_started", "turn": turn, "observation": {"gold": turn}} for turn in range(1, 81)))
    original = path.read_bytes()
    journal = WatchJournal(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(lambda _: journal.read(), range(24)))
    assert all(page == pages[0] for page in pages)
    assert len(pages[0]["attempts"]) == 80
    assert path.read_bytes() == original


def test_initialization_failure_is_visible_without_a_game_snapshot(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "worker_failed", "error": "构造初始化故障"})
    result = WatchJournal(path).read()
    assert result["attempts"][0]["status"] == "failed"
    assert result["records"][0]["event"]["error"] == "构造初始化故障"


def test_queued_job_has_empty_history(tmp_path):
    assert WatchJournal(tmp_path / "not-created.jsonl").read() == {
        "cursor": 0, "attempts": [], "selected": None, "records": [],
    }
