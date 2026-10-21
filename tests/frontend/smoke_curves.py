"""统一曲线工作区验收。所有曲线都是构造数据，不请求模型或重算评分。"""
from __future__ import annotations

import json
import math
import os
import re
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


def fixture_curve(scale=1, *, gap=False, different=False, turns=6):
    payload = replay(*(completed_turn(turn, score=0 if turn == 1 else turn * 100 * scale,
                                     gold=100 - turn * 3, xp=turn * 7,
                                     rewards=((turn * 10 * scale, turn * 20 * scale),))
                       for turn in range(1, turns + 1) if not (gap and turn == 3)))
    # 节点同样来自构造的实际操作与结算快照，不直接伪造曲线接口字段。
    for turn in payload["turns"]:
        number = turn["turn"]
        before = {"adventurers": [{"adventurer_id": "hero", "name": "节点剑士", "level": 8 if number <= 4 else 9}],
                  "monsters": [{"monster_id": "boss", "name": "节点首领", "tier": "boss"},
                               {"monster_id": "challenge", "name": "节点挑战", "tier": "challenge"}]}
        turn["observation_before"].update(before)
        if number in (2, 3):
            turn["steps"].insert(0, {"type": "tool_result", "name": "purchase_upgrade",
                                    "arguments": {"upgrade_id": f"roster_expansion_{number - 1}"}, "result": {"ok": True}})
        if number == 4:
            turn["steps"].insert(0, {"type": "tool_result", "name": "allocate_experience", "result": {"ok": True},
                                    "observation_after": {**before, "adventurers": [{"adventurer_id": "hero", "name": "节点剑士", "level": 9}]}})
        if number >= 5:
            for battle in turn["steps"][-1]["result"]["turn_result"]["battles"]:
                battle.update(monster_id="boss", adventurer_name="节点剑士", won=True)
            turn["steps"][-1]["result"]["turn_result"]["battles"].append({
                "monster_id": "challenge", "adventurer_name": "节点剑士", "won": True, "reward": {"gold": 0, "experience": 0}})
    payload["final_observation"]["max_turns"] = turns
    payload.update(status="completed", created_at=timestamp(), updated_at=timestamp(),
                   agent={"type": "FixtureAgent", "config": {"model": f"曲线构造模型 {scale}", "api_key": "must-not-reach-curves"}},
                   data={"preset": "default", "data_hash": "fixture-data", "benchmark_version": "fixture-only",
                         "protocol_hash": "different-fixture" if different else "fixture-rules", "settings_hash": "fixture-settings"},
                   config={"game_seed": 7, "scoring_seed": 11})
    return payload


def scroll_curves_to_bottom(page):
    """只用真实滚轮到达底部，不能用自动定位滚动 overflow:hidden 的祖先。"""
    page.keyboard.press("Escape")
    # 仅初始化位置，后续可达性必须由浏览器滚轮输入证明。
    page.evaluate("""() => {
        document.querySelector('.workspace-main').scrollTop = 0;
        document.querySelector('.curve-board').scrollTop = 0;
    }""")
    board = page.locator(".curve-board").bounding_box()
    header = page.locator(".workspace-header").bounding_box()
    page.mouse.move(board["x"] + 10, board["y"] + min(32, board["height"] / 2))
    page.mouse.wheel(0, 20000)
    page.wait_for_function("""() => {
        const board = document.querySelector('.curve-board');
        const main = document.querySelector('.workspace-main').getBoundingClientRect();
        const note = document.querySelector('.curve-note').getBoundingClientRect();
        return board.scrollTop + board.clientHeight >= board.scrollHeight - 2
            && note.bottom <= main.bottom + 1;
    }""", timeout=3000)
    geometry = page.evaluate("""() => {
        const board = document.querySelector('.curve-board');
        const rect = board.getBoundingClientRect();
        const main = document.querySelector('.workspace-main').getBoundingClientRect();
        const note = document.querySelector('.curve-note').getBoundingClientRect();
        const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
        return { top: rect.top, bottom: rect.bottom, mainBottom: main.bottom, sidebarTop: sidebar.top,
            sidebarBottom: sidebar.bottom, mainScroll: document.querySelector('.workspace-main').scrollTop,
            scrollTop: board.scrollTop, overflows: board.scrollHeight > board.clientHeight + 1,
            noteTop: note.top, noteBottom: note.bottom, headerTop: document.querySelector('.workspace-header').getBoundingClientRect().top };
    }""")
    assert geometry["bottom"] <= geometry["mainBottom"] + 1
    assert geometry["noteTop"] >= geometry["top"] - 1 and geometry["noteBottom"] <= geometry["bottom"] + 1
    assert geometry["mainScroll"] == 0 and abs(geometry["headerTop"] - header["y"]) < 1
    if geometry["overflows"]:
        assert geometry["scrollTop"] > 0
    if page.viewport_size["width"] <= 640:
        assert geometry["noteBottom"] <= geometry["sidebarTop"] + 1
    return geometry


