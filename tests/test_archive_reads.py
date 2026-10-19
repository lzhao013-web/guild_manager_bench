import asyncio
from collections import Counter
import json
import math
import os
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.middleware.gzip import GZipMiddleware

from guild_manager_bench.api import archive_io
from guild_manager_bench.api import routes_llm_archive as routes


def save(root, run_id, *, status="running"):
    path = root / run_id / "replay.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "kind": "llm_replay", "status": status, "turns": [],
        "data": {"preset": "full"}, "agent": {"config": {"model": "先锋"}},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def app_for(root):
    app = FastAPI()
    app.include_router(routes.llm_archive_router(root))
    return app


def test_list_reuses_unchanged_summaries_and_detects_edits_additions_and_deletions(tmp_path, monkeypatch):
    first = save(tmp_path, "a")
    second = save(tmp_path, "b")
    reads = Counter()
    original = routes._read_json

    def read(path):
        reads[path.parent.name] += 1
        return original(path)

    monkeypatch.setattr(routes, "_read_json", read)
    with TestClient(app_for(tmp_path)) as client:
        initial = client.get("/api/llm/runs").json()
        assert [run["run_id"] for run in initial["runs"]] == ["b", "a"]
        assert initial["runs"][0]["model"] == "先锋"
        assert client.get("/api/llm/runs").json() == initial
        assert reads == {"a": 1, "b": 1}

        stat = first.stat()
        first.write_bytes(first.read_bytes().replace(b"running", b"stopped"))
        os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        changed = client.get("/api/llm/runs").json()
        assert changed["runs"][1]["status"] == "stopped"
        assert reads == {"a": 2, "b": 1}

        stat = second.stat()
        temporary = second.with_suffix(".next")
        temporary.write_bytes(second.read_bytes().replace(b"running", b"completed"))
        os.utime(temporary, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        temporary.replace(second)
        added = save(tmp_path, "c")
        first.unlink()
        response = client.get("/api/llm/runs").json()
        assert [(run["run_id"], run["status"]) for run in response["runs"]] == [("c", "running"), ("b", "completed")]
        assert reads == {"a": 2, "b": 2, "c": 1}
        assert client.get("/api/llm/runs").json() == response
        assert reads == {"a": 2, "b": 2, "c": 1}
        assert sorted(tmp_path.rglob("*.*")) == sorted([second, added])


def test_replay_get_preserves_data_without_building_json_objects(tmp_path, monkeypatch):
    path = save(tmp_path, "sample")
    payload = json.loads(path.read_bytes())
    payload["probe"] = {"seed": 2**100 + 1, "text": "保留  空格\n换行", "float": -0.0}
    before = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(before)

    def no_parse(*args, **kwargs):
        raise AssertionError("读取完整回放不应构建 JSON 对象")

    monkeypatch.setattr(routes, "_read_json", no_parse)
    monkeypatch.setattr(archive_io.msgspec.json, "decode", no_parse)
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/llm/runs/sample/replay")
        assert response.status_code == 200
        assert response.json() == payload
        assert len(response.content) < len(before)
        assert response.headers["content-type"] == "application/json"
        assert int(response.headers["content-length"]) == len(response.content)
        assert response.json()["probe"]["seed"] == 2**100 + 1
        assert client.get("/api/llm/runs/missing/replay").status_code == 404
        assert client.get("/api/llm/runs/%2E%2E/replay").status_code == 400
        assert client.get("/api/llm/runs/bad%5Cname/replay").status_code == 400
    assert path.read_bytes() == before


@pytest.mark.parametrize("body,detail", [
    (b'{"broken":', "invalid JSON: replay.json"),
    (b"[]", "replay must be a JSON object"),
    (b"null", "replay must be a JSON object"),
])
def test_replay_get_keeps_invalid_archive_errors(tmp_path, body, detail):
    path = save(tmp_path, "sample")
    path.write_bytes(body)
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/llm/runs/sample/replay")
        assert response.status_code == 500
        assert response.json()["detail"] == detail
    assert path.read_bytes() == body


def test_list_refresh_does_not_hide_corrupt_or_non_object_archives(tmp_path, monkeypatch):
    path = save(tmp_path, "sample")
    original = routes._read_json
    reads = []

    def read(target):
        reads.append(target)
        return original(target)

    monkeypatch.setattr(routes, "_read_json", read)
    with TestClient(app_for(tmp_path)) as client:
        assert len(client.get("/api/llm/runs").json()["runs"]) == 1
        path.write_bytes(b"[]")
        assert client.get("/api/llm/runs").json() == {"runs": []}
        assert client.get("/api/llm/runs").json() == {"runs": []}
        assert len(reads) == 2
        path.write_bytes(b'{"broken":')
        response = client.get("/api/llm/runs")
        assert response.status_code == 500
        assert response.json()["detail"] == "invalid JSON: replay.json"
        save(tmp_path, "sample", status="completed")
        assert client.get("/api/llm/runs").json()["runs"][0]["status"] == "completed"


def test_replay_response_keeps_existing_http_gzip_support(tmp_path):
    path = save(tmp_path, "sample")
    payload = json.loads(path.read_bytes())
    payload["trace"] = "保留  空格\n" * 1000
    before = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(before)
    app = app_for(tmp_path)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    with TestClient(app) as client:
        response = client.get("/api/llm/runs/sample/replay")
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert int(response.headers["content-length"]) < len(before)
        assert response.json() == json.loads(before)
    assert path.read_bytes() == before


def test_cold_list_does_not_block_other_requests_or_duplicate_concurrent_reads(tmp_path, monkeypatch):
    save(tmp_path, "sample")
    entered, release = Event(), Event()
    original = routes._read_json
    reads = []

    def slow_read(path):
        reads.append(path)
        entered.set()
        assert release.wait(3), "列表读取必须在工作线程中执行"
        return original(path)

    monkeypatch.setattr(routes, "_read_json", slow_read)
    app = app_for(tmp_path)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.get("/api/llm/runs"))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                second = asyncio.create_task(client.get("/api/llm/runs"))
                assert (await client.get("/ping")).json() == {"ok": True}
            finally:
                release.set()
                responses = await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
            for response in responses:
                if isinstance(response, BaseException):
                    raise response
                assert response.status_code == 200
            assert responses[0].json() == responses[1].json()
            assert len(reads) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    {"text": "先锋😀", "seed": 2**100 + 1, "negative_seed": -(2**100 + 1)},
    {"bool": True, "null": None, "float": 1.2345678901234567, "zero": -0.0, "nested": [{"x": 1}]},
])
def test_archive_decoder_preserves_values_and_types(tmp_path, value):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    result = archive_io.read_archive_json(path)
    assert result == value
    for key in value:
        assert type(result[key]) is type(value[key])


def test_archive_decoder_keeps_stdlib_legacy_values_and_decode_errors(tmp_path):
    path = tmp_path / "replay.json"
    path.write_bytes(b'{"legacy":NaN,"large":1e999,"text":"\\ud800"}')
    result = archive_io.read_archive_json(path)
    assert math.isnan(result["legacy"])
    assert result["large"] == math.inf
    assert result["text"] == "\ud800"
    path.write_bytes(b'{"broken":')
    with pytest.raises(json.JSONDecodeError):
        archive_io.read_archive_json(path)


def test_archive_json_read_closes_the_handle_before_parsing(tmp_path, monkeypatch):
    path = save(tmp_path, "sample")
    before = path.read_bytes()
    temporary = path.with_suffix(".next")
    temporary.write_bytes(before.replace(b"running", b"completed"))
    original = archive_io.msgspec.json.decode

    def parse(raw):
        temporary.replace(path)
        return original(raw)

    monkeypatch.setattr(archive_io.msgspec.json, "decode", parse)
    assert archive_io.read_archive_json(path)["status"] == "running"
    assert json.loads(path.read_bytes())["status"] == "completed"
