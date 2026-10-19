import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_gpt61_advanced as batch_script
from guild_manager_bench.bench.llm.openai_compat import OpenAIChatCompletionsConfig
from guild_manager_bench.cli_output import RunReporter
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.bench.llm.tools import GuildManagerTools


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "data/presets/advanced").mkdir(parents=True)
    (root / "src/example.py").write_text("original = 1\n", encoding="utf-8")
    (root / "data/presets/advanced/skills.yaml").write_text("skills: []\n", encoding="utf-8")
    (root / ".env").write_text("OPENAI_COMPAT_API_KEY=wrong-provider-key\n", encoding="utf-8")
    monkeypatch.setattr(batch_script, "ROOT", root)
    return root


def gateway_file(tmp_path):
    path = tmp_path / "gateway.yaml"
    path.write_text('api-keys: ["gateway-test-secret"]\n', encoding="utf-8")
    return path


def fake_models(monkeypatch, models):
    def open_request(request, timeout):
        assert request.get_header("Authorization") == "Bearer gateway-test-secret"
        return io.StringIO(json.dumps({"data": [{"id": m} for m in models]}))
    monkeypatch.setattr(batch_script.urllib.request, "urlopen", open_request)


def test_start_creates_three_isolated_jobs_and_keeps_credentials_out_of_files(fake_workspace, tmp_path, monkeypatch):
    fake_models(monkeypatch, [batch_script.MODEL])
    calls = []
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=1000 + len(calls))
    monkeypatch.setattr(batch_script.subprocess, "Popen", popen)
    batch = tmp_path / "batch"
    manifest = batch_script.start_batch(batch, gateway_config=gateway_file(tmp_path),
                                       base_url="http://gateway.test/v1", timeout=1800)
    assert [c[0][-1] for c in calls] == ["high", "medium", "xhigh"]
    assert manifest["game_seed"] == 42 and manifest["scoring_seed"] == 20260529
    assert manifest["max_tool_calls_per_turn"] == 20 and manifest["max_turns"] == 35
    for effort, (_, arguments) in zip(batch_script.EFFORTS, calls):
        assert arguments["env"][batch_script.KEY_ENV] == "gateway-test-secret"
        assert arguments["env"]["PYTHONHASHSEED"] == "0"
        assert batch_script.load_json(batch / effort / "progress.json")["status"] == "starting"
        assert (batch / effort / "console.log").exists()
    for file in batch.rglob("*"):
        if file.is_file():
            assert b"gateway-test-secret" not in file.read_bytes()
            assert b"wrong-provider-key" not in file.read_bytes()
    (fake_workspace / "src/example.py").write_text("original = 2\n", encoding="utf-8")
    assert (batch / ".source_snapshot/src/example.py").read_text(encoding="utf-8") == "original = 1\n"


def test_unavailable_model_does_not_create_batch_or_launch_jobs(fake_workspace, tmp_path, monkeypatch):
    fake_models(monkeypatch, ["different-model"])
    batch = tmp_path / "batch"
    with pytest.raises(ValueError, match="未启动任何游戏"):
        batch_script.start_batch(batch, gateway_config=gateway_file(tmp_path),
                                 base_url="http://gateway.test/v1", timeout=1800)
    assert not batch.exists()


def test_progress_uses_real_reporter_events_without_exporting_model_content():
    definition = load_game_definition(batch_script.ROOT / "data/presets/advanced")
    observation = GuildManagerTools(definition).start_session("progress-test")["observation"]
    reporter = RunReporter(Console(file=io.StringIO(), markup=False), mode="quiet", provider="openai")
    reporter.configure(OpenAIChatCompletionsConfig(model=batch_script.MODEL, api_key="private-key"), streaming=True)
    reporter.on_event({"type": "run_started", "session_id": "progress-test", "config": {"max_tool_calls_per_turn":20},
                       "data": {"preset":"advanced", "game_seed":42, "scoring_seed":20260529}, "observation":observation})
    reporter.on_event({"type":"turn_started", "observation":observation})
    reporter.on_event({"type":"model_request", "step":1})
    reporter.on_event({"type":"model_response", "step":1, "usage":{"prompt_tokens":123,"completion_tokens":45},
                       "timing":{"duration_ms":100}, "text":"private model text", "assistant_metadata":{"reasoning_content":"private thought"}})
    reporter.on_event({"type":"turn_scored", "turn":1, "rank_score":321.25, "duration_ms":10})
    progress = batch_script.progress_record(reporter, "high", time.time()-10, "running")
    assert progress["completed_turns"] == 1 and progress["rank"] == 321.25
    assert progress["requests"] == 1 and progress["input_tokens"] == 123 and progress["output_tokens"] == 45
    assert progress["current_turn"] == 1
    serialized = json.dumps(progress)
    assert all(value not in serialized for value in ("private-key", "private model text", "private thought"))
    output = io.StringIO()
    console = Console(file=output, width=160)
    console.print(batch_script.progress_table(Path("test-batch"), [progress]))
    assert "321.2" in output.getvalue() and "123 / 45" in output.getvalue()


