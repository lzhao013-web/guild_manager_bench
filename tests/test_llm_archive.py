import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from guild_manager_bench.bench.llm import archive


def test_concurrent_archives_with_identical_timestamps_are_isolated(tmp_path, monkeypatch):
    created_at = "20261030-143930-994662"
    monkeypatch.setattr(archive, "_timestamp", lambda: created_at)
    sessions = [f"session-{index}" for index in range(4)]

    def create(session_id):
        writer = archive.start_llm_run_archive(
            tmp_path,
            session_id=session_id,
            config={},
            agent={"config": {"model": "deepseek-v4.1-flash"}},
        )
        writer.write_replay(status="running", turns=[], final_observation={"turn": 1})
        return writer

    with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
        writers = list(executor.map(create, sessions))

    assert len({writer.directory for writer in writers}) == len(sessions)
    for session_id, writer in zip(sessions, writers):
        records = [json.loads(line) for line in writer.trace_jsonl_path.read_text(encoding="utf-8").splitlines()]
        assert [record["session_id"] for record in records] == [session_id]
        assert records[0]["record_type"] == "archive_started"
        replay = json.loads(writer.replay_path.read_text(encoding="utf-8"))
        assert replay["session_id"] == session_id
        assert replay["created_at"] == created_at
        assert replay["status"] == "running"


@pytest.mark.parametrize("legacy_directory", [True, False], ids=["legacy-name", "unique-name"])
def test_resume_keeps_existing_directory_and_trace(tmp_path, legacy_directory):
    created_at = "20261030-143930-994662"
    agent = {"config": {"model": "deepseek-v4.1-flash"}}
    writer = archive.LlmRunArchiveWriter(
        tmp_path,
        session_id="same-session",
        config={},
        agent=agent,
        created_at=created_at,
        directory=tmp_path / f"{created_at}_deepseek-v4.1-flash" if legacy_directory else None,
    )
    writer.write_replay(status="interrupted", turns=[], final_observation={"turn": 1})
    original_trace = writer.trace_jsonl_path.read_bytes()
    history = [{"resumed_from_status": "interrupted"}]

    resumed = archive.resume_llm_run_archive(
        writer.directory,
        session_id="same-session",
        created_at=created_at,
        config={},
        agent=agent,
        resume_history=history,
    )
    resumed.write_replay(status="running", turns=[], final_observation={"turn": 1})

    assert resumed.directory == writer.directory
    assert list(tmp_path.iterdir()) == [writer.directory]
    assert resumed.trace_jsonl_path.read_bytes().startswith(original_trace)
    records = [json.loads(line) for line in resumed.trace_jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert [record["record_type"] for record in records] == ["archive_started", "archive_resumed"]
    assert [record["session_id"] for record in records] == ["same-session", "same-session"]
    replay = json.loads(resumed.replay_path.read_text(encoding="utf-8"))
    assert replay["session_id"] == "same-session"
    assert replay["created_at"] == created_at
    assert replay["status"] == "running"
    assert replay["resume_history"] == history
