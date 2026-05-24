from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from guild_manager_bench.api.routes_actions import actions_router
from guild_manager_bench.api.routes_sessions import sessions_router
from guild_manager_bench.api.store import SessionStore
from guild_manager_bench.api.websocket import SessionHub, websocket_router
from guild_manager_bench.game.loader import load_game_definition


def create_app(data_dir: str | Path = "data") -> FastAPI:
    """创建可视化和操作服务。"""

    definition = load_game_definition(data_dir)
    store = SessionStore(definition)
    hub = SessionHub()
    app = FastAPI(title="Guild Manager Bench")
    app.include_router(sessions_router(store))
    app.include_router(actions_router(store, hub))
    app.include_router(websocket_router(store, hub))

    static_dir = _static_dir()
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app


def _static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "static"


app = create_app()

