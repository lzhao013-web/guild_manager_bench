"""整局轨迹验收：真实游戏操作，构造续跑日志，不请求模型或评测评分。"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
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


def scenario():
    tools = GuildManagerTools.from_data_dir(ROOT / "data/presets/advanced")
    session = tools.start_session()["session_id"]
    tools._sessions[session].state = replace(tools.get_state(session), gold=5000, materials={key: 200 for key in MATERIAL_NAMES})
    obs = lambda: tools.get_observation(session)["observation"]
    events = [{"type": "run_started", "observation": obs()}]
    current = []

    def start():
        events.append({"type": "turn_started", "turn": obs()["turn"], "observation": obs()})

    def call(name, args, invoke):
        turn = obs()["turn"]
        identity = str(len(events))
        result = invoke()
        if result.get("ok") and not (name == "refresh_monsters" and args.get("preview") is True):
            result = {**result, "_observation_after": obs()}
        events.extend([
            {"type": "tool_call", "turn": turn, "name": name, "arguments": args, "call_id": identity},
            {"type": "tool_result", "turn": turn, "name": name, "arguments": args, "call_id": identity, "result": result},
        ])
        return result

    def end():
        turn = obs()["turn"]
        result = call("end_turn", {"hunts": []}, lambda: tools.end_turn(session, []))
        assert result["ok"]
        events.extend([{"type": "turn_completed", "turn": turn}, {"type": "turn_scored", "turn": turn, "rank_score": 0}])

    start()
    preview = call("refresh_monsters", {"locked_monster_ids": [], "preview": True}, lambda: tools.refresh_monsters(session, [], True))
    assert preview["ok"]
    candidate = next(c for c in obs()["recruit_candidates"] if c["template_id"] == "mercenary_warrior")
    assert call("recruit_adventurer", {"candidate_id": candidate["candidate_id"]}, lambda: tools.recruit_adventurer(session, candidate["candidate_id"]))["ok"]
    end()
    start()
    purchase = call("purchase_upgrade", {"upgrade_id": "training_grounds"}, lambda: tools.purchase_upgrade(session, "training_grounds"))
    assert purchase["ok"]
    purchase_summary = purchase["event"]["summary"]
    end()
    events.append({"type": "run_resumed", "restored_observation": obs(), "last_rank_score": 0})
    start()
    recipe = "recipe_t1_heavy_sword"
    for _ in range(2):
        assert call("craft_equipment", {"recipe_id": recipe}, lambda: tools.craft_equipment(session, recipe))["ok"]
    member = obs()["adventurers"][0]
    items = obs()["equipment_inventory"][:2]
    for item in reversed(items):
        assert call("equip_item", {"adventurer_id": member["adventurer_id"], "equipment_instance_id": item["instance_id"]}, lambda: tools.equip_item(session, member["adventurer_id"], item["instance_id"]))["ok"]
    end()
    checkpoint = tools.get_state(session)
    before_four = obs()
    start()
    assert call("craft_equipment", {"recipe_id": recipe}, lambda: tools.craft_equipment(session, recipe))["ok"]
    events.append({"type": "worker_stopped"})
    # 构造续跑恢复到回合检查点，保留旧分支日志验证标记，当前实例 ID 可能重新使用。
    tools._sessions[session].state = checkpoint
    events.append({"type": "run_resumed", "restored_observation": before_four, "last_rank_score": 0})
    boundary = len(events)
    start()
    assert call("craft_equipment", {"recipe_id": recipe}, lambda: tools.craft_equipment(session, recipe))["ok"]
    failed = call("equip_item", {"adventurer_id": member["adventurer_id"], "equipment_instance_id": "missing"}, lambda: tools.equip_item(session, member["adventurer_id"], "missing"))
    assert failed["ok"] is False
    end()
    events.append({"type": "worker_stopped"})
    current = [{"type": "run_resumed", "restored_observation": before_four, "last_rank_score": 0}, *events[boundary:]]
    return events, current, obs(), purchase_summary, items


def main():
    directory = ROOT / "runs" / f".smoke-trajectories-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    manager = app.state.workbench
    jobs, _, _ = seed(manager)
    job = jobs[0]
    events, current, observation, purchase_summary, items = scenario()
    archive = manager.archive_root / "trajectory-fixture"
    archive.mkdir(parents=True)
    path = archive / "trace.jsonl"
    path.write_text("".join(json.dumps({"record_type": "event", "created_at": timestamp(), "event": event}, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    journal = manager.root / job["id"] / "events.jsonl"
    journal.write_text("".join(json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False) + "\n" for event in current), encoding="utf-8")
    original = path.read_bytes()
    job.update(name="跨回合轨迹验收（构造）", observation=observation, turn=observation["turn"], archive_id=archive.name, cursor=journal.stat().st_size)
    job["config"]["preset"] = "advanced"
    manager._save(job)
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
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/?view=focus&run={job['id']}")
            page.get_by_role("button", name="展开最近行动", exact=True).click()
            page.get_by_role("button", name="整局检索", exact=True).click()
            overview = page.get_by_role("region", name="整局经营操作概览", exact=True)
            craft = overview.get_by_role("button", name="查看装备制作记录", exact=True)
            expect(craft).to_be_enabled()
            expect(craft.locator("b")).to_have_text("4")
            expect(craft.locator("em")).to_have_text("已确认 3")
            expect(overview.get_by_role("button", name=re.compile(r"共\s*13\s*次尝试"))).to_be_visible()
            page.screenshot(path=str(directory / "overview-light.png"), full_page=True)
            for width, height in [(1280, 720), (1440, 900)]:
                page.set_viewport_size({"width": width, "height": height})
                metrics = page.locator(".action-whole-workspace").evaluate("""element => {
                    const summary = element.querySelector('.trajectory-overview');
                    const body = element.querySelector('.trajectory-browser-body');
                    const detail = body.querySelector('.action-detail');
                    const s = summary.getBoundingClientRect(), b = body.getBoundingClientRect(), d = detail.getBoundingClientRect();
                    return { fits: element.scrollHeight <= element.clientHeight + 1, widthFits: summary.scrollWidth <= summary.clientWidth + 1,
                        fullWidth: Math.abs(s.width - b.width) <= 1, beforeDetail: s.bottom <= d.top + 1,
                        resultHeight: element.querySelector('.trajectory-results').clientHeight };
                }""")
                assert metrics["fits"] and metrics["widthFits"] and metrics["fullWidth"] and metrics["beforeDetail"] and metrics["resultHeight"] >= 120, metrics
            craft.click()
            expect(page.get_by_label("筛选轨迹操作")).to_have_value("category:craft")
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(4)
            overview.get_by_role("button", name="查看操作失败记录", exact=True).click()
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(1)
            overview.get_by_role("button", name=re.compile(r"共\s*13\s*次尝试")).click()
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(13)
            search = page.get_by_label("搜索整局行动", exact=True)
            search.fill("训练场")
            expect(craft.locator("b")).to_have_text("4")
            expect(craft.locator("em")).to_have_text("已确认 3")
            result = page.get_by_role("button", name=f"查看轨迹 回合2 {purchase_summary}", exact=True)
            expect(result).to_be_visible()
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(1)
            result.click()
            detail = page.get_by_role("region", name="所选行动详情", exact=True)
            expect(detail.get_by_role("heading", name=purchase_summary, exact=True)).to_be_visible()
            expect(page.get_by_label("选择观战回合")).not_to_have_value("")
            expect(page.get_by_text("历史局面", exact=True)).to_be_visible()
            raw = detail.locator("details").filter(has=page.get_by_text("原始事件与调用参数", exact=True))
            raw.locator("summary").click()
            expect(raw.locator("pre")).to_contain_text("training_grounds")
            raw.locator("summary").click()
            page.screenshot(path=str(directory / "search-talent-light.png"), full_page=True)
            page.get_by_label("切换展开模块", exact=True).select_option("upgrades")
            page.get_by_role("button", name="查看升级 训练场", exact=True).click()
            talent = page.get_by_role("region", name="训练场 的详情", exact=True)
            expect(talent.get_by_text("第 2 回合解锁", exact=True)).to_be_visible()
            talent.locator(".object-trajectories > summary").click()
            expect(talent.locator(".trajectory-result")).to_have_count(1)
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "talent-history-dark.png"), full_page=True)
            # 返回实时再查看仓库，避免把回合 2 没有的装备当成当时已存在。
            page.get_by_role("button", name=re.compile("返回实时")).click()
            page.get_by_label("切换展开模块", exact=True).select_option("inventory")
            page.get_by_role("button", name="查看装备 铁剑", exact=True).nth(1).click()
            equipment = page.get_by_role("region", name="装备库模块", exact=True).locator(".scene-inline-inspector")
            expect(equipment.get_by_text("第 3 回合制作", exact=True)).to_be_visible()
            equipment.locator(".object-trajectories > summary").click()
            expect(equipment.locator(".object-trajectories")).to_contain_text(items[1]["instance_id"])
            expect(equipment.locator(".object-trajectories .trajectory-result")).to_have_count(3)
            expect(equipment.get_by_text(re.compile("从 .* 身上卸下 铁剑"))).to_be_visible()
            page.screenshot(path=str(directory / "equipment-history-dark.png"), full_page=True)
            page.get_by_role("button", name="制作配方", exact=True).click()
            page.get_by_role("button", name="查看制作配方 铁剑", exact=True).click()
            recipe = page.get_by_role("region", name="装备库模块", exact=True).locator(".scene-inline-inspector")
            expect(recipe.get_by_text("已确认制作 3 次 · 首次第 3 回合", exact=True)).to_be_visible()
            recipe.locator(".object-trajectories > summary").click()
            expect(recipe.get_by_text("已回滚，不再生效", exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "recipe-history-dark.png"), full_page=True)
            page.get_by_label("切换展开模块", exact=True).select_option("actions")
            search.fill("铁剑")
            page.get_by_label("筛选轨迹状态").select_option("confirmed")
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(5)  # 三次制作、两次穿戴。
            page.get_by_label("轨迹结束回合").fill("3")
            expect(page.locator(".trajectory-search-panel .trajectory-result")).to_have_count(4)
            for width, height in [(1280, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert overview.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
                if width < 650:
                    assert page.locator(".trajectory-results").evaluate("element => getComputedStyle(element).overflowY === 'visible'")
                page.screenshot(path=str(directory / f"search-{width}.png"), full_page=True)
            browser.close()
        assert not errors, errors
        assert path.read_bytes() == original
        print(f"整局轨迹验收通过：全宽经营概览、整局计数不随筛选改变、类别和状态入口、跨续跑检索、天赋购买回合、装备制作与换装、回滚不计成功、历史定位、明暗与手机。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
