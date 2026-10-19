"""可复用的 API 连接。元数据保存在工作台目录，密钥保存在系统凭证库。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from guild_manager_bench.api.routes_llm_debug import _debug_agent
from guild_manager_bench.api.workbench_lock import FileLease
from guild_manager_bench.api.workbench_models import RunSpec

Provider = Literal["openai", "openai-responses", "anthropic", "google"]


class ProfileConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider = "openai"
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value):
        return RunSpec.safe_url(value)


class ProfileWrite(ProfileConnection):
    name: str = Field(min_length=1, max_length=120)
    default_model: str | None = Field(default=None, max_length=200)
    clear_api_key: bool = False

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入配置名称")
        return value.strip()

    @model_validator(mode="after")
    def key_action(self):
        if self.clear_api_key and self.api_key and self.api_key.get_secret_value():
            raise ValueError("不能同时设置和清除密钥")
        return self


class CredentialError(RuntimeError):
    pass


class Vault(Protocol):
    def get(self, reference: str) -> str: ...
    def set(self, reference: str, secret: str) -> None: ...
    def delete(self, reference: str) -> None: ...


class SystemVault:
    service = "guild-manager-bench/api-profiles"

    def get(self, reference: str) -> str:
        import keyring
        from keyring.errors import KeyringError

        try:
            secret = keyring.get_password(self.service, reference)
        except KeyringError as exc:
            raise CredentialError("无法访问系统凭证库，请使用保存配置时的系统用户") from exc
        if not secret:
            raise CredentialError("系统凭证库中的密钥不存在或为空，请重新填写并保存")
        return secret

    def set(self, reference: str, secret: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            keyring.set_password(self.service, reference, secret)
        except KeyringError as exc:
            raise CredentialError(
                "无法保存到系统凭证库，请配置可用的 keyring 后端。不会退回明文保存"
            ) from exc

    def delete(self, reference: str) -> None:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError

        try:
            try:
                keyring.delete_password(self.service, reference)
            except PasswordDeleteError:
                # 用户可能已经在系统凭证管理器中删除了旧凭证。
                # 仅在确认不存在时视为清理完成，其它失败仍然报错。
                if keyring.get_password(self.service, reference) is not None:
                    raise
        except KeyringError as exc:
            raise CredentialError("无法删除系统凭证，请检查系统凭证库") from exc


def resolve_connection(
    provider: Provider, base_url: str | None, api_key: str | None
) -> dict[str, Any]:
    # 列举模型不需要模型 ID。只复用适配器的环境解析，不发起推理请求。
    agent = _debug_agent(
        {"provider": provider, "base_url": base_url, "api_key": api_key, "model": "model-catalog"}
    )
    return {
        "provider": provider,
        "base_url": RunSpec.safe_url(agent.config.base_url),
        "api_key": agent.config.api_key,
    }


def _origin(url: str) -> tuple:
    parsed = urlsplit(url)
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80),
    )


def fingerprint(provider: str, base_url: str, secret: str | None) -> str:
    value = json.dumps([provider, base_url, secret], ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


class ApiProfiles:
    def __init__(self, root: Path, vault: Vault | None = None) -> None:
        self.root = root
        self.path = root / "api_profiles.json"
        self.vault = vault if vault is not None else SystemVault()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))["profiles"]

    def _write(self, profiles: list[dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "profiles": profiles}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def public(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            key: profile[key]
            for key in (
                "id",
                "name",
                "provider",
                "base_url",
                "default_model",
                "known_models",
                "source",
                "created_at",
                "updated_at",
            )
        } | {"credential_present": profile["credential_id"] is not None}

    def list(self) -> list[dict[str, Any]]:
        return [self.public(p) for p in self._read()]

    def _get(self, profile_id: str) -> dict[str, Any]:
        return next(p for p in self._read() if p["id"] == profile_id)

    def resolve(self, profile_id: str) -> dict[str, Any]:
        try:
            profile = self._get(profile_id)
        except StopIteration as exc:
            raise KeyError(profile_id) from exc
        secret = self.vault.get(profile["credential_id"]) if profile["credential_id"] else None
        return {
            "profile_id": profile["id"],
            "api_profile_name": profile["name"],
            "provider": profile["provider"],
            "base_url": profile["base_url"],
            "model": profile["default_model"],
            "api_key": secret,
        }

    def save(self, request: ProfileWrite, profile_id: str | None = None) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLease(self.root / "api_profiles.lock"):
            return self._save_locked(request, profile_id)

    def _save_locked(
        self,
        request: ProfileWrite,
        profile_id: str | None = None,
        *,
        source: str = "manual",
        known_models: list[str] | None = None,
    ) -> dict[str, Any]:
        profiles = self._read()
        old = None
        if profile_id:
            old = next((p for p in profiles if p["id"] == profile_id), None)
            if old is None:
                raise KeyError(profile_id)
        secret = request.api_key.get_secret_value() if request.api_key else None
        if not secret and old and old["credential_id"] and not request.clear_api_key:
            secret = self.vault.get(old["credential_id"])
        connection = resolve_connection(request.provider, request.base_url, secret)
        if (
            old
            and old["credential_id"]
            and not request.api_key
            and not request.clear_api_key
            and _origin(old["base_url"]) != _origin(connection["base_url"])
        ):
            raise ValueError(
                "更换服务的主机、协议或端口时，请重新输入密钥，或明确清除旧密钥。不会将已保存的密钥转发给新服务"
            )
        # 无显式密钥的配置保持“使用环境配置”，不复制当前环境密钥。
        reference = (
            old["credential_id"]
            if old and secret and not request.api_key and not request.clear_api_key
            else None
        )
        new_reference = None
        if secret and reference is None:
            new_reference = reference = uuid4().hex
            self.vault.set(reference, secret)
        now = datetime.now(timezone.utc).isoformat()
        profile = {
            "id": profile_id or uuid4().hex,
            "name": request.name,
            "provider": request.provider,
            "base_url": connection["base_url"],
            "default_model": request.default_model or None,
            "credential_id": reference,
            "fingerprint": fingerprint(request.provider, connection["base_url"], secret),
            "known_models": list(
                dict.fromkeys(
                    known_models
                    if known_models is not None
                    else (old["known_models"] if old else [])
                )
            ),
            "source": old["source"] if old else source,
            "created_at": old["created_at"] if old else now,
            "updated_at": now,
        }
        profiles = (
            [profile if p["id"] == profile_id else p for p in profiles]
            if old
            else [*profiles, profile]
        )
        try:
            self._write(profiles)
        except OSError:
            if new_reference:
                self.vault.delete(new_reference)
            raise
        if old and old["credential_id"] and old["credential_id"] != reference:
            self.vault.delete(old["credential_id"])
        return self.public(profile)

    def delete(self, profile_id: str) -> None:
        with FileLease(self.root / "api_profiles.lock"):
            profiles = self._read()
            profile = next((p for p in profiles if p["id"] == profile_id), None)
            if profile is None:
                raise KeyError(profile_id)
            self._write([p for p in profiles if p["id"] != profile_id])
            if profile["credential_id"]:
                self.vault.delete(profile["credential_id"])

    def import_history(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        # 从查重到写入持有同一把锁，CLI 导入和网页编辑不能产生重复配置。
        with FileLease(self.root / "api_profiles.lock"):
            existing = {p["fingerprint"] for p in self._read()}
            created = []
            for candidate in candidates:
                identity = fingerprint(
                    candidate["provider"], candidate["base_url"], candidate["api_key"]
                )
                if identity in existing:
                    continue
                body = ProfileWrite(
                    name=candidate["name"],
                    provider=candidate["provider"],
                    base_url=candidate["base_url"],
                    api_key=candidate["api_key"],
                    default_model=candidate["default_model"],
                )
                created.append(
                    self._save_locked(
                        body, source="command_history", known_models=candidate["known_models"]
                    )
                )
                existing.add(identity)
            return {
                "created": len(created),
                "skipped_existing": len(candidates) - len(created),
                "profiles": created,
            }
