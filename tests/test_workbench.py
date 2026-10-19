"""用本地假模型服务验证真实隔离进程，不发出付费模型请求。"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest
from pydantic import ValidationError

from guild_manager_bench.api.workbench import Workbench, read_events
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec, SchedulerSettings
from guild_manager_bench.game.presets import resolve_data_source


@contextmanager
def model_server():
    release = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload["model"])
            if payload["model"].startswith("slow"):
                release.wait(60)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": f"call-{len(calls)}", "type": "function", "function": {"name": "end_turn", "arguments": '{"hunts":[]}'}}]}, "finish_reason": None}]}
            end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 17, "completion_tokens": 5}}
            try:
                self.wfile.write((f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n").encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", release, calls
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def manager(tmp_path, **kwargs):
    preset = resolve_data_source(Path(__file__).resolve().parents[1] / "data", "default")
    return Workbench(tmp_path / "workbench", tmp_path / "archives", {"default": preset}, **kwargs)


def batch(url, *models):
    return CreateBatch(name="构造的并发测试", runs=[RunSpec(preset="default", model=m, base_url=url, api_key="secret-for-test") for m in models])


async def wait_for(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(.1)
    raise AssertionError("等待工作台状态超时")


def test_real_workers_queue_stop_one_and_complete_others(tmp_path):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            try:
                m.configure(SchedulerSettings(max_parallel=2, per_endpoint=2))
                a, b, c = m.create_batch(batch(url, "slow-a", "slow-b", "fast-c"))["jobs"]
                await wait_for(lambda: len(calls) == 2 and a["phase"] == b["phase"] == "waiting_model")
                assert a["status"] == b["status"] == "running"
                assert c["status"] == "queued"
                assert a["phase"] == "waiting_model"
                assert "secret-for-test" not in json.dumps(m.snapshot())
                assert all("secret-for-test" not in p.read_text(encoding="utf-8") for p in m.root.glob("*/state.json"))
                m.stop(a["id"])
                assert a["status"] == "stopping"
                await wait_for(lambda: a["status"] == "interrupted")
                assert b["status"] == "running"
                release.set()
                await wait_for(lambda: b["status"] == c["status"] == "completed")
                assert c["calls"] == 8
                assert c["usage"]["input_tokens"] == 17 * 8
                assert c["usage"]["output_tokens"] == 5 * 8
                assert c["last_score"] == 0  # 空队伍是合法的零分，不是缺失值。
                assert (m.archive_root / c["archive_id"] / "replay.json").is_file()
                page = m.events(c["id"], 0, 2)
                next_page = m.events(c["id"], page["cursor"], 2)
                assert page["events"][-1]["cursor"] < next_page["events"][0]["cursor"]
            finally:
                await m.close()
        asyncio.run(scenario())


def test_endpoint_limit_and_queued_cancellation(tmp_path):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            try:
                m.configure(SchedulerSettings(max_parallel=2, per_endpoint=1))
                a, b = m.create_batch(batch(url, "slow-a", "never-start"))["jobs"]
                await wait_for(lambda: bool(calls))
                assert b["status"] == "queued"
                m.stop(b["id"])
                assert b["status"] == "interrupted"
                release.set()
                await wait_for(lambda: a["status"] == "completed")
                assert "never-start" not in calls
            finally:
                await m.close()
        asyncio.run(scenario())


def test_restart_preserves_terminal_jobs_and_interrupts_unstarted_queue(tmp_path):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        job = m.create_batch(batch("http://127.0.0.1:1/v1", "queued"))["jobs"][0]
        # 模拟重启前的磁盘状态。未让调度循环启动真实网络请求。
        await m.close()
        path = m.root / job["id"] / "state.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["status"] = "queued"
        path.write_text(json.dumps(stored), encoding="utf-8")
        restored = manager(tmp_path)
        await restored.start()
        try:
            assert restored.get(job["id"])["status"] == "interrupted"
            assert "重启" in restored.get(job["id"])["error"]
        finally:
            await restored.close()
    asyncio.run(scenario())


def test_resume_archive_lock_and_failed_batch_is_atomic(tmp_path):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        archive = m.archive_root / "existing"
        archive.mkdir(parents=True)
        (archive / "replay.json").write_text(json.dumps({"status": "interrupted", "data": {"preset": "default"}, "config": {}}))
        spec = RunSpec(preset="default", model="local-model", base_url="http://127.0.0.1:1/v1", resume_run_id="existing")
        try:
            with pytest.raises(ValueError, match="已经有"):
                m.create_batch(CreateBatch(name="重复续跑", runs=[spec, spec]))
            assert not m.jobs
            m.create_batch(CreateBatch(name="一次续跑", runs=[spec]))
            with pytest.raises(ValueError, match="已经有"):
                m.create_batch(CreateBatch(name="再续跑", runs=[spec]))
        finally:
            await m.close()
    asyncio.run(scenario())


def test_event_cursor_does_not_commit_partial_lines(tmp_path):
    journal = tmp_path / "events.jsonl"
    first = json.dumps({"event": {"type": "model_delta", "text": "中文"}}, ensure_ascii=False).encode()
    journal.write_bytes(first + b"\n" + b'{"event":')
    page = read_events(journal, 0)
    assert len(page["events"]) == 1
    assert page["cursor"] == len(first) + 1
    assert read_events(journal, page["cursor"])["events"] == []
    with pytest.raises(ValueError, match="边界"):
        read_events(journal, 2)
    with pytest.raises(ValueError, match="范围"):
        read_events(journal, 10000)


@pytest.mark.parametrize("url", ["https://user:password@example.com/v1", "https://example.com/v1?key=secret", "file:///etc/passwd"])
def test_credentials_cannot_be_hidden_in_persisted_url(url):
    with pytest.raises(ValidationError):
        RunSpec(preset="default", base_url=url)
