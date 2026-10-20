import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.api_profiles import ApiProfiles, CredentialError, ProfileWrite
from guild_manager_bench.api.api_history import parse_history
from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.model_catalog import ModelCatalogError, fetch_models
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from test_workbench import manager, wait_for


class MemoryVault:
    """只替换外部系统凭证库，真实 HTTP、存储和工作进程仍然执行。"""

    def __init__(self):
        self.values = {}

    def get(self, reference):
        if reference not in self.values:
            raise CredentialError("测试凭证缺失，请重新填写密钥")
        return self.values[reference]

    def set(self, reference, secret):
        self.values[reference] = secret

    def delete(self, reference):
        self.values.pop(reference, None)


@contextmanager
def catalog_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
            path, query = urlsplit(self.path).path, parse_qs(urlsplit(self.path).query)
            if path == "/error/models":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"provider echoes fake-secret in its error body")
                return
            if path == "/redirect/models":
                self.send_response(302)
                self.send_header("Location", "/stolen/models")
                self.end_headers()
                return
            if "anthropic-version" in self.headers:
                assert path == "/v1/models"
                assert self.headers["x-api-key"] == "fake-secret"
                data = (
                    {
                        "data": [{"id": "claude-first", "display_name": "Claude First"}],
                        "has_more": True,
                        "last_id": "first & token",
                    }
                    if not query.get("after_id")
                    else {"data": [{"id": "claude-last"}], "has_more": False}
                )
            elif path == "/v1beta/models":
                assert self.headers["x-goog-api-key"] == "fake-secret"
                assert query["pageSize"] == ["1000"]
                data = (
                    {
                        "models": [
                            {
                                "name": "models/gemini-chat",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/embedding-only",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                        ],
                        "nextPageToken": "next & token",
                    }
                    if not query.get("pageToken")
                    else {"models": [{"name": "models/gemini-second"}]}
                )
            elif path == "/gateway/models":
                assert self.headers["x-goog-api-key"] == "fake-secret"
                data = {
                    "object": "list",
                    "data": [{"id": "gateway-gemini"}, {"id": "gateway-tool"}],
                }
            else:
                assert self.headers["Authorization"] == "Bearer fake-secret"
                data = {"data": [{"id": "discovered-model"}, {"id": "profile-model"}]}
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {"method": "POST", "model": payload["model"], "headers": dict(self.headers)}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {"name": "end_turn", "arguments": '{"hunts":[]}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
            end = {
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 2},
            }
            self.wfile.write(
                (
                    f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n"
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_profile_survives_reload_without_secret_in_metadata_or_listing(tmp_path):
    vault = MemoryVault()
    store = ApiProfiles(tmp_path, vault)
    profile = store.save(
        ProfileWrite(
            name="测试 API",
            base_url="https://example.test/v1",
            api_key="fake-secret",
            default_model="profile-model",
        )
    )
    assert profile["credential_present"] is True
    assert "fake-secret" not in json.dumps(profile)
    assert "credential_id" not in profile
    assert "fingerprint" not in profile
    assert "fake-secret" not in store.path.read_text(encoding="utf-8")
    restored = ApiProfiles(tmp_path, vault)
    assert restored.list() == [profile]
    assert restored.resolve(profile["id"])["api_key"] == "fake-secret"
    retained = restored.save(
        ProfileWrite(name="改名不换密钥", base_url="https://example.test/v1"), profile["id"]
    )
    assert restored.resolve(profile["id"])["api_key"] == "fake-secret"
    assert retained["credential_present"]
    cleared = restored.save(
        ProfileWrite(name="清除凭证", base_url="https://example.test/v1", clear_api_key=True),
        profile["id"],
    )
    assert not cleared["credential_present"]
    assert vault.values == {}
    restored.delete(profile["id"])
    assert restored.list() == []


def test_missing_credential_never_silently_uses_environment_key(tmp_path, monkeypatch):
    vault = MemoryVault()
    store = ApiProfiles(tmp_path, vault)
    profile = store.save(
        ProfileWrite(
            name="必须有原凭证", base_url="https://example.test/v1", api_key="original-secret"
        )
    )
    vault.values.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-global-key")
    with pytest.raises(CredentialError, match="凭证缺失"):
        store.resolve(profile["id"])
    store.save(
        ProfileWrite(
            name="重新输入修复", base_url="https://example.test/v1", api_key="replacement-secret"
        ),
        profile["id"],
    )
    assert store.resolve(profile["id"])["api_key"] == "replacement-secret"


def test_failed_metadata_write_preserves_original_secret(tmp_path, monkeypatch):
    vault = MemoryVault()
    store = ApiProfiles(tmp_path, vault)
    profile = store.save(
        ProfileWrite(name="原配置", base_url="https://example.test/v1", api_key="old-secret")
    )

    def fail(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError):
        store.save(
            ProfileWrite(
                name="不应半更新", base_url="https://changed.test/v1", api_key="new-secret"
            ),
            profile["id"],
        )
    assert store.resolve(profile["id"])["api_key"] == "old-secret"
    assert store.resolve(profile["id"])["base_url"] == "https://example.test/v1"
    assert list(vault.values.values()) == ["old-secret"]


def test_history_parser_deduplicates_credentials_and_protocols_without_executing_commands():
    text = """$env:MY_API_KEY = 'same;secret'
uv run guild-manager run --base-url "https://example.test/v1" --model first --api-key $env:MY_API_KEY
uv run guild-manager run `
 --base-url https://example.test/v1 --model second --api-key 'same;secret'
uv run guild-manager run --base-url https://example.test/v1 --provider openai-responses --model third --api-key 'same;secret'
uv run guild-manager run --base-url https://example.test/v1 --model fourth --api-key another-secret
uv run guild-manager run --base-url $unknown --api-key 'not-imported'
uv run guild-manager run --base-url https://user:secret@example.test/v1 --model unsafe
uv run guild-manager run --base-url https://example.test/v1 --api-key "$(DO-NOT-EXECUTE)"
uv run guild-manager run --base-url http://localhost/:8000/v1 --model malformed
uv run guild-manager run --base-url https://example.test/v1 --model https://not-a-model.test
"""
    result = parse_history(text)
    assert len(result["candidates"]) == 3
    assert result["skipped_unresolved"] == 5
    original = result["candidates"][0]
    assert original["api_key"] == "same;secret"
    assert original["known_models"] == ["first", "second"]
    assert original["default_model"] == "second"
    assert result["candidates"][1]["provider"] == "openai-responses"
    assert result["candidates"][2]["api_key"] == "another-secret"


def test_history_import_is_idempotent_and_never_writes_literal_keys(tmp_path):
    store = ApiProfiles(tmp_path, MemoryVault())
    candidate = parse_history(
        "uv run guild-manager run --base-url https://example.test/v1 --api-key unique-history-secret --model m"
    )["candidates"]
    assert store.import_history(candidate)["created"] == 1
    assert store.import_history(candidate)["skipped_existing"] == 1
    assert len(store.list()) == 1
    assert store.list()[0]["known_models"] == ["m"]
    assert "unique-history-secret" not in store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "provider,path,expected",
    [
        ("openai", "/v1", ["discovered-model", "profile-model"]),
        ("openai-responses", "/v1", ["discovered-model", "profile-model"]),
        ("anthropic", "", ["claude-first", "claude-last"]),
        ("google", "/v1beta", ["gemini-chat", "gemini-second"]),
        ("google", "/gateway", ["gateway-gemini", "gateway-tool"]),
    ],
)
def test_provider_catalog_authentication_pagination_and_no_generation(provider, path, expected):
    with catalog_server() as (url, requests):
        result = fetch_models(
            {"provider": provider, "base_url": url + path, "api_key": "fake-secret"}
        )
        assert [model["id"] for model in result["models"]] == expected
        assert not result["truncated"]
        assert all(request["method"] == "GET" for request in requests)
        if provider == "anthropic" or path == "/v1beta":
            assert len(requests) == 2
            assert "%26" in requests[1]["path"]


def test_catalog_does_not_forward_credentials_on_redirect_or_echo_upstream_error():
    with catalog_server() as (url, requests):
        with pytest.raises(ModelCatalogError) as failure:
            fetch_models(
                {"provider": "openai", "base_url": url + "/error", "api_key": "fake-secret"}
            )
        assert "HTTP 401" in str(failure.value)
        assert "fake-secret" not in str(failure.value)
        with pytest.raises(ModelCatalogError, match="重定向"):
            fetch_models(
                {"provider": "openai", "base_url": url + "/redirect", "api_key": "fake-secret"}
            )
        assert len(requests) == 2
        assert all("stolen" not in request["path"] for request in requests)


def test_api_crud_catalog_and_origin_checks_do_not_return_secret(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.api_profiles = ApiProfiles(m.root, MemoryVault())
    app = FastAPI()
    app.include_router(workbench_router(m))
    with catalog_server() as (url, requests), TestClient(app) as client:
        body = {
            "name": "API 集成样例",
            "provider": "openai",
            "base_url": url + "/v1",
            "api_key": "fake-secret",
            "default_model": "profile-model",
        }
        assert (
            client.post(
                "/api/workbench/api-profiles", json=body, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        response = client.post("/api/workbench/api-profiles", json=body)
        assert response.status_code == 201
        assert "fake-secret" not in response.text
        profile = response.json()
        listing = client.get("/api/workbench/api-profiles")
        assert listing.json()["profiles"][0]["id"] == profile["id"]
        assert "fake-secret" not in listing.text
        catalog = client.post(f"/api/workbench/api-profiles/{profile['id']}/models")
        assert catalog.status_code == 200
        assert len(catalog.json()["models"]) == 2
        assert all(r["method"] == "GET" for r in requests)
        edited = {**body, "name": "改名", "api_key": None}
        assert (
            client.put(f"/api/workbench/api-profiles/{profile['id']}", json=edited).status_code
            == 200
        )
        assert m.api_profiles.resolve(profile["id"])["api_key"] == "fake-secret"
        assert client.delete(f"/api/workbench/api-profiles/{profile['id']}").status_code == 200
        assert client.post(f"/api/workbench/api-profiles/{profile['id']}/models").status_code == 404
        assert client.get("/api/workbench/api-profiles").json()["profiles"] == []


def test_key_editor_reads_updates_and_clears_saved_key_without_exposing_environment(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.api_profiles = ApiProfiles(m.root, MemoryVault())
    app = FastAPI()
    app.include_router(workbench_router(m))
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key-must-not-be-returned")
    with TestClient(app) as client:
        body = {"name": "可直接编辑 Key", "base_url": "https://example.test/v1", "api_key": "saved-editor-key"}
        profile = client.post("/api/workbench/api-profiles", json=body).json()
        route = f"/api/workbench/api-profiles/{profile['id']}"
        blocked = client.post(route + "/key", headers={"Origin": "https://other.example"})
        assert blocked.status_code == 403
        assert "saved-editor-key" not in blocked.text
        read = client.post(route + "/key", headers={"Origin": "http://testserver"})
        assert read.status_code == 200
        assert read.json() == {"api_key": "saved-editor-key"}
        assert read.headers["cache-control"] == "no-store"
        assert "saved-editor-key" not in client.get("/api/workbench/api-profiles").text
        changed = {**body, "api_key": "edited-editor-key"}
        response = client.put(route, json=changed)
        assert response.status_code == 200
        assert "edited-editor-key" not in response.text
        assert client.post(route + "/key").json() == {"api_key": "edited-editor-key"}
        cleared = {**body, "api_key": None, "clear_api_key": True}
        assert client.put(route, json=cleared).status_code == 200
        assert client.post(route + "/key").json() == {"api_key": None}
        assert m.api_profiles.vault.values == {}
        assert "environment-key-must-not-be-returned" not in client.post(route + "/key").text
        metadata = m.api_profiles.path.read_text(encoding="utf-8")
        assert all(key not in metadata for key in ("saved-editor-key", "edited-editor-key", "environment-key-must-not-be-returned"))


def test_key_editor_reports_deleted_profile_and_missing_key_without_mutating_config(tmp_path):
    m = manager(tmp_path)
    m.api_profiles = ApiProfiles(m.root, MemoryVault())
    profile = m.api_profiles.save(ProfileWrite(name="凭证已丢失", base_url="https://example.test/v1", api_key="missing-key"))
    m.api_profiles.vault.values.clear()
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        assert client.post("/api/workbench/api-profiles/not-found/key").status_code == 404
        response = client.post(f"/api/workbench/api-profiles/{profile['id']}/key")
        assert response.status_code == 503
        assert "missing-key" not in response.text
        assert m.api_profiles.list() == [profile]


def test_selected_profile_reaches_real_worker_without_key_in_job_metadata(tmp_path):
    with catalog_server() as (url, requests):

        async def scenario():
            m = manager(tmp_path)
            m.api_profiles = ApiProfiles(m.root, MemoryVault())
            await m.start()
            try:
                profile = m.api_profiles.save(
                    ProfileWrite(
                        name="保存过的连接",
                        provider="openai",
                        base_url=url + "/v1",
                        api_key="fake-secret",
                        default_model="profile-model",
                    )
                )
                job = m.create_batch(
                    CreateBatch(
                        name="引用配置执行",
                        runs=[RunSpec(preset="default", profile_id=profile["id"])],
                    )
                )["jobs"][0]
                await wait_for(lambda: job["status"] in {"completed", "failed"})
                assert job["status"] == "completed", job["error"]
                assert job["config"]["profile_id"] == profile["id"]
                assert job["config"]["api_profile_name"] == "保存过的连接"
                assert "fake-secret" not in json.dumps(m.snapshot())
                assert all(r["model"] == "profile-model" for r in requests)
                assert all(r["headers"]["Authorization"] == "Bearer fake-secret" for r in requests)
                assert "fake-secret" not in (m.root / job["id"] / "state.json").read_text(
                    encoding="utf-8"
                )
                assert "fake-secret" not in (m.root / job["id"] / "events.jsonl").read_text(
                    encoding="utf-8"
                )
            finally:
                await m.close()

        asyncio.run(scenario())
