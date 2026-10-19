"""会话标识与网关路由请求头。

OpenCode Go 这类网关要求客户端为每个会话发送稳定的 ``x-opencode-session`` 头，
用于请求路由和 prompt cache 命中；缺失时请求会被直接拒绝：

    HTTP 400: {"error":{"type":"MissingSessionID", ...}}

参见 https://opencode.ai/docs/go/#where-can-i-use-it 。

本模块只提供纯函数，供各 provider 适配器复用，避免适配器之间循环导入。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

OPENCODE_SESSION_HEADER = "x-opencode-session"

# 跨 provider 共用的环境变量名；provider 专用变量优先级更高。
SHARED_SESSION_ENV = "OPENCODE_SESSION_ID"


@dataclass(frozen=True, slots=True)
class ResolvedSessionId:
    """解析后的会话标识；``explicit`` 表示由用户指定而非自动生成。"""

    value: str
    explicit: bool


def new_session_id() -> str:
    """生成一个新的会话标识。"""

    return uuid.uuid4().hex


def resolve_session_id(
    explicit_value: str | None,
    dotenv_values: Mapping[str, str],
    *provider_env_names: str,
) -> ResolvedSessionId:
    """按 显式参数 > 进程环境变量 > dotenv > 自动生成 解析会话标识。

    自动生成的值在适配器实例生命周期内保持不变，因此一次 run 的所有请求共享同一个
    会话标识；``explicit`` 为 True 时说明用户已指定，跑局不应再用游戏会话 id 覆盖。
    """

    names = (*provider_env_names, SHARED_SESSION_ENV)
    candidates = (
        _clean(explicit_value),
        *(_clean(os.environ.get(name)) for name in names),
        *(_clean(dotenv_values.get(name)) for name in names),
    )
    for value in candidates:
        if value:
            return ResolvedSessionId(value=value, explicit=True)
    return ResolvedSessionId(value=new_session_id(), explicit=False)


def session_headers(session_id: str | None) -> dict[str, str]:
    """返回会话路由请求头；未解析到会话标识时不发送。"""

    value = _clean(session_id)
    return {OPENCODE_SESSION_HEADER: value} if value else {}


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
