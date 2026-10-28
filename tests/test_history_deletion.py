import asyncio
import json
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.routes_llm_archive import llm_archive_router
from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench import write_json
from guild_manager_bench.api.workbench_lock import FileLease, archive_gate
from test_workbench import manager


def archive(m, name):
    directory = m.archive_root / name
    directory.mkdir(parents=True)
    (directory / "replay.json").write_text(json.dumps({"kind": "llm_replay", "status": "completed", "turns": []}), encoding="utf-8")
    (directory / "trace.jsonl").write_text("historical trace", encoding="utf-8")
    return directory


def job(m, name, run_id=None, status="completed", **config):
    data = {"id": name, "archive_id": run_id, "status": status, "config": config}
    directory = m.root / name
    directory.mkdir(parents=True)
    write_json(directory / "state.json", data)
    (directory / "events.jsonl").write_text("execution log", encoding="utf-8")
    m.jobs[name] = data
    return data


def client(m):
    app = FastAPI()
    app.include_router(workbench_router(m))
    app.include_router(llm_archive_router(m.archive_root))
    return TestClient(app)


def test_delete_archive_removes_all_ended_executions_but_keeps_independent_branch(tmp_path):
    m = manager(tmp_path)
    original = archive(m, "original")
    child = archive(m, "child")
    job(m, "first", "original", status="interrupted")
    job(m, "resumed", "original", resume_run_id="original")
    job(m, "branch", "child", rerun_run_id="original")
    with client(m) as api:
        assert len(api.get("/api/llm/runs").json()["runs"]) == 2
        response = api.delete("/api/workbench/archives/original")
        assert response.status_code == 200, response.text
        assert set(response.json()["deleted_job_ids"]) == {"first", "resumed"}
        assert [run["run_id"] for run in api.get("/api/llm/runs").json()["runs"]] == ["child"]
        assert api.get("/api/workbench/jobs/first/events").status_code == 404
    assert not original.exists()
    assert not (m.root / "first").exists()
    assert not (m.root / "resumed").exists()
    assert child.is_dir()
    assert set(m.jobs) == {"branch"}
    restored = manager(tmp_path)

    async def restart():
        await restored.start()
        try:
            assert set(restored.jobs) == {"branch"}
        finally:
            await restored.close()
    asyncio.run(restart())


def test_delete_cli_and_unarchived_failure(tmp_path):
    m = manager(tmp_path)
    path = archive(m, "cli")
    job(m, "failed", status="failed")
    with client(m) as api:
        assert api.delete("/api/workbench/archives/cli").status_code == 200
        response = api.delete("/api/workbench/jobs/failed")
        assert response.status_code == 200
        assert response.json()["deleted_job_ids"] == ["failed"]
        assert api.delete("/api/workbench/archives/missing").status_code == 404
        assert api.delete("/api/workbench/jobs/missing").status_code == 404
    assert not path.exists()
    assert not m.jobs
    assert not (m.root / "failed").exists()


@pytest.mark.parametrize("status", ["queued", "starting", "running", "stopping"])
def test_active_run_is_not_deletable(tmp_path, status):
    m = manager(tmp_path)
    path = archive(m, "busy")
    job(m, "busy-job", "busy", status=status)
    with client(m) as api:
        assert api.delete("/api/workbench/archives/busy").status_code == 409
        assert api.delete("/api/workbench/jobs/busy-job").status_code == 409
    assert (path / "trace.jsonl").read_text() == "historical trace"
    assert "busy-job" in m.jobs


@pytest.mark.parametrize("reference", ["resume_run_id", "rerun_run_id"])
def test_queued_recovery_protects_source_archive(tmp_path, reference):
    m = manager(tmp_path)
    path = archive(m, "source")
    job(m, "ended", "source")
    job(m, "waiting", status="queued", **{reference: "source"})
    with client(m) as api:
        assert api.delete("/api/workbench/archives/source").status_code == 409
    assert path.is_dir()
    assert set(m.jobs) == {"ended", "waiting"}


