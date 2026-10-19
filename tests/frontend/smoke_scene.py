"""图形化单局验收：构造资源，通过真实游戏操作生成六人、装备和怪物快照。无模型请求。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
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


def rich_snapshot():
    tools = GuildManagerTools.from_data_dir(ROOT / "data/presets/full")
    session_id = tools.start_session()["session_id"]
    # 仅构造测试起始资源。角色成长、装备和怪物均由游戏接口生成，不修改游戏数据表。
    tools._sessions[session_id].state = replace(tools.get_state(session_id), gold=40000, experience_pool=40000, materials={key: 200 for key in MATERIAL_NAMES})
    before = tools.get_observation(session_id)["observation"]
    for _ in range(3):
        for upgrade in tools.get_observation(session_id)["observation"]["global_upgrades"]:
            if upgrade["party_size_bonus"] and upgrade["can_purchase"]:
                assert tools.purchase_upgrade(session_id, upgrade["upgrade_id"])["ok"]
    for candidate in before["recruit_candidates"][:6]:
        assert tools.recruit_adventurer(session_id, candidate["candidate_id"])["ok"]
    for index, member in enumerate(tools.get_observation(session_id)["observation"]["adventurers"]):
        assert tools.allocate_experience(session_id, member["adventurer_id"], 1800 + index * 300)["ok"]
    recipes = tools.get_observation(session_id)["observation"]["crafting_recipes"]
    for recipe in recipes[:16]:
        current = next(item for item in tools.get_observation(session_id)["observation"]["crafting_recipes"] if item["recipe_id"] == recipe["recipe_id"])
        if current["can_craft"]:
            assert tools.craft_equipment(session_id, recipe["recipe_id"])["ok"]
    observation = tools.get_observation(session_id)["observation"]
    for item in observation["equipment_inventory"]:
        for member in tools.get_observation(session_id)["observation"]["adventurers"]:
            slot = next(slot for slot in member["equipment_slots"] if slot["slot"] == item["slot"])
            if (not item["allowed_classes"] or member["template_id"] in item["allowed_classes"]) and slot["item"] is None and slot["blocked_by"] is None:
                assert tools.equip_item(session_id, member["adventurer_id"], item["instance_id"])["ok"]
                break
    for _ in range(11):
        assert tools.end_turn(session_id, [])["ok"]
    observation = tools.get_observation(session_id)["observation"]
    for index, member in enumerate(tools.get_state(session_id).adventurers):
        visible = next(item for item in observation["adventurers"] if item["adventurer_id"] == member.adventurer_id)
        member.resources.current_hp = max(1, int(visible["effective_stats"]["hp"] * [.27, .84, .62, .95, .73, .48][index]))
        member.resources.current_mp = int(visible["effective_stats"]["mp"] * [.55, .8, .45, .9, .35, .7][index])
    after = tools.get_observation(session_id)["observation"]
    assert len(after["adventurers"]) == 6
    assert len(after["monsters"]) >= 5
    assert len(after["equipment_inventory"]) >= 6
    return before, after


def assert_overview_space(page):
    """检查真实内容是否放得下，不仅检查七个外框是否在屏幕内。"""
    problems = page.evaluate("""() => {
        const issues = [];
        if (document.scrollingElement.scrollHeight > innerHeight + 1) issues.push('页面需要纵向滚动');
        for (const selector of ['.focus-workspace', '.workspace-overview-panel', '.overview-module-content']) {
            for (const element of document.querySelectorAll(selector)) {
                if (element.scrollHeight > element.clientHeight + 1) issues.push(`${element.closest('.overview-module')?.getAttribute('aria-label') || selector} 内容溢出 ${element.scrollHeight - element.clientHeight}px`);
            }
        }
        for (const module of document.querySelectorAll('.overview-module')) {
            const box = module.getBoundingClientRect();
            if (box.top < 0 || box.bottom > innerHeight) issues.push(`${module.className} 超出首屏`);
        }
        return issues;
    }""")
    assert not problems, {"viewport": page.viewport_size, "problems": problems}


def main():
    directory = ROOT / "runs" / f".smoke-scene-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    jobs, _, _ = seed(app.state.workbench)
    before, after = rich_snapshot()
    job = jobs[-1]
    job["name"] = "六人编队图形化验收（构造局面）"
    job["config"]["preset"] = "full"
    job["max_turns"] = after["max_turns"]
    job["observation"] = after
    job["turn"] = after["turn"]
    events = [
        {"type": "run_started", "observation": before},
        {"type": "turn_started", "turn": after["turn"], "observation": after},
        {"type": "model_request", "turn": after["turn"], "step": 1, "request": {"messages": [{"role": "user", "content": "构造总览验收输入，不代表实际模型决策"}]}},
        {"type": "model_response", "turn": after["turn"], "step": 1, "text": "构造观战验收输出：检查编队、装备和本回合怪物。此文本不代表实际模型决策。"},
        {"type": "tool_result", "turn": after["turn"], "name": "get_party", "content": "构造验收：已读取六人编队", "result": {"ok": True}},
        {"type": "worker_stopped"},
    ]
    path = app.state.workbench.root / job["id"] / "events.jsonl"
    path.write_text("".join(json.dumps({"at": timestamp(), "event": e}, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
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
            page = browser.new_page(viewport={"width": 1800, "height": 1120})
            page.emulate_media(reduced_motion="reduce")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/?view=focus&run={job['id']}")
            expect(page.locator(".overview-module")).to_have_count(7)
            expect(page.locator(".overview-party-grid > button")).to_have_count(6)
            assert page.locator(".workspace-overview-panel").evaluate("element => { const box = element.getBoundingClientRect(); return [...element.querySelectorAll('.overview-module')].every(module => { const r = module.getBoundingClientRect(); return r.top >= box.top && r.bottom <= box.bottom + 1; }); }")
            viewpoint = page.locator(".spectator-viewpoint").text_content()
            page.get_by_role("button", name="深色", exact=True).click()
            page.locator(".overview-party-grid img").first.wait_for()
            assert page.locator(".overview-party-grid img").evaluate_all("images => images.every(image => image.complete && image.naturalWidth > 0)")
            page.screenshot(path=str(directory / "overview-dark.png"), full_page=True)
            for width, height in [(1440, 900), (1366, 768), (1280, 720)]:
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(directory / f"overview-{width}x{height}.png"), full_page=True)
                assert_overview_space(page)
            page.set_viewport_size({"width": 1366, "height": 768})
            page.get_by_role("button", name="查看全部材料", exact=True).click()
            expect(page.locator(".material-tile-grid > div")).to_have_count(len(after["materials"]))
            page.keyboard.press("Escape")
            page.get_by_role("button", name=f"查看成员 {after['adventurers'][0]['name']}", exact=True).click()
            expect(page.locator(".scene-inline-inspector .paperdoll")).to_be_visible()
            expect(page.get_by_role("dialog")).to_have_count(0)
            expect(page.locator(".selected-equipment")).to_be_in_viewport(ratio=1)
            expect(page.locator(".scene-character-stats")).to_be_in_viewport(ratio=1)
            assert page.locator(".scene-inspector-scroll").evaluate("element => element.scrollHeight <= element.clientHeight + 1")
            expect(page.get_by_label("切换查看对象", exact=True)).to_have_count(1)
            page.screenshot(path=str(directory / "party-expanded-dark.png"), full_page=True)
            page.get_by_role("button", name="技能与战术", exact=False).click()
            expect(page.locator(".scene-inline-inspector .scene-skill").first).to_be_visible()
            page.screenshot(path=str(directory / "skills-inline-dark.png"), full_page=True)
            page.get_by_label("切换查看对象", exact=True).select_option(after['adventurers'][1]['adventurer_id'])
            expect(page.get_by_role("region", name=f"{after['adventurers'][1]['name']} 的详情", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开编队", exact=True).click()
            expect(page.get_by_role("button", name="技能与战术", exact=False)).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_role("region", name=f"{after['adventurers'][1]['name']} 的详情", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="浅色", exact=True).click()
            page.screenshot(path=str(directory / "overview-light.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            assert page.locator(".workspace-overview-panel").evaluate("element => { const box = element.getBoundingClientRect(); return [...element.querySelectorAll('.overview-module')].every(module => { const r = module.getBoundingClientRect(); return r.top >= box.top && r.bottom <= box.bottom + 1; }); }")
            page.screenshot(path=str(directory / "overview-desktop-compact.png"), full_page=True)
            page.set_viewport_size({"width": 1800, "height": 1120})
            page.get_by_role("button", name="展开本回合怪物", exact=True).click()
            expect(page.locator(".scene-monster-card")).to_have_count(len(after["monsters"]))
            page.screenshot(path=str(directory / "monsters-expanded-light.png"), full_page=True)
            page.get_by_label("切换展开模块", exact=True).select_option("inventory")
            expect(page.locator(".scene-item-card")).to_have_count(len(after["equipment_inventory"]))
            page.get_by_label("筛选装备槽位", exact=True).select_option("boots")
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开装备库", exact=True).click()
            expect(page.get_by_label("筛选装备槽位", exact=True)).to_have_value("boots")
            page.screenshot(path=str(directory / "inventory-filter-restored.png"), full_page=True)
            page.get_by_label("切换展开模块", exact=True).select_option("upgrades")
            expect(page.locator(".scene-upgrade-node")).to_have_count(len(after["global_upgrades"]))
            assert page.locator(".upgrade-connections path").count() == sum(len(upgrade["required_upgrade_ids"]) for upgrade in after["global_upgrades"])
            page.screenshot(path=str(directory / "upgrades-expanded-light.png"), full_page=True)
            page.set_viewport_size({"width": 1800, "height": 800})
            for _ in range(5):
                page.get_by_role("button", name="放大天赋图", exact=True).click()
            upgrade_canvas = page.get_by_role("region", name="公会发展模块", exact=True).locator(".talent-scrollport")
            upgrade_canvas.evaluate("element => { element.scrollTop = 180; }")
            page.wait_for_function("document.querySelector('.module-upgrades .talent-scrollport').scrollTop > 0")
            scroll = upgrade_canvas.evaluate("element => element.scrollTop")
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开公会发展", exact=True).click()
            assert abs(upgrade_canvas.evaluate("element => element.scrollTop") - scroll) < 2
            page.set_viewport_size({"width": 1800, "height": 1120})
            page.get_by_label("切换展开模块", exact=True).select_option("recruitment")
            expect(page.locator(".scene-candidate-card")).to_have_count(len(after["recruit_candidates"]))
            expect(page.locator(".spectator-viewpoint")).to_have_text(viewpoint)
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(directory / "overview-mobile.png"), full_page=True)
            page.get_by_role("button", name="展开编队", exact=True).click()
            expect(page.locator(".scene-inline-inspector")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(page.get_by_role("dialog")).to_have_count(0)
            page.screenshot(path=str(directory / "party-expanded-mobile.png"), full_page=True)
            page.get_by_label("切换展开模块", exact=True).select_option("upgrades")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator(".scene-upgrade-node").first.click()
            expect(page.get_by_role("region", name="公会发展模块", exact=True).locator(".scene-inline-inspector")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            browser.close()
        if "--lighthouse" in sys.argv:
            report_path = directory / "lighthouse-scene.json"
            environment = {**os.environ, "CHROME_PATH": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"}
            npx_cli = Path(shutil.which("npx")).parent / "node_modules/npm/bin/npx-cli.js"
            subprocess.run([shutil.which("node"), str(npx_cli), "--yes", "lighthouse", base + f"/?view=focus&run={job['id']}", "--preset=desktop", "--chrome-flags=--headless", "--quiet", "--only-categories=performance,accessibility,best-practices", "--output=json", f"--output-path={report_path}"], check=True, timeout=120, env=environment)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            print("图形化观战 Lighthouse:", {name: value["score"] for name, value in report["categories"].items()})
        assert not errors, errors
        print(f"总览与模块展开验收通过：七模块首屏、原地展开、内联对象详情、筛选与滚动恢复、共享观看位置、明暗主题和窄屏。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