def test_monitor_marks_abrupt_worker_exit_without_changing_saved_state(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    assert batch_script.process_alive(os.getpid())
    assert not batch_script.process_alive(child.pid)
    batch_script.write_json(tmp_path / "batch.json", {"efforts":["high"]})
    job = tmp_path / "high"
    job.mkdir()
    saved = {"effort":"high", "status":"running", "phase":"等待模型"}
    batch_script.write_json(job / "progress.json", saved)
    batch_script.write_json(job / "process.json", {"pid":child.pid})
    assert batch_script.read_progress(tmp_path)[0]["status"] == "exited"
    assert batch_script.load_json(job / "progress.json") == saved


@pytest.mark.parametrize("lock_progress,complete_game", [(False, False), (True, False), (True, True)])
def test_real_workers_preserve_game_outcome_with_or_without_locked_progress(tmp_path, monkeypatch, lock_progress, complete_game):
    if lock_progress and os.name != "nt":
        pytest.skip("复现Windows不允许替换正被普通读者打开的文件")
    requests = []
    held_readers = {}
    batch = tmp_path / "real-batch"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            assert self.path == "/v1/models"
            body = json.dumps({"data":[{"id":batch_script.MODEL}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((payload["model"], payload["reasoning_effort"], self.headers["Authorization"]))
            effort = payload["reasoning_effort"]
            if lock_progress and effort not in held_readers:
                held_readers[effort] = (batch / effort / "progress.json").open(encoding="utf-8")
            if complete_game:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": 0, "id": f"end_{len(requests)}", "type": "function",
                    "function": {"name": "end_turn", "arguments": '{"hunts": []}'},
                }]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"intentional integration test stop"}}')
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    children = []
    original_popen = subprocess.Popen
    def tracked_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(batch_script.subprocess, "Popen", tracked_popen)
    try:
        batch_script.start_batch(batch, gateway_config=gateway_file(tmp_path),
                                 base_url=f"http://127.0.0.1:{server.server_port}/v1", timeout=2)
        for process in children:
            assert process.wait(timeout=90) == (0 if complete_game else 1)
        expected_requests = 35 if complete_game else 1
        assert sorted(effort for _,effort,_ in requests) == sorted(list(batch_script.EFFORTS) * expected_requests)
        assert all(model == batch_script.MODEL and key == "Bearer gateway-test-secret" for model,_,key in requests)
        rows = batch_script.read_progress(batch)
        assert all(r["status"] == ("completed" if complete_game else "failed") and r["requests"] == expected_requests for r in rows)
        if complete_game:
            assert all(r["completed_turns"] == 35 and r["error"] is None and r["rank"] == 0 for r in rows)
        else:
            assert all("intentional integration test stop" in r["error"] for r in rows)
        if lock_progress:
            assert all(batch_script.load_json(batch / e / "progress.json")["status"] == "running"
                       for e in batch_script.EFFORTS)
            assert all("监控写入或展示失败，游戏继续运行" in (batch / e / "console.log").read_text(encoding="utf-8")
                       for e in batch_script.EFFORTS)
        assert len({r["protocol"]["benchmark_version"] for r in rows}) == 1
        for effort in batch_script.EFFORTS:
            job = batch / effort
            replay = json.loads(next((job / "archive").glob("*/replay.json")).read_text(encoding="utf-8"))
            assert replay["data"]["preset"] == "advanced"
            assert replay["data"]["game_seed"] == 42 and replay["data"]["scoring_seed"] == 20260529
            assert replay["agent"]["config"]["reasoning_effort"] == effort
            assert batch_script.load_json(job / "exit.json")["exit_code"] == (0 if complete_game else 1)
            # 运行器在异常传播前保存可恢复的interrupted检查点，工作进程另记failed。
            assert replay["status"] == ("completed" if complete_game else "interrupted")
            if complete_game:
                assert replay["final_observation"]["turn"] == 36
                assert len(replay["turns"]) == 35
            assert "gateway-test-secret" not in (job / "progress.json").read_text(encoding="utf-8")
        console = Console(file=io.StringIO(), width=160)
        console.print(batch_script.progress_table(batch, rows))
        assert "high" in console.file.getvalue() and ("已完成" if complete_game else "失败") in console.file.getvalue()
    finally:
        for reader in held_readers.values():
            reader.close()
        for process in children:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shared_reader_allows_atomic_replacement_and_preserves_old_reader_view(tmp_path):
    path = tmp_path / "progress.json"
    batch_script.write_json(path, {"turn": 1})
    if os.name == "nt":
        # 旧读法在真实Windows上必然阻止替换，不依赖竞态概率。
        with path.open(encoding="utf-8"):
            with pytest.raises(PermissionError):
                batch_script.write_json(path, {"turn": 2})
    with batch_script.open_shared_read(path) as reader:
        batch_script.write_json(path, {"turn": 3})
        assert json.load(reader) == {"turn": 1}
    assert batch_script.load_json(path) == {"turn": 3}


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    original = batch_script.replace_json_file
    attempts = []
    def replace(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("temporary file lock")
        return original(source, target)
    monkeypatch.setattr(batch_script, "replace_json_file", replace)
    monkeypatch.setattr(batch_script.time, "sleep", lambda _: None)
    batch_script.write_json(path, {"turn": 2})
    assert len(attempts) == 3
    assert batch_script.load_json(path) == {"turn": 2}


def test_progress_failure_cannot_escape_event_sink_and_recovers(tmp_path, monkeypatch, capsys):
    reporter = RunReporter(Console(file=io.StringIO()), mode="quiet", provider="openai")
    path = tmp_path / "progress.json"
    sink = batch_script.ProgressSink(reporter, path, "high", time.time(), "private-key")
    real_write = batch_script.write_json
    def denied(*args):
        raise PermissionError("private-key locked")
    monkeypatch.setattr(batch_script, "write_json", denied)
    sink.on_event({"type": "turn_scored", "turn": 1, "rank_score": 123.5, "duration_ms": 10})
    assert reporter.completed == 1 and sink.last_record["rank"] == 123.5
    warning = capsys.readouterr().err
    assert "游戏继续运行" in warning and "private-key" not in warning
    monkeypatch.setattr(batch_script, "write_json", real_write)
    sink.on_event({"type": "turn_scored", "turn": 2, "rank_score": 234.5, "duration_ms": 10})
    assert batch_script.load_json(path)["completed_turns"] == 2
    assert batch_script.load_json(path)["rank"] == 234.5


def test_reporter_display_failure_cannot_escape_event_sink(tmp_path, capsys):
    reporter = RunReporter(Console(file=io.StringIO()), mode="quiet", provider="openai")
    def broken(event):
        raise RuntimeError("display failed")
    reporter.on_event = broken
    sink = batch_script.ProgressSink(reporter, tmp_path / "progress.json", "high", time.time(), "")
    sink.on_event({"type": "model_request"})
    assert "display failed" in capsys.readouterr().err
    assert batch_script.load_json(tmp_path / "progress.json")["status"] == "running"


def test_exit_record_is_authoritative_when_progress_file_is_stale(tmp_path):
    batch_script.write_json(tmp_path / "batch.json", {"efforts": ["high"]})
    job = tmp_path / "high"
    job.mkdir()
    stale = {"effort": "high", "status": "running", "phase": "等待模型", "completed_turns": 1}
    batch_script.write_json(job / "progress.json", stale)
    batch_script.write_json(job / "exit.json", {
        "status": "completed", "exit_code": 0, "finished_at": 123.0, "error": None,
        "score": {"rank_score": 999}, "progress": {"completed_turns": 35, "rank": 999},
    })
    row, = batch_script.read_progress(tmp_path)
    assert row["status"] == "completed" and row["completed_turns"] == 35 and row["rank"] == 999
    assert batch_script.load_json(job / "progress.json") == stale


def test_restart_copies_original_snapshot_not_changed_workspace(fake_workspace, tmp_path, monkeypatch):
    fake_models(monkeypatch, [batch_script.MODEL])
    monkeypatch.setattr(batch_script.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=1234))
    options = dict(gateway_config=gateway_file(tmp_path), base_url="http://gateway.test/v1", timeout=1800)
    old = tmp_path / "old"
    original = batch_script.start_batch(old, **options)
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    (fake_workspace / "src/example.py").write_text("changed = True\n", encoding="utf-8")
    new = tmp_path / "new"
    restarted = batch_script.start_batch(new, snapshot_from=old, **options)
    assert restarted["snapshot_files"] == original["snapshot_files"]
    assert restarted["restarted_from"] == str(old.resolve())
    assert (new / ".source_snapshot/src/example.py").read_text(encoding="utf-8") == "original = 1\n"
    assert before == {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}


