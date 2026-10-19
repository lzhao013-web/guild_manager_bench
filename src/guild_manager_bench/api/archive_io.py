"""回放读取与解析分离，解析和响应发送前先关闭文件句柄。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msgspec


def read_archive_response_bytes(path: Path) -> bytes:
    """只移除格式空白，避免原样传输缩进导致响应体积增大。"""
    return msgspec.json.format(path.read_bytes(), indent=-1)


def read_archive_json(path: Path) -> Any:
    raw = path.read_bytes()
    try:
        return msgspec.json.decode(raw)
    except msgspec.DecodeError:
        # 旧写入器使用标准库，允许 NaN 和孤立代理字符。也保留 JSONDecodeError 契约。
        return json.loads(raw.decode("utf-8"))
