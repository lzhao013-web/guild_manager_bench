"""备忘投影只读取成功写入，不把未来或回滚分支带进观看位置。"""
from copy import deepcopy

from test_workbench_watch import append
from guild_manager_bench.api.workbench_watch import WatchJournal


def write_memo(content, turn, *, ok=True):
    return {"type": "tool_result", "turn": turn, "name": "write_memo", "result": {
        "ok": ok, "memo": {"content": content, "updated_turn": turn, "max_chars": 500}}}


def test_memo_baselines_survive_turns_clear_retry_and_incremental_restart(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "run_started", "observation": {"turn": 1}},
           {"type": "turn_started", "turn": 1, "observation": {}},
           write_memo("先升级铁匠铺", 1), {"type": "turn_completed"},
           {"type": "turn_scored", "turn": 1, "rank_score": 0},
           {"type": "turn_started", "turn": 2, "observation": {}})
    journal = WatchJournal(path)
    page = journal.read()
    saved = {"state": "saved", "content": "先升级铁匠铺", "updated_turn": 1}
    assert page["memo_before"] == saved
    first = page["attempts"][1]["id"]
    assert journal.read(first)["memo_before"] == {"state": "empty", "updated_turn": None}
    append(path, write_memo("失败分支的笔记", 2), {"type": "turn_failed"}, {"type": "turn_retry"},
           {"type": "turn_started", "turn": 2, "observation": {}}, write_memo("不能保存", 2, ok=False))
    assert journal.read()["memo_before"] == saved
    append(path, write_memo("", 2), {"type": "turn_completed"},
           {"type": "turn_scored", "turn": 2, "rank_score": 0},
           {"type": "turn_started", "turn": 3, "observation": {}})
    page = journal.read()
    assert page["memo_before"] == {"state": "empty", "updated_turn": 2}
    assert WatchJournal(path).read() == page
    assert journal.read(first)["memo_before"] == {"state": "empty", "updated_turn": None}


def test_checkpoint_rollback_drops_a_completed_but_unsaved_memo_without_rewriting_history(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "run_started", "observation": {"turn": 1}},
           {"type": "turn_started", "turn": 1, "observation": {}}, write_memo("保留 T1", 1),
           {"type": "turn_scored", "turn": 1, "rank_score": 0},
           {"type": "turn_started", "turn": 2, "observation": {}}, write_memo("撤销 T2", 2),
           {"type": "turn_scored", "turn": 2, "rank_score": 0},
           {"type": "turn_started", "turn": 3, "observation": {}})
    journal = WatchJournal(path)
    historical = deepcopy(journal.read())
    append(path, {"type": "run_resumed", "restored_observation": {"turn": 2}},
           {"type": "turn_started", "turn": 2, "observation": {}})
    latest = journal.read()
    assert latest["memo_before"] == {"state": "saved", "content": "保留 T1", "updated_turn": 1}
    assert journal.read(historical["selected"])["memo_before"] == historical["memo_before"]


def test_resume_uses_the_recorded_source_before_checkpoint_not_its_later_memo(tmp_path):
    source = tmp_path / "archive.jsonl"
    append(source, {"type": "run_started", "observation": {"turn": 1}},
           {"type": "turn_started", "turn": 1, "observation": {}}, write_memo("续跑继承", 1),
           {"type": "turn_scored", "turn": 1, "rank_score": 0},
           {"type": "turn_started", "turn": 2, "observation": {}}, write_memo("不能提前展示", 2),
           {"type": "turn_scored", "turn": 2, "rank_score": 0})
    origin = WatchJournal(source)
    path = tmp_path / "execution.jsonl"
    append(path, {"type": "run_started", "observation": {"turn": 2}},
           {"type": "run_resumed", "restored_observation": {"turn": 2}},
           {"type": "turn_started", "turn": 2, "observation": {}})
    journal = WatchJournal(path, memo_seed=origin.memo_before_turn)
    assert journal.read()["memo_before"] == {"state": "saved", "content": "续跑继承", "updated_turn": 1}
    assert WatchJournal(path).read()["memo_before"] == {"state": "unavailable"}


def test_watch_api_seeds_resume_from_archived_events(tmp_path):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_workbench import manager
    from guild_manager_bench.api.routes_workbench import workbench_router

    m = manager(tmp_path)
    source = m.archive_root / "saved"
    source.mkdir(parents=True)
    (source / "replay.json").write_text("{}", encoding="utf-8")
    events = [{"type": "run_started", "observation": {"turn": 1}},
              {"type": "turn_started", "turn": 1, "observation": {}}, write_memo("源存档备忘", 1),
              {"type": "turn_scored", "turn": 1, "rank_score": 0}]
    (source / "trace.jsonl").write_text("".join(json.dumps({"record_type": "event", "created_at": "2026-10-30", "event": e}) + "\n" for e in events), encoding="utf-8")
    m.jobs["resumed"] = {"id": "resumed", "config": {"preset": "default", "resume_run_id": "saved"}}
    directory = m.root / "resumed"
    directory.mkdir(parents=True)
    append(directory / "events.jsonl", {"type": "run_started", "observation": {"turn": 2}},
           {"type": "run_resumed", "restored_observation": {"turn": 2}},
           {"type": "turn_started", "turn": 2, "observation": {}})
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        response = client.get("/api/workbench/jobs/resumed/watch")
    assert response.status_code == 200
    assert response.json()["memo_before"] == {"state": "saved", "content": "源存档备忘", "updated_turn": 1}
    assert not m._processes


def test_legacy_notes_are_identified_instead_of_claimed_to_be_persistent(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "run_started", "observation": {"turn": 1}},
           {"type": "turn_started", "turn": 1, "observation": {}},
           {"type": "tool_result", "name": "write_memo", "result": {"ok": True, "memo": {"content": "旧笔记", "count": 1}}},
           {"type": "turn_scored", "turn": 1, "rank_score": 0},
           {"type": "turn_started", "turn": 2, "observation": {}})
    assert WatchJournal(path).read()["memo_before"] == {"state": "legacy"}
