
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.api_profiles import (
    ApiProfiles,
    CredentialError,
    ProfileWrite,
    SystemVault,
)
from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api import api_history
from test_api_profiles import MemoryVault, catalog_server
from test_workbench import manager


def test_stored_key_cannot_be_forwarded_to_new_origin_without_reentering_it(tmp_path):
    store = ApiProfiles(tmp_path, MemoryVault())
    profile = store.save(
        ProfileWrite(
            name="原 API", base_url="https://example.test/v1", api_key="secret-remains-in-vault"
        )
    )
    for address in [
        "https://other.test/v1",
        "http://example.test/v1",
        "https://example.test:8443/v1",
    ]:
        with pytest.raises(ValueError, match="重新输入密钥"):
            store.save(ProfileWrite(name="不可静默转发", base_url=address), profile["id"])
        assert store.resolve(profile["id"])["base_url"] == "https://example.test/v1"
    # 同一服务的路由迁移不需要重新输入密钥。
    store.save(
        ProfileWrite(name="同源路由迁移", base_url="https://example.test:443/inference/v1"),
        profile["id"],
    )
    assert store.resolve(profile["id"])["api_key"] == "secret-remains-in-vault"
    store.save(
        ProfileWrite(
            name="显式提供新密钥", base_url="https://other.test/v1", api_key="new-explicit-key"
        ),
        profile["id"],
    )
    assert store.resolve(profile["id"])["api_key"] == "new-explicit-key"


def test_history_import_api_only_reads_fixture_and_does_not_contact_service(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.api_profiles = ApiProfiles(m.root, MemoryVault())
    app = FastAPI()
    app.include_router(workbench_router(m))
    with catalog_server() as (url, requests):
        history = tmp_path / "ConsoleHost_history.txt"
        history.write_text(
            f"uv run guild-manager run --base-url {url}/v1 --api-key history-import-secret --model history-model\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(api_history, "history_paths", lambda: [history])
        with TestClient(app) as client:
            blocked = client.post(
                "/api/workbench/api-profiles/import-history",
                headers={"Origin": "https://other.example"},
            )
            assert blocked.status_code == 403
            assert m.api_profiles.list() == []
            response = client.post("/api/workbench/api-profiles/import-history")
            assert response.status_code == 200
            assert response.json()["created"] == 1
            assert response.json()["source_count"] == 1
            assert "history-import-secret" not in response.text
            assert requests == []
            assert (
                client.post("/api/workbench/api-profiles/import-history").json()["skipped_existing"]
                == 1
            )
            assert len(m.api_profiles.list()) == 1


def test_cli_import_and_web_edits_share_one_mutation_lock(tmp_path):
    from guild_manager_bench.api.workbench_lock import FileLease
    from guild_manager_bench.api.api_history import parse_history

    store = ApiProfiles(tmp_path, MemoryVault())
    profile = store.save(
        ProfileWrite(name="原配置", base_url="https://example.test/v1", api_key="mutex-secret")
    )
    candidates = parse_history(
        "uv run guild-manager run --base-url https://example.test/v1 --api-key mutex-secret --model m"
    )["candidates"]
    with FileLease(tmp_path / "api_profiles.lock"):
        with pytest.raises(OSError):
            store.import_history(candidates)
    assert store.import_history(candidates)["skipped_existing"] == 1
    assert len(store.list()) == 1
    assert store.resolve(profile["id"])["api_key"] == "mutex-secret"


@pytest.mark.parametrize("secret", [None, ""])
def test_system_vault_missing_or_empty_password_is_not_an_environment_fallback(monkeypatch, secret):
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *args: secret)
    with pytest.raises(CredentialError, match="不存在或为空"):
        SystemVault().get("dummy-reference")


def test_system_vault_delete_is_idempotent_only_when_credential_is_confirmed_absent(monkeypatch):
    import keyring
    from keyring.errors import PasswordDeleteError

    def missing(*args):
        raise PasswordDeleteError("test delete failure")

    monkeypatch.setattr(keyring, "delete_password", missing)
    monkeypatch.setattr(keyring, "get_password", lambda *args: None)
    SystemVault().delete("dummy-reference")
    monkeypatch.setattr(keyring, "get_password", lambda *args: "still-present-secret")
    with pytest.raises(CredentialError, match="无法删除"):
        SystemVault().delete("dummy-reference")
