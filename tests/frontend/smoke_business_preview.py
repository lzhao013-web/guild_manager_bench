"""复现等待模型、空编队的大面板：真实隔离工作进程与本地假模型，无付费调用。"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/frontend"))
from smoke_multiview import assert_space
from smoke_workbench import model_server, wait_until
from guild_manager_bench.api.app import create_app


def main():
    directory = ROOT / "runs" / f".smoke-business-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors = []
    try:
        wait_until(lambda: server.started)
        with model_server() as (url, release, calls), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = page.request.post(base + "/api/workbench/batches", data={
                "name": "等待场景验收（非真实评测）", "runs": [{"preset": "advanced", "model": "slow-business-preview", "base_url": url, "api_key": "fixture-only-key"}],
            })
            assert response.status == 201, response.text()
            job_id = response.json()["jobs"][0]["id"]
            wait_until(lambda: app.state.workbench.get(job_id)["phase"] == "waiting_model")
            watch = page.request.get(base + f"/api/workbench/jobs/{job_id}/watch").json()
            observation = next(record["event"]["observation"] for record in watch["records"] if record["event"]["type"] == "turn_started")
            assert not observation["adventurers"]
            assert observation["recruit_candidates"] and observation["crafting_recipes"] and observation["global_upgrades"]
            page.goto(base)
            page.get_by_role("checkbox", name="选择当前筛选的全部运行", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator('.monitor-previews[data-mode="tiles"]')).to_be_visible()
            for width, height in [(1920, 1080), (2493, 1548), (1280, 720)]:
                page.set_viewport_size({"width": width, "height": height})
                expect(page.get_by_role("region", name="招募候选预览", exact=True)).to_be_visible()
                expect(page.get_by_role("region", name="天赋与升级预览", exact=True)).to_be_visible()
                expect(page.get_by_role("region", name="装备与制作预览", exact=True)).to_be_visible()
                assert_space(page, 1)
                model = page.locator(".monitor-model").bounding_box()
                business = page.locator(".monitor-preview-grid").bounding_box()
                assert model["height"] <= 52, model
                assert business["height"] > model["height"] * 3, {"model": model, "business": business}
                page.screenshot(path=str(directory / f"waiting-{width}x{height}.png"), full_page=True)
            candidate = next((item for item in observation["recruit_candidates"] if item["can_recruit"]), observation["recruit_candidates"][0])
            page.get_by_role("button", name=f"查看招募候选 {candidate['name']}", exact=True).click()
            expect(page.get_by_role("region", name=f"{candidate['name']} 的详情", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回多路观战", exact=True).click()
            recipe = max(observation["crafting_recipes"], key=lambda item: (item["can_craft"], item["gold_cost"]))
            equipment = page.get_by_role("region", name="装备与制作预览", exact=True)
            expect(equipment.get_by_role("button", name="查看制作配方", exact=False).first).to_have_attribute("aria-label", f"查看制作配方 {recipe['output_name']}")
            page.get_by_role("button", name=f"查看制作配方 {recipe['output_name']}", exact=True).click()
            expect(page.get_by_role("button", name="制作配方", exact=True)).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_role("region", name=f"{recipe['name']} 的详情", exact=True)).to_be_visible()
            expect(page.get_by_text("只读查看所选快照，不执行制作。", exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "recipe-details.png"), full_page=True)
            page.get_by_role("button", name="返回多路观战", exact=True).click()
            page.get_by_role("button", name="深色", exact=True).click()
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.screenshot(path=str(directory / "waiting-dark.png"), full_page=True)
            assert len(calls) == 1, "查看预览不应触发新的模型请求"
            page.request.post(base + f"/api/workbench/jobs/{job_id}/stop")
            release.set()
            wait_until(lambda: app.state.workbench.get(job_id)["status"] == "interrupted")
            browser.close()
        assert not errors, errors
        print(f"经营预览验收通过：真实等待与空编队、招募和天赋、制作费用与条件、短模型入口、单局详情与返回、明暗主题、不额外请求模型。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