def test_restart_rejects_tampered_snapshot_before_launching(fake_workspace, tmp_path, monkeypatch):
    fake_models(monkeypatch, [batch_script.MODEL])
    children = []
    def popen(*a, **kw):
        children.append(1)
        return SimpleNamespace(pid=1234)
    monkeypatch.setattr(batch_script.subprocess, "Popen", popen)
    options = dict(gateway_config=gateway_file(tmp_path), base_url="http://gateway.test/v1", timeout=1800)
    old = tmp_path / "old"
    batch_script.start_batch(old, **options)
    (old / ".source_snapshot/src/example.py").write_text("tampered = True\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="原批次快照指纹不一致"):
        batch_script.start_batch(tmp_path / "new", snapshot_from=old, **options)
    assert len(children) == 3 and not (tmp_path / "new").exists()


@pytest.fixture
def monitor_batch(tmp_path):
    batch_script.write_json(tmp_path / "batch.json", {"efforts": list(batch_script.EFFORTS), "max_turns": 35})
    for effort in batch_script.EFFORTS:
        job = tmp_path / effort
        job.mkdir()
        batch_script.write_json(job / "progress.json", {
            "effort": effort, "status": "running", "phase": "正在等待模型响应",
            "completed_turns": 4, "total_turns": 35, "rank": 169.8,
            "requests": 38, "input_tokens": 503000, "output_tokens": 12400,
            "updated_at": time.time() - 10,
        })
    return tmp_path


def sharing_conflict():
    exc = PermissionError("file is being replaced")
    exc.winerror = 32
    return exc


@pytest.mark.parametrize("error_factory", [sharing_conflict, lambda: FileNotFoundError("replace window")])
def test_monitor_retries_temporary_read_failure(tmp_path, monkeypatch, error_factory):
    path = tmp_path / "progress.json"
    batch_script.write_json(path, {"turn": 7})
    original = batch_script.load_json
    attempts = []
    def read(candidate):
        attempts.append(candidate)
        if len(attempts) < 3:
            raise error_factory()
        return original(candidate)
    monkeypatch.setattr(batch_script, "load_json", read)
    monkeypatch.setattr(batch_script.time, "sleep", lambda _: None)
    assert batch_script.read_monitor_json(path) == {"turn": 7}
    assert len(attempts) == 3


@pytest.mark.parametrize("error_factory", [sharing_conflict, lambda: FileNotFoundError("replace window")])
def test_monitor_keeps_cached_row_updates_other_jobs_and_recovers(monitor_batch, monkeypatch, error_factory):
    batch = monitor_batch
    previous = batch_script.read_progress(batch)
    original = batch_script.load_json
    high = original(batch / "high/progress.json")
    high["completed_turns"] = 6
    batch_script.write_json(batch / "high/progress.json", high)
    medium = original(batch / "medium/progress.json")
    medium["completed_turns"] = 7
    batch_script.write_json(batch / "medium/progress.json", medium)
    before = {p: p.read_bytes() for p in batch.rglob("*.json")}
    blocked = True
    def read(path):
        if blocked and path == batch / "high/progress.json":
            raise error_factory()
        return original(path)
    monkeypatch.setattr(batch_script, "load_json", read)
    stale = batch_script.read_progress(batch, previous=previous)
    assert stale[0]["status"] == "running" and stale[0]["completed_turns"] == 4
    assert "保留上次数据" in stale[0]["monitor_warning"]
    assert stale[1]["completed_turns"] == 7 and "monitor_warning" not in stale[1]
    assert "monitor_warning" not in previous[0]
    output = io.StringIO()
    Console(file=output, width=220).print(batch_script.progress_table(batch, stale))
    assert "进度暂不可读" in output.getvalue() and "上次进度更新" in output.getvalue()
    blocked = False
    fresh = batch_script.read_progress(batch, previous=stale)
    assert fresh[0]["completed_turns"] == 6 and "monitor_warning" not in fresh[0]
    assert before == {p: p.read_bytes() for p in batch.rglob("*.json")}


def test_monitor_without_cache_displays_unknown_not_zero_progress(monitor_batch, monkeypatch):
    original = batch_script.load_json
    def read(path):
        if path == monitor_batch / "high/progress.json":
            raise sharing_conflict()
        return original(path)
    monkeypatch.setattr(batch_script, "load_json", read)
    rows = batch_script.read_progress(monitor_batch)
    assert rows[0]["status"] == "unknown" and rows[0]["completed_turns"] is None
    assert rows[0]["requests"] is None and "尚无可用数据" in rows[0]["monitor_warning"]
    output = io.StringIO()
    Console(file=output, width=220).print(batch_script.progress_table(monitor_batch, rows))
    assert "状态待确认" in output.getvalue() and "—/35" in output.getvalue()
    assert rows[1]["status"] == "running"


def test_monitor_reads_exit_record_even_when_progress_is_unreadable(monitor_batch, monkeypatch):
    batch_script.write_json(monitor_batch / "high/exit.json", {
        "status": "completed", "exit_code": 0, "finished_at": time.time(), "error": None,
        "progress": {"completed_turns": 35, "rank": 999, "requests": 200},
    })
    original = batch_script.load_json
    def read(path):
        if path == monitor_batch / "high/progress.json":
            pytest.fail("完整退出记录存在时不应依赖进度文件")
        return original(path)
    monkeypatch.setattr(batch_script, "load_json", read)
    row = batch_script.read_progress(monitor_batch)[0]
    assert row["status"] == "completed" and row["completed_turns"] == 35 and row["rank"] == 999
    assert "monitor_warning" not in row


def test_monitor_tolerates_unreadable_exit_or_process_metadata(monitor_batch, monkeypatch):
    batch_script.write_json(monitor_batch / "high/exit.json", {"status": "completed"})
    batch_script.write_json(monitor_batch / "medium/process.json", {"pid": os.getpid()})
    original = batch_script.load_json
    def read(path):
        if path.name in {"exit.json", "process.json"}:
            raise sharing_conflict()
        return original(path)
    monkeypatch.setattr(batch_script, "load_json", read)
    rows = batch_script.read_progress(monitor_batch)
    assert all(row["status"] == "running" for row in rows)
    assert "退出记录暂不可读" in rows[0]["monitor_warning"]
    assert "进程状态暂不可读" in rows[1]["monitor_warning"]
    assert "monitor_warning" not in rows[2]


@pytest.mark.skipif(os.name != "nt", reason="需要真实Windows独占文件句柄")
def test_monitor_survives_actual_windows_read_lock_and_recovers(monitor_batch):
    import ctypes
    from ctypes import wintypes
    previous = batch_script.read_progress(monitor_batch)
    path = monitor_batch / "high/progress.json"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        stale = batch_script.read_progress(monitor_batch, previous=previous)
        assert "进度暂不可读" in stale[0]["monitor_warning"]
        assert stale[0]["rank"] == previous[0]["rank"]
        assert "monitor_warning" not in stale[1]
    finally:
        assert kernel.CloseHandle(handle)
    fresh = batch_script.read_progress(monitor_batch, previous=stale)
    assert "monitor_warning" not in fresh[0]


def test_watch_passes_last_rows_to_next_refresh_and_does_not_exit_on_stale_data(monkeypatch, tmp_path):
    initial = [{"status": "running"}]
    stale = [{"status": "running", "monitor_warning": "进度暂不可读"}]
    final = [{"status": "completed"}]
    calls = []
    def read(batch, previous=None):
        calls.append(previous)
        return [initial, stale, final][len(calls)-1]
    monkeypatch.setattr(batch_script, "read_progress", read)
    monkeypatch.setattr(batch_script, "progress_table", lambda *args: "monitor test")
    monkeypatch.setattr(batch_script.time, "sleep", lambda _: None)
    assert batch_script.watch(tmp_path, compact=True) == 0
    assert calls[0] is None and calls[1] is initial and calls[2] is stale