def test_held_archive_lock_prevents_any_deletion(tmp_path):
    m = manager(tmp_path)
    path = archive(m, "locked")
    job(m, "ended", "locked")
    with FileLease(path / "workbench.lock"), client(m) as api:
        assert api.delete("/api/workbench/archives/locked").status_code == 409
    assert path.is_dir()
    assert (m.root / "ended" / "state.json").is_file()


def test_delete_is_same_origin_and_rejects_paths_outside_archive_root(tmp_path):
    m = manager(tmp_path)
    path = archive(m, "safe")
    with client(m) as api:
        assert api.delete("/api/workbench/archives/safe", headers={"Origin": "https://other.example"}).status_code == 403
        assert api.delete("/api/workbench/archives/C:%5Coutside").status_code == 422
        assert api.delete("/api/workbench/archives/not-a-run").status_code == 404
    assert path.is_dir()


def test_linked_archive_cannot_delete_another_directory(tmp_path):
    m = manager(tmp_path)
    target = archive(m, "target")
    alias = m.archive_root / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统未授权创建符号链接")
    with client(m) as api:
        assert api.delete("/api/workbench/archives/alias").status_code == 422
    assert (target / "replay.json").is_file()


def test_staging_failure_rolls_back_before_removing_records(tmp_path, monkeypatch):
    m = manager(tmp_path)
    path = archive(m, "rollback")
    job(m, "ended", "rollback")
    original = Path.rename

    def fail_execution(source, target):
        if source == m.root.resolve() / "ended":
            raise PermissionError("execution in use")
        return original(source, target)

    monkeypatch.setattr(Path, "rename", fail_execution)
    with client(m) as api:
        assert api.delete("/api/workbench/archives/rollback").status_code == 503
    assert (path / "replay.json").is_file()
    assert (m.root / "ended" / "state.json").is_file()
    assert "ended" in m.jobs


def test_cleanup_failure_is_reported_and_does_not_resurrect_records(tmp_path, monkeypatch):
    from guild_manager_bench.api import workbench

    m = manager(tmp_path)
    archive(m, "cleanup")
    job(m, "ended", "cleanup")

    def fail_cleanup(path):
        raise PermissionError("cannot purge")

    monkeypatch.setattr(workbench.shutil, "rmtree", fail_cleanup)
    with client(m) as api:
        response = api.delete("/api/workbench/archives/cleanup")
        assert response.status_code == 503
        assert "部分文件" in response.json()["detail"]
        assert api.get("/api/llm/runs").json()["runs"] == []
    assert not m.jobs
    assert not list(m.root.glob("*/state.json"))
    assert list(m.root.glob(".deleting/*/ended/state.json"))


def test_windows_junction_is_not_followed(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction")
    m = manager(tmp_path)
    target = archive(m, "junction-target")
    alias = m.archive_root / "junction-alias"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)
    try:
        with client(m) as api:
            assert api.delete("/api/workbench/archives/junction-alias").status_code == 422
        assert (target / "replay.json").is_file()
    finally:
        alias.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows 路径大小写不敏感")
@pytest.mark.parametrize("queued_reference", [False, True])
def test_windows_case_alias_uses_the_same_archive_identity(tmp_path, queued_reference):
    m = manager(tmp_path)
    path = archive(m, "CaseSource")
    job(m, "ended", "CaseSource")
    if queued_reference:
        job(m, "waiting", status="queued", rerun_run_id="CaseSource")
    with client(m) as api:
        response = api.delete("/api/workbench/archives/casesource")
        assert response.status_code == (409 if queued_reference else 200)
    if queued_reference:
        assert path.is_dir()
        assert set(m.jobs) == {"ended", "waiting"}
    else:
        assert not path.exists()
        assert not m.jobs
        assert not (m.root / "ended").exists()


def test_archive_gate_keeps_lock_while_directory_is_renamed(tmp_path):
    directory = tmp_path / "original"
    directory.mkdir()
    moved = tmp_path / "moved"
    with archive_gate(directory):
        directory.rename(moved)
        with pytest.raises(OSError):
            with archive_gate(directory):
                pass
    with archive_gate(directory):
        pass
