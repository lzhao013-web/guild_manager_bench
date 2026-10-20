"""怪物分组和模型用量浏览器验收。构造局面与调用数据，不请求模型、不重新评分。"""
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
sys.path.insert(0, str(ROOT / "tests/frontend"))
from smoke_scene import rich_snapshot, assert_overview_space
from smoke_watch import seed
from smoke_multiview import assert_space
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp
from guild_manager_bench.game import engine
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.observation import _monster_to_dict


def main():
    directory = ROOT / "runs" / f".smoke-monster-models-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    manager = app.state.workbench
    jobs, _, _ = seed(manager)
    before, after = rich_snapshot()
    definition = load_game_definition(ROOT / "data/presets/advanced")
    state = engine.new_game(definition)
    monsters = []
    for index, (archetype_id, tier) in enumerate((("slime", "normal"), ("orc_captain", "elite"), ("lich", "boss"), ("iron_colossus", "challenge")), 1):
        collection = definition.content.challenge_archetypes if tier == "challenge" else definition.content.monster_archetypes
        archetype = next(item for item in collection if item.archetype_id == archetype_id)
        rules = definition.rules.monster_spawn
        config = getattr(rules, tier) if tier in ("elite", "boss") else None
        monster = next(item for item in engine.spawn_monsters(definition, 30) if item.archetype_id == archetype_id) if tier == "challenge" else engine._spawn_monster(archetype, 30, index, 1, 1, tier, config)
        monsters.append(_monster_to_dict(definition, state, monster))
    after = {**after, "turn": 30, "monsters": monsters}
    events = [
        {"type": "run_started", "observation": before},
        {"type": "turn_started", "turn": 30, "observation": after},
        {"type": "model_request", "turn": 30, "step": 1},
        {"type": "model_response", "turn": 30, "step": 1, "text": "构造模型输出，用于检查调用统计。不是实际模型决策。", "usage": {"input_tokens": 1200, "output_tokens": 130, "prompt_tokens_details": {"cached_tokens": 800}, "completion_tokens_details": {"reasoning_tokens": 90}}, "timing": {"duration_ms": 2500}, "tool_calls": [{"name": "get_party"}]},
        {"type": "model_request", "turn": 30, "step": 2},
        {"type": "model_response", "turn": 30, "step": 2, "text": "第二次调用的构造正文。", "usage": {"input_tokens": 40, "output_tokens": 0}, "timing": {"duration_ms": 900}, "tool_calls": []},
        {"type": "worker_stopped"},
    ]
    for index, job in enumerate(jobs):
        path = manager.root / job["id"] / "events.jsonl"
        path.write_text("".join(json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
        job["config"]["preset"] = "advanced"
        job.update(name=f"怪物与调用信息 {index + 1}（构造）", observation=after, turn=30, max_turns=35, cursor=path.stat().st_size)
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
            page.emulate_media(reduced_motion="reduce")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/?view=focus&run={jobs[-1]['id']}")
            expect(page.locator(".overview-enemies .monster-badges")).to_have_count(4)
            for label in ("T1 · 普通", "T2 · 精英", "T3 · 首领", "固定挑战"):
                expect(page.get_by_role("group", name=label, exact=True)).to_be_visible()
            stats = page.get_by_role("group", name="本次模型调用概览", exact=True)
            expect(stats).to_contain_text("输入 40")
            expect(stats).to_contain_text("输出 0")
            expect(stats).to_contain_text("900 ms")
            for width, height in ((1440, 900), (1280, 720)):
                page.set_viewport_size({"width": width, "height": height})
                assert_overview_space(page)
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "overview-dark.png"), full_page=True)
            page.get_by_role("button", name="展开本回合怪物", exact=True).click()
            cards = page.locator(".scene-monster-card")
            expect(cards).to_have_count(4)
            for index, text in enumerate(("T1", "T2", "T3", "挑战")):
                expect(cards.nth(index).locator(".monster-stage")).to_have_text(text)
            page.screenshot(path=str(directory / "monsters-dark.png"), full_page=True)
            cards.nth(2).click()
            expect(page.locator(".scene-inline-inspector").get_by_role("group", name="T3 · 首领", exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "monster-detail-dark.png"), full_page=True)
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开模型输出", exact=True).click()
            selector = page.get_by_label("选择模型调用", exact=True)
            selector.select_option(index=0)
            expect(stats).to_contain_text("输入 1,200")
            expect(stats).to_contain_text("输出 130")
            expect(stats).to_contain_text("耗时 2.5 秒")
            expect(stats).to_contain_text("缓存 800")
            expect(stats).to_contain_text("推理 90")
            expect(stats).to_contain_text("工具 1")
            page.screenshot(path=str(directory / "model-call-dark.png"), full_page=True)
            selector.select_option(index=1)
            expect(stats).to_contain_text("输出 0")
            expect(stats).not_to_contain_text("缓存 800")
            page.get_by_role("button", name="浅色", exact=True).click()
            selector.select_option(index=0)
            page.screenshot(path=str(directory / "model-call-light.png"), full_page=True)
            page.goto(base + "/?section=archives")
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator(".monitor-monsters .monster-badges")).to_have_count(16)
            expect(page.locator(".monitor-model .model-call-stats")).to_have_count(4)
            for width, height in ((1280, 720), (1440, 900)):
                page.set_viewport_size({"width": width, "height": height})
                assert_space(page, 4)
                page.screenshot(path=str(directory / f"four-{width}x{height}.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert_space(page, 4, allow_page_scroll=True)
            page.screenshot(path=str(directory / "four-mobile.png"), full_page=True)
            browser.close()
        assert not errors, errors
        assert not manager._processes
        print(f"怪物与模型概览验收通过：四类阶级与普通/精英/首领、单局/详情/四路、调用独立统计与零值、明暗主题、1280×720 与手机。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
