from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from guild_manager_bench.api.routes_actions import actions_router
from guild_manager_bench.api.routes_llm_archive import llm_archive_router
from guild_manager_bench.api.routes_llm_debug import llm_debug_router
from guild_manager_bench.api.routes_sessions import sessions_router
from guild_manager_bench.api.store import SessionStore
from guild_manager_bench.api.websocket import SessionHub, websocket_router
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import resolve_data_source


def create_app(data_dir: str | Path = "data", *, preset: str | None = None) -> FastAPI:
    """创建可视化和操作服务。"""

    data_preset = resolve_data_source(data_dir, preset)
    definition = load_game_definition(data_preset.data_dir)
    store = SessionStore(definition)
    hub = SessionHub()
    app = FastAPI(title="Guild Manager Bench")
    app.state.data_preset = data_preset
    app.include_router(sessions_router(store))
    app.include_router(actions_router(store, hub))
    app.include_router(websocket_router(store, hub))
    app.include_router(llm_archive_router())
    app.include_router(llm_debug_router(data_preset.data_dir, data_source=data_preset.to_dict()))

    assets_dir = _assets_dir()
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir, html=False), name="assets")

    replay_dir = _replay_dir()
    if replay_dir.exists():
        app.mount("/replay", StaticFiles(directory=replay_dir, html=True), name="replay")

    leaderboard_dir = _leaderboard_dir()
    if leaderboard_dir.exists():
        app.mount("/leaderboard", StaticFiles(directory=leaderboard_dir, html=True), name="leaderboard")

    static_dir = _static_dir()
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app


def _static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "static"


def _replay_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "replay"


def _leaderboard_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "leaderboard"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "assets"


app = create_app()
