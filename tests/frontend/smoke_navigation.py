"""用真实的 JS/CSS 分包 404 验证页面隔离和手动刷新，不启动游戏或请求模型。"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/frontend"))
from smoke_workbench import wait_until


def main():
    output = ROOT / "runs" / f".smoke-navigation-{uuid4().hex[:8]}"
    output.mkdir(parents=True)
    dist = ROOT / "web/workbench/dist"
    app = FastAPI()

    @app.get("/api/workbench")
    async def snapshot():
        return {"jobs": [], "settings": {"max_parallel": 2, "per_endpoint": 2},
                "presets": ["default"], "scheduler_error": None}

    @app.get("/api/workbench/api-profiles")
    async def profiles():
        return {"profiles": []}

    @app.get("/api/llm/runs")
    async def archives():
        return {"runs": []}

    @app.get("/")
    async def home():
        return FileResponse(dist / "index.html")

    app.mount("/workbench", StaticFiles(directory=dist, html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors, requests, results = [], [], []
    try:
        wait_until(lambda: server.started)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            for prefix, extension, label, selector, width in (
                ("CurveBoard", "js", "曲线对比", ".curve-board", 1280),
                ("CurveBoard", "css", "曲线对比", ".curve-board", 390),
                ("RunHistory", "js", "历史存档", ".archives-section", 1280),
            ):
                context = browser.new_context(viewport={"width": width, "height": 844})
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append((request.method, request.url)))
                pattern = f"**/workbench/assets/{prefix}-*.{extension}"
                failed = []

                def missing(route):
                    failed.append(route.request.url)
                    route.fulfill(status=404, content_type="text/plain", body="fixture missing build asset")

                page.route(pattern, missing)
                page.goto(base + "/?curves=archive%3Afixture")
                navigation = page.get_by_role("navigation", name="主导航", exact=True)
                navigation.get_by_role("button", name=label, exact=True).click()
                notice = page.get_by_role("alert", name=label + "加载失败", exact=True)
                expect(notice).to_be_visible()
                expect(navigation).to_be_visible()
                expect(notice.get_by_role("button", name="刷新页面", exact=True)).to_be_visible()
                assert failed, f"没有触发真实分包 404：{pattern}"
                navigation.get_by_role("button", name="API 配置", exact=True).click()
                expect(notice).not_to_be_visible()
                navigation.get_by_role("button", name=label, exact=True).click()
                expect(notice).to_be_visible()
                before = page.url
                page.screenshot(path=str(output / f"{prefix}-{extension}-{width}.png"))
                page.unroute(pattern)
                notice.get_by_role("button", name="刷新页面", exact=True).click()
                expect(page.locator(selector)).to_be_visible()
                assert page.url == before, "恢复时丢失当前页面地址或曲线选择"
                expect(page.get_by_role("alert", name=label + "加载失败", exact=True)).not_to_be_visible()
                results.append({"module": prefix, "extension": extension, "width": width,
                                "failed_urls": failed, "recovered_url": page.url})
                context.close()
            browser.close()
        assert not errors, errors
        assert all(method in ("GET", "HEAD") for method, _ in requests), requests
        (output / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"导航故障验收通过：JS/CSS 分包 404、导航保留、原地址刷新恢复。产物：{output}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


if __name__ == "__main__":
    main()
