from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from guild_manager_bench.api.routes_actions import actions_router
from guild_manager_bench.api.routes_llm_archive import llm_archive_router
from guild_manager_bench.api.routes_llm_debug import llm_debug_router
from guild_manager_bench.api.routes_sessions import sessions_router
from guild_manager_bench.api.store import SessionStore
from guild_manager_bench.api.static_files import WorkbenchFiles
from guild_manager_bench.api.websocket import SessionHub, websocket_router
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import list_data_presets, resolve_data_source
from guild_manager_bench.api.workbench import Workbench
from guild_manager_bench.api.routes_workbench import workbench_router


def create_app(data_dir: str | Path = "data", *, preset: str | None = None) -> FastAPI:
    """创建可视化和操作服务。"""

    data_preset = resolve_data_source(data_dir, preset)
    definition = load_game_definition(data_preset.data_dir)
    store = SessionStore(definition)
    hub = SessionHub()
    source_path = Path(data_dir)
    data_root = source_path.parent.parent if source_path.parent.name == "presets" else source_path
    presets = {data_preset.name or "current": data_preset}
    presets.update({item.name: item for item in list_data_presets(data_root)})
    manager = Workbench(Path("runs/.workbench"), Path("runs/llm"), presets)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(title="Guild Manager Bench", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    app.state.workbench = manager
    app.include_router(workbench_router(manager))
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
    workbench_dir = Path(__file__).resolve().parents[3] / "web" / "workbench" / "dist"
    if workbench_dir.exists():
        app.mount("/workbench", WorkbenchFiles(directory=workbench_dir, html=True), name="workbench")

    @app.get("/", include_in_schema=False)
    async def home(request: Request):
        # 保留旧观战和人工操作 URL，避免已有链接失效。
        if request.query_params.get("mode") == "manual" or request.query_params.get("watch") == "1":
            return FileResponse(static_dir / "index.html")
        index = workbench_dir / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>工作台尚未构建</h1><p>在项目根目录执行 <code>npm install &amp;&amp; npm run build</code>，然后重启服务。</p><a href='/legacy/'>打开旧版调试页</a>", status_code=503)

    if static_dir.exists():
        app.mount("/legacy", StaticFiles(directory=static_dir, html=True), name="legacy")
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