def verify_hover_scrollbar_stability(page):
    """在真实溢出临界点反复悬停密集节点，不能自动追踪已经移位的按钮。"""
    board = page.locator(".curve-board")
    node = page.get_by_role("region", name="战力曲线", exact=True).get_by_role("button", name=re.compile("轨道节点.*T20："))
    expect(node).to_be_visible()
    page.evaluate("async () => { await document.fonts.ready; }")
    page.get_by_label("定位对比回合", exact=True).fill("1")
    board.focus()
    page.mouse.move(0, 0)

    def settle():
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        page.wait_for_function("""() => [...document.querySelectorAll('.curve-plot svg')].every(svg =>
            Math.abs(svg.getBoundingClientRect().width - svg.viewBox.baseVal.width) < 0.1)""")

    def geometry():
        return page.evaluate("""() => {
            const board = document.querySelector('.curve-board');
            const rect = board.getBoundingClientRect();
            const note = document.querySelector('.curve-note').getBoundingClientRect();
            const node = document.querySelector('.curve-chart[aria-label="战力曲线"] .curve-event-node[aria-label*=" / T20："]');
            const box = node.getBoundingClientRect();
            return { width: board.clientWidth, clientHeight: board.clientHeight, scrollHeight: board.scrollHeight,
                scrollTop: board.scrollTop, spare: rect.bottom - note.bottom - board.scrollTop,
                x: box.x, y: box.y, nodeWidth: box.width, nodeHeight: box.height,
                turn: document.querySelector('input[aria-label="定位对比回合"]').value };
        }""")

    for _ in range(8):
        board.evaluate("element => { element.scrollTop = 0; }")
        settle()
        before = geometry()
        if 1 <= before["spare"] <= 4:
            break
        height = math.ceil(page.viewport_size["height"] - before["spare"] + 2)
        page.set_viewport_size({"width": 1536, "height": height})
    assert 1 <= before["spare"] <= 4, f"未到达溢出临界高度：{before}"
    assert before["scrollHeight"] <= before["clientHeight"], before
    assert before["scrollTop"] == 0, before
    pointer = {"x": before["x"] + before["nodeWidth"] / 2, "y": before["y"] + before["nodeHeight"] / 2}
    svg = page.get_by_role("region", name="经济曲线", exact=True).locator("svg").bounding_box()
    empty_turn = {"x": svg["x"] + 54, "y": svg["y"] + 25}
    cycles = []
    for _ in range(3):
        # 使用固定屏幕坐标，locator.hover() 的重定位会掩盖节点漂移。
        page.mouse.move(pointer["x"], pointer["y"])
        page.wait_for_function("""() => {
            const board = document.querySelector('.curve-board');
            return board.scrollHeight > board.clientHeight && !!document.querySelector('.curve-event-popover');
        }""")
        settle()
        hovered = geometry()
        assert hovered["width"] == before["width"], {"before": before, "hovered": hovered}
        assert abs(hovered["x"] - before["x"]) < 0.1 and abs(hovered["y"] - before["y"]) < 0.1, hovered
        assert hovered["turn"] == "20", f"鼠标不动时误切到相邻回合：{hovered}"
        expect(page.get_by_role("dialog", name="战力节点详情", exact=True)).to_contain_text("T20")
        page.mouse.move(empty_turn["x"], empty_turn["y"])
        page.wait_for_function("document.querySelector('input[aria-label=\"定位对比回合\"]').value === '1'")
        settle()
        empty = geometry()
        assert empty["scrollHeight"] <= empty["clientHeight"], empty
        assert empty["width"] == before["width"] and abs(empty["x"] - before["x"]) < 0.1, empty
        cycles.append({"hovered": hovered, "empty": empty})
    return {"viewport": page.viewport_size, "before": before, "cycles": cycles}


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
            # 保留会占用布局宽度的滚动条，否则默认的隐藏模式会掩盖临界高度问题。
            browser = playwright.chromium.launch(channel="msedge", headless=True, ignore_default_args=["--hide-scrollbars"])
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
            economy = page.get_by_role("region", name="经济曲线", exact=True)
            experience = page.get_by_role("region", name="经验曲线", exact=True)
            power = page.get_by_role("region", name="战力曲线", exact=True)
            expect(economy.get_by_role("button", name=re.compile("轨道节点.*T2"))).to_have_count(4)
            experience.get_by_role("button", name=re.compile("轨道节点.*T4")).first.hover()
            experience_detail = page.get_by_role("dialog", name="经验节点详情", exact=True)
            expect(experience_detail).to_be_visible()
            expect(experience_detail).to_contain_text("1号 曲线构造模型 4")
            expect(experience_detail).to_contain_text("节点剑士：Lv8 → Lv9")
            experience_detail.hover()
            expect(experience_detail).to_be_visible()
            experience.get_by_role("heading", name="经验", exact=True).hover()
            expect(experience_detail).not_to_be_visible()
            expect(page.get_by_role("region", name="所选回合关键节点", exact=True)).to_contain_text("节点剑士：Lv8 → Lv9")
            experience.get_by_role("button", name=re.compile("轨道节点.*T4")).first.click()
            expect(page.get_by_label("定位对比回合", exact=True)).to_have_value("4")
            expect(page.get_by_role("region", name="所选回合关键节点", exact=True)).to_contain_text("节点剑士：Lv8 → Lv9")
            power.get_by_role("button", name=re.compile("轨道节点.*T5")).first.press("Enter")
            expect(page.get_by_label("定位对比回合", exact=True)).to_have_value("5")
            expect(page.get_by_role("region", name="所选回合关键节点", exact=True)).to_contain_text("节点挑战")
            power_detail = page.get_by_role("dialog", name="战力节点详情", exact=True)
            expect(power_detail).to_be_visible()
            expect(power_detail.get_by_role("listitem")).to_have_count(2)
            expect(power_detail).to_contain_text("节点首领")
            expect(power_detail).to_contain_text("节点挑战")
            page.screenshot(path=str(directory / "curves-node-details.png"), full_page=True, animations="disabled")
            page.keyboard.press("Escape")
            expect(power_detail).not_to_be_visible()
            expect(power.locator(".curve-event-node[data-event-count='2']")).to_have_count(8)
            page.get_by_label("定位对比回合", exact=True).fill("3")
            row_b = values.get_by_role("row").filter(has_text="曲线构造模型 2")
            expect(row_b.get_by_text("未记录", exact=True)).to_have_count(3)
            page.get_by_label("定位对比回合", exact=True).fill("1")
            row_a = values.get_by_role("row").filter(has_text="曲线构造模型 1")
            expect(row_a.get_by_role("cell").last.get_by_text("0", exact=True)).to_be_visible()
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "curves-income-dark.png"), full_page=True)
            page.get_by_role("button", name="结算后余额", exact=True).click()
            separate = economy.get_by_role("button", name=re.compile("轨道节点.*T2"))
            expect(separate).to_have_count(4)
            for node in separate.all():
                expect(node).to_have_attribute("data-event-count", "1")
            separate.first.click()
            expect(page.get_by_label("定位对比回合", exact=True)).to_have_value("2")
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
                for chart in (economy, experience, power):
                    assert chart.locator(".curve-plot button").count() == 0
                    assert chart.locator(".curve-event-row").count() == 4
                    plot_box = chart.locator(".curve-plot").bounding_box()
                    assert chart.locator(".curve-event-tracks").bounding_box()["y"] >= plot_box["y"] + plot_box["height"]
                if width >= 1280:
                    power.get_by_role("button", name=re.compile("轨道节点.*T5")).last.hover()
                    expect(power_detail).to_be_visible()
                    box = power_detail.bounding_box()
                    plot_box = power.locator(".curve-plot").bounding_box()
                    assert box["y"] >= plot_box["y"] + plot_box["height"]
                    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
                    assert box["y"] + box["height"] <= height + 1
                    page.mouse.move(0, 0)
                    expect(power_detail).not_to_be_visible()
                geometry = scroll_curves_to_bottom(page)
                if width in (1280, 390):
                    assert geometry["overflows"] and geometry["scrollTop"] > 0
                if width == 1280:
                    page.locator(".curve-board").focus()
                    page.keyboard.press("Home")
                    page.wait_for_function("document.querySelector('.curve-board').scrollTop <= 1")
                    page.keyboard.press("End")
                    page.wait_for_function("""() => {
                        const board = document.querySelector('.curve-board');
                        return board.scrollTop + board.clientHeight >= board.scrollHeight - 2;
                    }""")
                page.screenshot(path=str(directory / f"curves-bottom-{width}.png"))
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
            # 35 回合密集记录：真实触摸、精确横轴对齐、相邻命中区域及范围放大。
            dense_path = manager.archive_root / "fixture-curve-dense" / "replay.json"
            dense_path.parent.mkdir()
            dense_path.write_text(json.dumps(fixture_curve(turns=35), ensure_ascii=False), encoding="utf-8")
            before[dense_path] = dense_path.read_bytes()
            hover_page = browser.new_page(viewport={"width": 1536, "height": 1000})
            hover_page.on("pageerror", lambda error: errors.append(str(error)))
            hover_page.on("request", lambda request: requests.append((request.method, request.url)))
            hover_page.goto(base + "/?section=curves&curves=archive:fixture-curve-dense")
            hover_report = verify_hover_scrollbar_stability(hover_page)
            (directory / "curves-hover-scrollbar.json").write_text(json.dumps(hover_report, ensure_ascii=False, indent=2), encoding="utf-8")
            hover_page.screenshot(path=str(directory / "curves-hover-scrollbar.png"))
            hover_page.close()
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.on("request", lambda request: requests.append((request.method, request.url)))
            mobile.goto(base + "/?section=curves&curves=archive:fixture-curve-dense")
            expect(mobile.locator(".curve-legend > div")).to_have_count(1)
            dense_power = mobile.get_by_role("region", name="战力曲线", exact=True)
            nodes = dense_power.get_by_role("button", name=re.compile("轨道节点"))
            expect(nodes).to_have_count(31)
            assert nodes.evaluate_all("""nodes => nodes.every((node, i) => node.dataset.compact === 'true' && (!i || nodes[i-1].getBoundingClientRect().right <= node.getBoundingClientRect().left + 0.1))""")
            target = dense_power.get_by_role("button", name=re.compile("轨道节点.*T20："))
            target.tap()
            expect(mobile.get_by_label("定位对比回合", exact=True)).to_have_value("20")
            expect(mobile.get_by_role("region", name="所选回合关键节点", exact=True)).to_contain_text("节点挑战")
            touch_detail = mobile.get_by_role("dialog", name="战力节点详情", exact=True)
            expect(touch_detail).to_be_visible()
            expect(touch_detail.get_by_role("listitem")).to_have_count(2)
            expect(touch_detail).to_contain_text("节点首领")
            expect(touch_detail).to_contain_text("节点挑战")
            box = touch_detail.bounding_box()
            plot_box = dense_power.locator(".curve-plot").bounding_box()
            assert box["y"] >= plot_box["y"] + plot_box["height"]
            assert box["x"] >= 0 and box["x"] + box["width"] <= 391
            mobile.screenshot(path=str(directory / "curves-touch-details.png"), animations="disabled")
            node_box = target.bounding_box()
            point_box = dense_power.locator("circle[data-selected='true']").bounding_box()
            assert abs(node_box["x"] + node_box["width"] / 2 - point_box["x"] - point_box["width"] / 2) < 1
            assert dense_power.locator(".curve-plot button").count() == 0
            assert dense_power.locator(".curve-event-node[title]").count() == 0
            dense_power.screenshot(path=str(directory / "curves-dense-390.png"))
            mobile.get_by_label("曲线起始回合", exact=True).fill("20")
            mobile.get_by_label("曲线结束回合", exact=True).fill("21")
            expect(nodes).to_have_count(2)
            expect(target).to_have_attribute("data-compact", "false")
            expect(target).to_have_text("首挑")
            target.tap()
            expect(touch_detail).to_be_visible()
            mobile.get_by_role("button", name="关闭节点详情", exact=True).tap()
            expect(touch_detail).not_to_be_visible()
            dense_power.screenshot(path=str(directory / "curves-dense-zoom-390.png"))
            browser.close()
        assert all(path.read_bytes() == original for path, original in before.items()), "读取曲线不能修复、重算或回写存档"
        assert not any(method != "GET" for method, _ in requests), requests
        assert not any("/rescore" in url or "/rebuild" in url or url.endswith("/replay") for _, url in requests), requests
        assert not errors, errors
        print(f"曲线验收通过：统一入口、CLI 与受控 run、三指标与口径切换、三类关键节点、图下独立事件轨道、同数值不同 run 分离、悬停详情浮层、移入阅读、移开与 Escape 关闭、键盘及触摸查看、缺回合断线、零分、条件警告、范围与联动、刷新恢复、单局与多路往返、明暗和手机、真实滚轮访问底部、顶部固定与手机导航避让、临界高度下滚动条切换与密集节点固定坐标悬停。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
