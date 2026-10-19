"""统一曲线工作区验收。所有曲线都是构造数据，不请求模型或重算评分。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests/frontend"))
from test_workbench_curves import completed_turn, replay
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec


def fixture_curve(scale=1, *, gap=False, different=False):
    payload = replay(*(completed_turn(turn, score=0 if turn == 1 else turn * 100 * scale,
                                     gold=100 - turn * 3, xp=turn * 7,
                                     rewards=((turn * 10 * scale, turn * 20 * scale),))
                       for turn in range(1, 7) if not (gap and turn == 3)))
    payload["final_observation"]["max_turns"] = 6
    payload.update(status="completed", created_at=timestamp(), updated_at=timestamp(),
                   agent={"type": "FixtureAgent", "config": {"model": f"曲线构造模型 {scale}", "api_key": "must-not-reach-curves"}},
                   data={"preset": "default", "data_hash": "fixture-data", "benchmark_version": "fixture-only",
                         "protocol_hash": "different-fixture" if different else "fixture-rules", "settings_hash": "fixture-settings"},
                   config={"game_seed": 7, "scoring_seed": 11})
    return payload


def main():
    directory = ROOT / "runs" / f".smoke-curves-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    manager = app.state.workbench
    manager.root.mkdir(parents=True, exist_ok=True)
    jobs = manager.create_batch(CreateBatch(name="曲线构造验收（非真实评测）", runs=[
        RunSpec(name=f"曲线构造模型 {i}", preset="default", provider="openai", model=f"fixture-curves-{i}", base_url="http://127.0.0.1:1/v1", api_key="fixture-only") for i in (1, 2)
    ]))["jobs"]
    archive_names = ["fixture-curve-a", "fixture-curve-b", "fixture,curve-cli", "fixture-curve-four"]
    before = {}
    for index, archive in enumerate(archive_names):
        folder = manager.archive_root / archive
        folder.mkdir(parents=True)
        path = folder / "replay.json"
        path.write_text(json.dumps(fixture_curve(index + 1, gap=index == 1, different=index == 2), ensure_ascii=False), encoding="utf-8")
        before[path] = path.read_bytes()
        if index < len(jobs):
            jobs[index].update(status="completed", phase="idle", archive_id=archive, turn=6, max_turns=6, ended_at=timestamp())
            manager._save(jobs[index])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors, requests = [], []
    try:
        wait_until(lambda: server.started)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.goto(base + "/?section=archives")
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="对比选中曲线", exact=True).click()
            values = page.get_by_role("region", name="同回合数值与差值", exact=True)
            expect(values).to_be_visible()
            expect(page.locator(".curve-legend > div")).to_have_count(4)
            expect(page.get_by_label("添加曲线 run", exact=True)).to_be_disabled()
            expect(page.get_by_role("status").filter(has_text="所选 run 的规则")).to_be_visible()
            for metric in ("经济", "经验", "战力"):
                expect(page.get_by_role("region", name=metric + "曲线", exact=True)).to_be_visible()
            page.get_by_label("定位对比回合", exact=True).fill("3")
            row_b = values.get_by_role("row").filter(has_text="曲线构造模型 2")
            expect(row_b.get_by_text("未记录", exact=True)).to_have_count(3)
            page.get_by_label("定位对比回合", exact=True).fill("1")
            row_a = values.get_by_role("row").filter(has_text="曲线构造模型 1")
            expect(row_a.get_by_role("cell").last.get_by_text("0", exact=True)).to_be_visible()
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "curves-income-dark.png"), full_page=True)
            page.get_by_role("button", name="结算后余额", exact=True).click()
            expect(values.get_by_role("columnheader", name="结算后未分配经验池", exact=True)).to_be_visible()
            page.get_by_label("曲线起始回合", exact=True).fill("2")
            page.get_by_label("曲线结束回合", exact=True).fill("5")
            page.get_by_label("定位对比回合", exact=True).fill("4")
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "curves-dark.png"), full_page=True)
            for width, height in [(1280, 720), (1920, 1080), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(directory / f"curves-{width}.png"), full_page=True)
                if width == 1280:
                    expect(page.locator(".curve-values tbody tr").last).to_be_in_viewport(ratio=1)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("button", name="浅色", exact=True).click()
            page.get_by_role("button", name="全程 / 跟随最新", exact=True).click()
            page.screenshot(path=str(directory / "curves-light.png"), full_page=True)
            page.reload()
            expect(page.locator(".curve-legend > div")).to_have_count(4)
            expect(values).to_be_visible()
            # 单局入口和原工作区返回。无事件的构造任务仍可读取已有权威回放曲线。
            page.goto(base + f"/?section=archives&view=focus&run={jobs[0]['id']}")
            page.get_by_role("button", name="查看运行曲线", exact=True).click()
            expect(page.locator(".curve-legend > div")).to_have_count(1)
            page.get_by_role("button", name="返回原工作区", exact=True).click()
            expect(page.get_by_role("button", name="查看运行曲线", exact=True)).to_be_visible()
            # 多路使用统一入口，不再挂载经营进展的小曲线。
            page.get_by_role("button", name="历史存档", exact=True).click()
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator(".watch-monitor")).to_have_count(2)
            expect(page.locator(".business-score-trend")).to_have_count(0)
            page.get_by_role("button", name="对比当前观战曲线", exact=True).click()
            expect(page.locator(".curve-legend > div")).to_have_count(2)
            expect(values).to_be_visible()
            browser.close()
        assert all(path.read_bytes() == original for path, original in before.items()), "读取曲线不能修复、重算或回写存档"
        assert not any(method != "GET" for method, _ in requests), requests
        assert not any("/rescore" in url or "/rebuild" in url or url.endswith("/replay") for _, url in requests), requests
        assert not errors, errors
        print(f"曲线验收通过：统一入口、CLI 与受控 run、三指标与口径切换、缺回合断线、零分、条件警告、范围与联动、刷新恢复、单局与多路往返、明暗和手机。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
