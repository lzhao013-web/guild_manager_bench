"""历史 preset 筛选与排序验收。只读构造存档，不调用模型。"""
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
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec


def main():
    directory = ROOT / "runs" / f".smoke-history-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    manager = app.state.workbench
    manager.root.mkdir(parents=True, exist_ok=True)
    jobs = manager.create_batch(CreateBatch(name="历史筛选构造验收（非真实评测）", runs=[
        RunSpec(name=name, preset=preset, provider="openai", model="fixture-history", base_url="http://127.0.0.1:1/v1", api_key="fixture-only")
        for name, preset in [("A full managed", "full"), ("B advanced managed", "advanced"), ("C failed managed", "default")]
    ]))["jobs"]
    before = {}

    def archive(name, model, preset, created, score, turns):
        path = manager.archive_root / name / "replay.json"
        path.parent.mkdir(parents=True)
        payload = replay(*(completed_turn(turn, score=score) for turn in range(1, turns + 1)))
        payload.update(status="completed", created_at=created, data={"preset": preset}, score={"rank_score": score}, agent={"config": {"model": model}})
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before[path] = path.read_bytes()

    for index, job in enumerate(jobs):
        failed = index == 2
        archive_id = None if failed else f"fixture-managed-{index}"
        score = None if failed else (300 if index == 0 else 0)
        turns = None if failed else (35 if index == 0 else 6)
        created = f"2026-10-{18 + index:02d}T10:00:00Z"
        job.update(status="failed" if failed else "completed", phase="idle", archive_id=archive_id,
                   created_at=created, ended_at=created, turn=turns, max_turns=35, last_score=score,
                   error="构造启动失败" if failed else None)
        manager._save(job)
        if archive_id:
            archive(archive_id, job["name"], job["config"]["preset"], created, score, turns)
    archive("fixture-cli-advanced", "D CLI advanced", "advanced", "2026-10-21T10:00:00Z", 100, 2)
    archive("fixture-cli-full", "E CLI full", "full", "2026-10-17T10:00:00Z", 200, 4)
    archive("fixture-cli-custom", "F CLI custom", "custom-history-fixture", "2026-10-16T10:00:00Z", 50, 1)
    archive("fixture-cli-unknown", "G CLI old", None, None, None, 0)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    errors, requests = [], []
    thread.start()
    try:
        wait_until(lambda: server.started)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.goto(f"http://127.0.0.1:{sock.getsockname()[1]}/?section=archives")
            expect(page.get_by_text("显示 7 / 7 个历史 run", exact=True)).to_be_visible()
            names = page.locator(".archive-table tbody tr td:nth-child(2) strong")
            expect(names).to_have_text(["D CLI advanced", "C failed managed", "B advanced managed", "A full managed", "E CLI full", "F CLI custom", "G CLI old"])
            preset = page.get_by_role("combobox", name="筛选历史 preset", exact=True)
            expect(page.get_by_role("combobox", name="历史排序", exact=True)).to_have_count(0)
            created = page.get_by_role("columnheader", name="创建时间", exact=True)
            expect(created).to_have_attribute("aria-sort", "descending")
            page.get_by_role("button", name="创建时间", exact=True).click()
            expect(names.first).to_have_text("F CLI custom")
            expect(names.last).to_have_text("G CLI old")
            expect(created).to_have_attribute("aria-sort", "ascending")
            page.get_by_role("button", name="创建时间", exact=True).click()
            score = page.get_by_role("button", name="Rank Score", exact=True)
            score.click()
            expect(page.get_by_role("columnheader", name="Rank Score", exact=True)).to_have_attribute("aria-sort", "descending")
            expect(created).not_to_have_attribute("aria-sort")
            expect(names).to_have_text(["A full managed", "E CLI full", "D CLI advanced", "F CLI custom", "B advanced managed", "C failed managed", "G CLI old"])
            score.click()
            expect(page.get_by_role("columnheader", name="Rank Score", exact=True)).to_have_attribute("aria-sort", "ascending")
            expect(names).to_have_text(["B advanced managed", "F CLI custom", "D CLI advanced", "E CLI full", "A full managed", "C failed managed", "G CLI old"])
            page.get_by_role("button", name="Preset", exact=True).click()
            expect(page.locator(".archive-table tbody tr td:nth-child(3)")).to_have_text(["advanced", "advanced", "custom-history-fixture", "default", "full", "full", "未记录"])
            page.get_by_role("button", name="当前状态", exact=True).click()
            expect(names.first).to_have_text("C failed managed")
            page.get_by_role("button", name="进度", exact=True).click()
            expect(names.first).to_have_text("A full managed")
            page.get_by_role("button", name="Run / 模型", exact=True).click()
            expect(names).to_have_text(["A full managed", "B advanced managed", "C failed managed", "D CLI advanced", "E CLI full", "F CLI custom", "G CLI old"])
            preset.select_option("full")
            expect(names).to_have_text(["A full managed", "E CLI full"])
            preset.select_option("advanced")
            expect(names).to_have_text(["B advanced managed", "D CLI advanced"])
            page.get_by_role("combobox", name="筛选历史状态", exact=True).select_option("failed")
            expect(page.get_by_text("没有匹配的历史 run", exact=True)).to_be_visible()
            preset.select_option("all")
            expect(names).to_have_text(["C failed managed"])
            page.get_by_role("combobox", name="筛选历史状态", exact=True).select_option("all")
            page.get_by_role("textbox", name="搜索存档", exact=True).fill("CLI")
            expect(names).to_have_count(4)
            preset.select_option("full")
            expect(names).to_have_text(["E CLI full"])
            page.get_by_role("textbox", name="搜索存档", exact=True).fill("")
            preset.select_option("")
            expect(names).to_have_text(["G CLI old"])
            preset.select_option("custom-history-fixture")
            expect(names).to_have_text(["F CLI custom"])
            preset.select_option("all")
            expect(page.get_by_role("combobox", name="筛选历史批次", exact=True)).to_have_count(0)
            score.click()
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "history-dark.png"), full_page=True)
            page.get_by_role("button", name="浅色", exact=True).click()
            for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, height)
                for control in page.locator(".archives-section .runs-toolbar select").all():
                    box = control.bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
                page.screenshot(path=str(directory / f"history-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            preset.select_option("advanced")
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="对比选中曲线", exact=True).click()
            expect(page.locator(".curve-legend > div")).to_have_count(2)
            browser.close()
        assert not errors, errors
        assert all(method == "GET" for method, _ in requests), requests
        assert all(path.read_bytes() == original for path, original in before.items()), "筛选排序不得修改原始存档"
        print(f"历史验收通过：表头点击排序与升降序标记、preset 组合筛选、零分与缺失字段、受控与 CLI 归并、曲线选择、明暗与手机。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
