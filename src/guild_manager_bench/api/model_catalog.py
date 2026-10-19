"""只读模型目录，不发起聊天、推理或计费生成请求。"""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from guild_manager_bench.api.api_profiles import ProfileConnection, resolve_connection

MAX_BYTES = 4 * 1024 * 1024
MAX_PAGES = 20


class ModelCatalogError(RuntimeError):
    pass


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib 的默认重定向可能把认证头带往其他站点。
        return None


def fetch_models(connection: dict[str, Any]) -> dict[str, Any]:
    provider, base, secret = (
        connection["provider"],
        connection["base_url"].rstrip("/"),
        connection["api_key"],
    )
    path = "/v1/models" if provider == "anthropic" else "/models"
    headers = {"Accept": "application/json", "User-Agent": "guild-manager-bench/1.0"}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if secret:
            headers["x-api-key"] = secret
    elif provider == "google":
        if secret:
            headers["x-goog-api-key"] = secret
    elif secret:
        headers["Authorization"] = f"Bearer {secret}"
    opener = build_opener(NoCredentialRedirect())
    models: dict[str, dict[str, str]] = {}
    cursor = None
    cursors = set()
    for _ in range(MAX_PAGES):
        params = {}
        if provider == "google":
            params = {"pageSize": 1000}
            if cursor:
                params["pageToken"] = cursor
        elif provider == "anthropic":
            params = {"limit": 100}
            if cursor:
                params["after_id"] = cursor
        url = base + path + ("?" + urlencode(params) if params else "")
        try:
            with opener.open(Request(url, headers=headers), timeout=15) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ModelCatalogError("模型列表响应过大，请确认服务地址")
            data = json.loads(raw)
        except HTTPError as exc:
            exc.close()
            if 300 <= exc.code < 400:
                raise ModelCatalogError(
                    "模型列表接口发生重定向。为避免转发凭证，请直接配置最终服务地址"
                ) from None
            hint = (
                "请检查密钥和访问权限"
                if exc.code in {401, 403}
                else "该服务可能不支持列举模型，可手动填写模型 ID"
            )
            raise ModelCatalogError(f"获取模型失败（HTTP {exc.code}），{hint}") from None
        except (URLError, TimeoutError, socket.timeout, ConnectionError):
            raise ModelCatalogError("无法连接模型列表接口或请求超时，请检查地址与网络") from None
        except (ValueError, UnicodeError):
            raise ModelCatalogError("接口没有返回有效的模型列表 JSON，可手动填写模型 ID") from None
        if not isinstance(data, dict):
            raise ModelCatalogError("模型列表格式不正确")
        # OpenCode Go 的 Google 原生入口实测返回 OpenAI 格式目录。
        # 只识别明确的两种列表结构，不更换协议、认证头或服务地址重试。
        native_google = provider == "google" and isinstance(data.get("models"), list)
        items = data.get("models") if native_google else data.get("data")
        if not isinstance(items, list):
            raise ModelCatalogError("响应缺少模型列表，该服务可能不支持此接口。仍可手动填写模型 ID")
        for item in items:
            if not isinstance(item, dict):
                continue
            if provider == "google":
                methods = item.get("supportedGenerationMethods")
                if isinstance(methods, list) and "generateContent" not in methods:
                    continue
            identifier = item.get("name" if native_google else "id")
            if not isinstance(identifier, str) or not identifier:
                continue
            identifier = identifier.removeprefix("models/") if provider == "google" else identifier
            display = item.get("displayName") or item.get("display_name") or identifier
            models[identifier] = {"id": identifier, "name": str(display)}
        if provider == "google":
            cursor = data.get("nextPageToken")
        elif provider == "anthropic" and data.get("has_more"):
            cursor = data.get("last_id")
            if not cursor:
                raise ModelCatalogError("模型目录分页缺少 last_id")
        else:
            cursor = None
        if cursor is None or cursor == "":
            return {"models": sorted(models.values(), key=lambda m: m["id"]), "truncated": False}
        if not isinstance(cursor, str) or cursor in cursors:
            raise ModelCatalogError("模型目录返回了无效或重复的分页标识")
        cursors.add(cursor)
    return {"models": sorted(models.values(), key=lambda m: m["id"]), "truncated": True}


def fetch_temporary_models(body: ProfileConnection) -> dict[str, Any]:
    secret = body.api_key.get_secret_value() if body.api_key else None
    return fetch_models(resolve_connection(body.provider, body.base_url, secret))
