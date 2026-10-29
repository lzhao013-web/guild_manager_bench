from __future__ import annotations

import re
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


class WorkbenchFiles(StaticFiles):
    """仅对 Vite 带内容哈希的 JS/CSS 使用长期缓存。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            hashed = re.fullmatch(r"assets/[^/]+-[A-Za-z0-9_-]{8}\.(?:js|css)", Path(path).as_posix())
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if hashed else "no-cache"
            )
        return response
