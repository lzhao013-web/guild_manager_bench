from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench_trajectories import TrajectoryStore
from test_workbench import manager


def observation():
    return {"turn": 1, "gold": 100, "experience_pool": 0, "materials": {}, "adventurers": [
        {"adventurer_id": "hero", "name": "战士", "skills": [], "tactics": {}}
    ], "equipment_inventory": [], "global_upgrades": [
        {"upgrade_id": "training", "name": "训练场", "unlocked": False}
    ], "crafting_recipes": [{"recipe_id": "sword", "name": "铁剑"}], "recruit_candidates": [], "monsters": []}


def write(path, events, *, archived=True, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        for event in events:
            record = {"record_type": "event", "created_at": "2026-10-19T12:00:00", "event": event} if archived else {"at": "2026-10-19T12:00:00", "event": event}
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def operation(name, args, before, after, summary):
    return [{"type": "tool_call", "name": name, "arguments": args, "call_id": summary},
            {"type": "tool_result", "name": name, "arguments": args, "call_id": summary, "result": {
                "ok": True, "_observation_after": after, "event": {"summary": summary, "changes": [
                    {"kind": "resource", "label": "金币", "before": before["gold"], "after": after["gold"]}
                ]}}}]


def test_whole_run_history_merges_calls_and_distinguishes_instances_and_resume_rollback(tmp_path):
    path = tmp_path / "trace.jsonl"
    first = observation()
    bought = deepcopy(first)
    bought["global_upgrades"][0]["unlocked"] = True
    bought["gold"] = 80
    sword1 = deepcopy(bought)
    sword1["equipment_inventory"] = [{"instance_id": "eq-1", "name": "铁剑", "equipped_by": None}]
    sword2 = deepcopy(sword1)
    sword2["turn"] = 2
    sword2["equipment_inventory"].append({"instance_id": "eq-2", "name": "铁剑", "equipped_by": None})
    equipped = deepcopy(sword2)
    equipped["equipment_inventory"][1]["equipped_by"] = "hero"
    events = [{"type": "turn_started", "turn": 1, "observation": first},
              *operation("purchase_upgrade", {"upgrade_id": "training"}, first, bought, "购买训练场"),
              *operation("craft_equipment", {"recipe_id": "sword"}, bought, sword1, "制作第一件铁剑"),
              {"type": "turn_completed"},
              {"type": "turn_started", "turn": 2, "observation": {**sword1, "turn": 2}},
              *operation("craft_equipment", {"recipe_id": "sword"}, sword1, sword2, "制作第二件铁剑"),
              *operation("equip_item", {"adventurer_id": "hero", "equipment_instance_id": "eq-2"}, sword2, equipped, "战士穿戴第二件铁剑")]
    write(path, events)
    store = TrajectoryStore()
    rows = store.read(path, archived=True)["entries"]
    assert len(rows) == 4
    assert rows[0]["lifecycle"] == "confirmed" and rows[2]["lifecycle"] == "uncommitted"
    assert {s["id"] for s in rows[1]["subjects"]} == {"sword", "eq-1"}
    assert {s["id"] for s in rows[2]["subjects"]} == {"sword", "eq-2"}
    assert {s["id"] for s in rows[3]["subjects"]} == {"hero", "eq-2"}
    assert next(s for s in rows[1]["subjects"] if s["kind"] == "equipment")["created"] is True
    assert next(s for s in rows[3]["subjects"] if s["kind"] == "equipment")["ownership"] == {"before": None, "after": {"id": "hero", "name": "战士"}}
    assert rows[3]["ordinal"] == 2 and rows[3]["turn"] == 2
    # 续跑前已经确认的行动仍在；未提交分支回退，即使之后复用了同一个装备 ID 也不能混成有效购买。
    write(path, [{"type": "run_resumed", "restored_observation": {**sword1, "turn": 2}},
                 {"type": "turn_started", "turn": 2, "observation": {**sword1, "turn": 2}},
                 *operation("craft_equipment", {"recipe_id": "sword"}, sword1, sword2, "续跑后重新制作"),
                 {"type": "turn_completed"}], append=True)
    rows = store.read(path, archived=True)["entries"]
    assert [r["lifecycle"] for r in rows] == ["confirmed", "confirmed", "rolled_back", "rolled_back", "confirmed"]
    page = store.read(path, archived=True, attempt=rows[0]["attempt"])
    assert page["selected"] == rows[0]["attempt"]
    target = next(r for r in page["records"] if r["cursor"] == rows[0]["cursor"])
    assert target["event"]["result"]["_observation_after"]["global_upgrades"][0]["unlocked"]


def test_resume_checkpoint_can_roll_back_a_previously_completed_but_unsaved_turn(tmp_path):
    first = observation()
    after = deepcopy(first)
    after["global_upgrades"][0]["unlocked"] = True
    path = tmp_path / "trace.jsonl"
    write(path, [{"type": "turn_started", "turn": 1, "observation": first},
                 {"type": "turn_completed"},
                 {"type": "turn_started", "turn": 2, "observation": {**first, "turn": 2}},
                 *operation("purchase_upgrade", {"upgrade_id": "training"}, {**first, "turn": 2}, {**after, "turn": 2}, "购买训练场"),
                 {"type": "turn_completed"}])
    store = TrajectoryStore()
    assert store.read(path, archived=True)["entries"][0]["lifecycle"] == "confirmed"
    write(path, [{"type": "run_resumed", "restored_observation": {**first, "turn": 2}}], append=True)
    row = store.read(path, archived=True)["entries"][0]
    assert row["lifecycle"] == "rolled_back"
    assert next(a for a in store.read(path, archived=True, attempt=row["attempt"])["attempts"] if a["id"] == row["attempt"])["status"] == "rolled_back"


def test_failed_and_pending_calls_are_not_confirmed_successes_and_partial_tail_is_not_read(tmp_path):
    path = tmp_path / "events.jsonl"
    write(path, [{"type": "turn_started", "turn": 3, "observation": observation()},
                 {"type": "tool_call", "name": "craft_equipment", "arguments": {"recipe_id": "sword"}, "call_id": "x"},
                 {"type": "tool_result", "name": "craft_equipment", "arguments": {"recipe_id": "sword"}, "call_id": "x", "result": {"ok": False, "error": "金币不足"}},
                 {"type": "tool_call", "name": "purchase_upgrade", "arguments": {"upgrade_id": "training"}, "call_id": "y"}], archived=False)
    with path.open("ab") as stream:
        stream.write(b'{"at":')
    rows = TrajectoryStore().read(path, archived=False)["entries"]
    assert len(rows) == 2
    assert rows[0]["event"]["result"]["ok"] is False
    assert rows[1]["event"]["type"] == "tool_call"
    assert all(row["lifecycle"] == "uncommitted" for row in rows)


def test_api_uses_same_archive_across_jobs_does_not_leak_raw_data_and_locates_exact_record(tmp_path):
    m = manager(tmp_path)
    path = m.archive_root / "same" / "trace.jsonl"
    before = observation()
    after = deepcopy(before)
    after["global_upgrades"][0]["unlocked"] = True
    write(path, [{"type": "run_started", "observation": before},
                 {"type": "model_response", "text": "private model text", "raw": {"api_key": "never-expose"}},
                 {"type": "turn_started", "turn": 1, "observation": before},
                 *operation("purchase_upgrade", {"upgrade_id": "training"}, before, after, "购买训练场"),
                 {"type": "turn_completed"}])
    original = path.read_bytes()
    for identity in ("old", "new"):
        m.jobs[identity] = {"id": identity, "archive_id": "same", "config": {}}
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        data = client.get("/api/workbench/jobs/new/trajectories").json()
        assert data == client.get("/api/workbench/jobs/old/trajectories").json()
        assert data["origin"] == "archive" and data["warning"] is None
        m.jobs["new"]["checkpoint_error"] = "fixture disk error"
        assert "检查点保存失败" in client.get("/api/workbench/jobs/new/trajectories").json()["warning"]
        assert all(text not in json.dumps(data) for text in ("never-expose", "private model text", "_observation_after"))
        row = data["entries"][0]
        page = client.get("/api/workbench/jobs/new/trajectories", params={"attempt": row["attempt"]})
        assert page.status_code == 200
        assert any(r["cursor"] == row["cursor"] for r in page.json()["records"])
        assert client.get("/api/workbench/jobs/new/trajectories?attempt=999999").status_code == 422
        assert client.get("/api/workbench/jobs/missing/trajectories").status_code == 404
        m.jobs["bad"] = {"id": "bad", "archive_id": "../outside", "config": {}}
        assert client.get("/api/workbench/jobs/bad/trajectories").status_code == 400
    assert path.read_bytes() == original


def test_missing_archive_reports_partial_coverage_instead_of_claiming_never_bought(tmp_path):
    m = manager(tmp_path)
    m.jobs["waiting"] = {"id": "waiting", "archive_id": None, "config": {}}
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        data = client.get("/api/workbench/jobs/waiting/trajectories").json()
        assert data["entries"] == [] and data["origin"] == "execution" and data["warning"]


def test_stopped_job_does_not_show_an_archived_attempt_as_still_running(tmp_path):
    m = manager(tmp_path)
    m.jobs["one"] = {"id": "one", "archive_id": "same", "status": "interrupted", "config": {}}
    write(m.archive_root / "same" / "trace.jsonl", [
        {"type": "turn_started", "turn": 2, "observation": {**observation(), "turn": 2}},
        {"type": "tool_result", "name": "purchase_upgrade", "arguments": {"upgrade_id": "training"}, "result": {"ok": True}},
    ])
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        data = client.get("/api/workbench/jobs/one/trajectories").json()
        assert data["entries"][0]["lifecycle"] == "uncommitted"
        page = client.get("/api/workbench/jobs/one/trajectories", params={"attempt": data["entries"][0]["attempt"]}).json()
        assert page["attempts"][-1]["status"] == "interrupted"


def test_locations_pin_the_log_origin_and_raw_offsets_cannot_read_archive_metadata(tmp_path):
    m = manager(tmp_path)
    m.jobs["one"] = {"id": "one", "archive_id": "same", "config": {}}
    events = [{"type": "turn_started", "turn": 1, "observation": observation()},
              {"type": "tool_result", "name": "purchase_upgrade", "arguments": {"upgrade_id": "training"}, "result": {"ok": False, "error": "金币不足"}}]
    write(m.root / "one" / "events.jsonl", events, archived=False)
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        execution = client.get("/api/workbench/jobs/one/trajectories").json()
        path = m.archive_root / "same" / "trace.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"record_type": "archive_started", "config": {"private": "not-an-event"}}) + "\n", encoding="utf-8")
        write(path, events, append=True)
        archive = client.get("/api/workbench/jobs/one/trajectories").json()
        assert archive["origin"] == "archive"
        old = client.get("/api/workbench/jobs/one/trajectories", params={"attempt": execution["entries"][0]["attempt"], "origin": "execution"})
        assert old.status_code == 200 and old.json()["origin"] == "execution"
        page = client.get("/api/workbench/jobs/one/trajectories", params={"attempt": archive["entries"][0]["attempt"], "origin": "archive"}).json()
        target = next(row for row in page["records"] if row["event"]["type"] == "tool_result")
        raw = client.get("/api/workbench/jobs/one/trajectories", params={"raw_after": target["after"], "origin": "archive"})
        assert raw.status_code == 200 and raw.json()["event"]["arguments"]["upgrade_id"] == "training"
        assert client.get("/api/workbench/jobs/one/trajectories?raw_after=0&origin=archive").status_code == 422
        assert client.get(f"/api/workbench/jobs/one/trajectories?raw_after={2 ** 100}").status_code == 422
        assert client.get("/api/workbench/jobs/one/trajectories?attempt=1&raw_after=0").status_code == 422
        path.unlink()
        assert client.get("/api/workbench/jobs/one/trajectories", params={"attempt": archive["entries"][0]["attempt"], "origin": "archive"}).status_code == 404


def test_numeric_references_are_resolved_only_with_recorded_mapping(tmp_path):
    before = observation()
    after = deepcopy(before)
    after["global_upgrades"][0]["unlocked"] = True
    events = [{"type": "turn_started", "turn": 1, "observation": before},
              *operation("purchase_upgrade", {"upgrade_id": 7}, before, after, "购买训练场")]
    events[-1]["result"]["_llm_refs"] = {"upgrade": {"training": 7}}
    path = tmp_path / "trace.jsonl"
    write(path, events)
    row = TrajectoryStore().read(path, archived=True)["entries"][0]
    assert row["subjects"] == [{"kind": "upgrade", "id": "training", "name": "训练场"}]
    assert row["names"]["training"] == "训练场"
    unknown = tmp_path / "unknown.jsonl"
    write(unknown, [{"type": "turn_started", "turn": 1, "observation": before},
                    {"type": "tool_result", "name": "purchase_upgrade", "arguments": {"upgrade_id": 7}, "result": {"ok": False, "error": "无效引用"}}])
    assert TrajectoryStore().read(unknown, archived=True)["entries"][0]["subjects"] == []
