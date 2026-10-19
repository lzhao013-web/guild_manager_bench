"""行动表达验收：通过真实游戏操作生成构造日志，不请求模型或运行评测评分。"""
from __future__ import annotations

from dataclasses import replace
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
sys.path.insert(0, str(ROOT / "tests/frontend"))
from smoke_multiview import assert_space
from smoke_watch import seed
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.game.state import MATERIAL_NAMES


def action_events():
    tools = GuildManagerTools.from_data_dir(ROOT / "data/presets/advanced")
    session = tools.start_session()["session_id"]
    tools._sessions[session].state = replace(tools.get_state(session), gold=5000, experience_pool=1000, materials={key: 200 for key in MATERIAL_NAMES})
    observation = lambda: tools.get_observation(session)["observation"]
    initial = observation()
    events = [{"type": "run_started", "observation": initial}, {"type": "turn_started", "turn": 1, "observation": initial}]
    summaries = {}

    def call(name, arguments, invoke):
        call_id = f"fixture-{len(events)}"
        events.append({"type": "tool_call", "turn": 1, "name": name, "arguments": arguments, "call_id": call_id})
        result = invoke()
        if result.get("ok") and not name.startswith("get_"):
            result = {**result, "_observation_after": observation()}
        events.append({"type": "tool_result", "turn": 1, "name": name, "arguments": arguments, "call_id": call_id, "result": result})
        if result.get("ok") and result.get("event", {}).get("summary"):
            summaries[name] = result["event"]["summary"]
        return result

    call("get_party", {}, lambda: tools.get_party(session))
    call("get_recruitment", {}, lambda: tools.get_recruitment(session))
    candidate = next(c for c in initial["recruit_candidates"] if c["template_id"] == "mercenary_warrior")
    call("recruit_adventurer", {"candidate_id": candidate["candidate_id"]}, lambda: tools.recruit_adventurer(session, candidate["candidate_id"]))
    member = observation()["adventurers"][0]
    member_id = member["adventurer_id"]
    call("get_party", {}, lambda: tools.get_party(session))
    call("get_party", {}, lambda: tools.get_party(session))
    call("allocate_experience", {"adventurer_id": member_id, "amount": 100}, lambda: tools.allocate_experience(session, member_id, 100))
    recipe = next(r for r in observation()["crafting_recipes"] if r["recipe_id"] == "recipe_t1_heavy_sword")
    call("craft_equipment", {"recipe_id": recipe["recipe_id"]}, lambda: tools.craft_equipment(session, recipe["recipe_id"]))
    item = observation()["equipment_inventory"][0]
    call("equip_item", {"adventurer_id": member_id, "equipment_id": item["instance_id"]}, lambda: tools.equip_item(session, member_id, item["instance_id"]))
    upgrade = next(u for u in observation()["global_upgrades"] if u["upgrade_id"] == "training_grounds")
    call("purchase_upgrade", {"upgrade_id": upgrade["upgrade_id"]}, lambda: tools.purchase_upgrade(session, upgrade["upgrade_id"]))
    # 佣兵战士只有被动技能，另招募有主动技能的职业验证战术变化。
    caster = next(c for c in observation()["recruit_candidates"] if c["template_id"] == "bloodfiend")
    call("recruit_adventurer", {"candidate_id": caster["candidate_id"]}, lambda: tools.recruit_adventurer(session, caster["candidate_id"]))
    tactician = observation()["adventurers"][-1]
    skill = tactician["tactics"]["available_active_skills"][0]
    call("configure_tactics", {"adventurer_id": tactician["adventurer_id"], "skill_priority": [], "disabled_skills": [skill["skill_id"]]}, lambda: tools.configure_tactics(session, tactician["adventurer_id"], [], [skill["skill_id"]]))
    failed = call("equip_item", {"adventurer_id": member_id, "equipment_id": "missing-fixture-item"}, lambda: tools.equip_item(session, member_id, "missing-fixture-item"))
    assert failed["ok"] is False
    monster = observation()["monsters"][0]
    call("end_turn", {"hunts": [{"adventurer_id": member_id, "monster_id": monster["monster_id"]}]}, lambda: tools.end_turn(session, [{"adventurer_id": member_id, "monster_id": monster["monster_id"]}]))
    events.append({"type": "worker_stopped"})
    assert all(name in summaries for name in ("recruit_adventurer", "allocate_experience", "craft_equipment", "equip_item", "purchase_upgrade", "configure_tactics", "end_turn")), summaries
    return initial, observation(), events, summaries, member["name"], monster["name"]


