"""天赋路线图验收：真实高级规则生成构造快照，不请求模型或重新评分。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
from dataclasses import replace
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/frontend"))
from smoke_watch import seed
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.game.state import MATERIAL_NAMES


def talent_snapshot():
    tools = GuildManagerTools.from_data_dir(ROOT / "data/presets/advanced")
    session_id = tools.start_session()["session_id"]
    before = tools.get_observation(session_id)["observation"]
    tools._sessions[session_id].state = replace(tools.get_state(session_id), gold=100000, experience_pool=100000, materials={key: 10000 for key in MATERIAL_NAMES})

    def unlock(upgrade_id):
        upgrades = tools.get_observation(session_id)["observation"]["global_upgrades"]
        node = next(upgrade for upgrade in upgrades if upgrade["upgrade_id"] == upgrade_id)
        if node["unlocked"]:
            return
        for parent in node["required_upgrade_ids"]:
            unlock(parent)
        assert tools.purchase_upgrade(session_id, upgrade_id)["ok"]

    unlock("arcane_council")
    unlock("armory")
    # 仅构造可视化测试资源缺口。解锁、前置和互斥状态由真实游戏接口计算。
    tools._sessions[session_id].state = replace(tools.get_state(session_id), gold=80, experience_pool=0, materials={key: 0 for key in MATERIAL_NAMES})
    after = tools.get_observation(session_id)["observation"]
    return before, after


def main():
    directory = ROOT / "runs" / f".smoke-talents-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    jobs, _, _ = seed(app.state.workbench)
    before, after = talent_snapshot()
    job = jobs[-1]
    job.update(name="高级天赋路线验收（构造局面）", observation=after, turn=after["turn"], max_turns=after["max_turns"])
    job["config"]["preset"] = "advanced"
    events = [
        {"type": "run_started", "observation": before},
        {"type": "turn_started", "turn": after["turn"], "observation": after},
        {"type": "worker_stopped"},
    ]
    path = app.state.workbench.root / job["id"] / "events.jsonl"
    path.write_text("".join(json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    job["cursor"] = path.stat().st_size
    app.state.workbench._save(job)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors = []
    try:
        wait_until(lambda: server.started)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.emulate_media(reduced_motion="reduce")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/?view=focus&run={job['id']}")
            page.get_by_role("button", name="展开公会发展", exact=True).click()
            module = page.get_by_role("region", name="公会发展模块", exact=True)
            expect(module.locator(".scene-upgrade-node")).to_have_count(25)
            expect(module.locator(".talent-lane")).to_have_count(5)
            expect(module.locator("path.dependency")).to_have_count(sum(len(upgrade["required_upgrade_ids"]) for upgrade in after["global_upgrades"]))
            expect(module.locator("path.exclusive")).to_have_count(3)
            page.get_by_role("button", name="深色", exact=True).click()
            page.get_by_role("button", name="适应全图", exact=True).click()
            assert module.locator(".talent-scrollport").evaluate("""element => {
                const box = element.getBoundingClientRect();
                return [...element.querySelectorAll('.scene-upgrade-node')].every(node => { const r = node.getBoundingClientRect(); return r.top >= box.top && r.left >= box.left && r.bottom <= box.bottom + 1 && r.right <= box.right + 1; });
            }""")
            page.screenshot(path=str(directory / "talents-all-dark.png"), full_page=True)
            page.get_by_role("button", name="查看升级 战争学院", exact=True).click()
            detail = page.get_by_role("region", name="战争学院 的详情", exact=True)
            expect(detail.get_by_text("互斥锁定", exact=True)).to_be_visible()
            assert detail.get_by_label("切换查看对象", exact=True).bounding_box()["width"] >= 120
            expect(detail.get_by_text("龙鳞", exact=True)).to_be_visible()
            expect(module.locator(".scene-upgrade-node.is-route")).not_to_have_count(0)
            highlighted = module.locator("path.dependency.highlighted").count()
            page.mouse.move(5, 5)
            assert module.locator("path.dependency.highlighted").count() == highlighted
            expect(module.locator('.scene-upgrade-node[data-upgrade-id="arcane_council"]')).to_have_class(re.compile("is-exclusive"))
            page.screenshot(path=str(directory / "talents-war-route-dark.png"), full_page=True)
            detail.get_by_role("button", name=re.compile("秘法结社")).click()
            expect(page.get_by_role("region", name="秘法结社 的详情", exact=True)).to_be_visible()
            expect(module.locator('.scene-upgrade-node[data-upgrade-id="arcane_council"]')).to_have_attribute("aria-pressed", "true")
            attempts = page.request.get(base + f"/api/workbench/jobs/{job['id']}/watch").json()["attempts"]
            initial = next(attempt for attempt in attempts if attempt["turn"] is None)
            page.get_by_label("选择观战回合", exact=True).select_option(str(initial["id"]))
            expect(page.get_by_text("历史局面", exact=True)).to_be_visible()
            expect(module.locator(".scene-upgrade-node.unlocked")).to_have_count(0)
            page.get_by_role("button", name=re.compile("返回实时")).click()
            expect(module.locator(".scene-upgrade-node.unlocked")).to_have_count(sum(upgrade["unlocked"] for upgrade in after["global_upgrades"]))
            page.get_by_label("搜索天赋", exact=True).fill("战争")
            page.get_by_role("button", name="定位", exact=True).click()
            expect(page.get_by_role("region", name="战争学院 的详情", exact=True)).to_be_visible()
            expect(module.locator(".scene-upgrade-node")).to_have_count(25)
            page.get_by_role("button", name="天赋图原始大小", exact=True).click()
            expect(page.get_by_role("button", name="天赋图原始大小", exact=True)).to_have_text("100%")
            viewport = module.locator(".talent-scrollport")
            viewport.evaluate("element => { element.scrollLeft = 110; }")
            page.wait_for_function("document.querySelector('.module-upgrades .talent-scrollport').scrollLeft > 0")
            left = viewport.evaluate("element => element.scrollLeft")
            canvas_handle = viewport.element_handle()
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开公会发展", exact=True).click()
            expect(page.get_by_label("搜索天赋", exact=True)).to_have_value("战争")
            expect(page.get_by_role("button", name="天赋图原始大小", exact=True)).to_have_text("100%")
            restored = viewport.evaluate("element => element.scrollLeft")
            assert abs(restored - left) < 2, {"before": left, "restored": restored, "same_canvas": canvas_handle.evaluate("element => element === document.querySelector('.module-upgrades .talent-scrollport')"), "zoom": page.get_by_role("button", name="天赋图原始大小", exact=True).text_content()}
            # 空白处鼠标平移，不要求用户抓滚动条。
            viewport.evaluate("element => { element.scrollLeft = 0; }")
            box = viewport.bounding_box()
            page.mouse.move(box["x"] + box["width"] - 15, box["y"] + 12)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] - 95, box["y"] + 12)
            page.mouse.up()
            assert viewport.evaluate("element => element.scrollLeft") > 0
            page.get_by_role("button", name="浅色", exact=True).click()
            page.get_by_role("button", name="收起对象详情", exact=True).click()
            page.get_by_role("button", name="适应全图", exact=True).click()
            page.screenshot(path=str(directory / "talents-all-light.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_label("搜索天赋", exact=True).fill("战争")
            page.get_by_role("button", name="定位", exact=True).click()
            expect(page.get_by_role("region", name="战争学院 的详情", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(directory / "talents-mobile.png"), full_page=True)
            browser.close()
        assert not errors, errors
        print(f"天赋图验收通过：真实高级快照、分支层级、完整前置、互斥、费用缺口、搜索缩放与平移、历史状态、返回恢复、明暗主题和窄屏。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