def main():
    directory = ROOT / "runs" / f".smoke-actions-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    jobs, _, _ = seed(app.state.workbench)
    before, after, events, summaries, member_name, monster_name = action_events()
    for index, job in enumerate(jobs):
        job["name"] = f"行动表达验收 {index + 1}（构造）"
        job["config"]["preset"] = "advanced"
        records = events if index == 0 else [
            {"type": "run_started", "observation": before}, {"type": "turn_started", "turn": 1, "observation": before},
            *({"type": "tool_result", "name": "get_party", "turn": 1, "result": {"ok": True, "adventurers": []}} for _ in range(3)),
            {"type": "worker_stopped"},
        ]
        path = app.state.workbench.root / job["id"] / "events.jsonl"
        path.write_text("".join(json.dumps({"at": timestamp(), "event": e}, ensure_ascii=False) + "\n" for e in records), encoding="utf-8")
        job.update(cursor=path.stat().st_size, observation=after if index == 0 else before, turn=after["turn"] if index == 0 else before["turn"], max_turns=before["max_turns"], scores=[], last_score=None)
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
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/?view=focus&run={jobs[0]['id']}")
            actions = page.get_by_role("region", name="最近行动概览", exact=True)
            expect(actions.get_by_text(summaries["end_turn"], exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "overview-light.png"), full_page=True)
            page.get_by_role("button", name="展开最近行动", exact=True).click()
            timeline = page.get_by_role("region", name="观战行动时间线", exact=True)
            detail = page.get_by_role("region", name="所选行动详情", exact=True)
            for name in ("recruit_adventurer", "craft_equipment", "equip_item", "purchase_upgrade"):
                row = timeline.get_by_role("button", name=f"定位行动 {summaries[name]}", exact=True)
                expect(row).to_have_count(1)
                row.click()
                expect(detail.get_by_role("heading", name=summaries[name], exact=True)).to_be_visible()
            expect(detail.get_by_role("heading", name="实际局面变化", exact=True)).to_be_visible()
            expect(detail.get_by_text("攻击", exact=False).first).to_be_visible()
            expect(page.get_by_text("历史局面", exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "purchase-light.png"), full_page=True)
            timeline.locator(".action-entry").filter(has_text="配置主动技能").click()
            expect(detail).to_contain_text("停用技能")
            timeline.locator(".action-entry.failed").click()
            expect(detail.locator(".action-detail-warning")).to_be_visible()
            queries = timeline.locator("details.is-query")
            expect(queries).to_have_count(2)
            expect(queries.first).not_to_have_attribute("open", "")
            queries.last.locator("summary").click()
            expect(queries.last.get_by_role("button", name="定位行动 查看队伍", exact=True)).to_have_count(2)
            queries.last.locator("summary").click()
            timeline.get_by_role("button", name=f"定位行动 {summaries['end_turn']}", exact=True).click()
            expect(detail.get_by_text(f"{member_name} → {monster_name}：", exact=False).first).to_be_visible()
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "battle-dark.png"), full_page=True)
            for width, height in [(1280, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(directory / f"timeline-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base + f"/?view=focus&run={jobs[1]['id']}")
            expect(page.get_by_role("region", name="最近行动概览", exact=True)).to_contain_text("目前只进行了查询，尚无经营动作")
            page.get_by_role("button", name="展开最近行动", exact=True).click()
            expect(page.get_by_text("目前只进行了 3 次查询，尚无经营动作", exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "queries-only.png"), full_page=True)
            page.goto(base + "/?section=archives")
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator(".watch-grid .monitor-previews")).to_have_count(4)
            for width, height in [(1280, 720), (1440, 900)]:
                page.set_viewport_size({"width": width, "height": height})
                assert_space(page, 4)
            page.screenshot(path=str(directory / "four-dark.png"), full_page=True)
            assert all(job["status"] == "interrupted" for job in app.state.workbench.snapshot()["jobs"])
            browser.close()
        assert not errors, errors
        print(f"行动表达验收通过：真实招募、装备、升级、战术、失败和讨伐日志，合并调用结果，连续查询折叠，历史定位，明暗与窄屏，多路不溢出。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
