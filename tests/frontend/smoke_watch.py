"""详细观战浏览器验收：使用构造事件与真实游戏快照，不调用外部模型或评分。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "frontend"))
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from guild_manager_bench.bench.llm.tools import GuildManagerTools


def seed(manager):
    tools = GuildManagerTools.from_data_dir(ROOT / "data" / "presets" / "default")
    session = tools.start_session()
    session_id = session["session_id"]
    before = tools.get_observation(session_id)["observation"]
    candidate = next(c for c in before["recruit_candidates"] if c["can_recruit"])
    result = tools.recruit_adventurer(session_id, candidate["candidate_id"])
    assert result["ok"]
    after = tools.get_observation(session_id)["observation"]
    result = {**result, "_observation_after": after}
    end = tools.end_turn(session_id, [])
    next_observation = tools.get_observation(session_id)["observation"]
    events = [
        {"type": "run_started", "observation": before},
        {"type": "turn_started", "turn": 1, "observation": before},
        {"type": "model_request", "turn": 1, "step": 1, "request": {"messages": [{"role": "user", "content": "构造观战验收输入，不代表实际模型决策"}]}},
        *({"type": "model_delta", "text": "验收 "} for _ in range(800)),
        {"type": "model_response", "turn": 1, "step": 1, "text": "构造流式输出，用于验证历史读取。", "usage": {"input_tokens": 800, "output_tokens": 20}},
        {"type": "tool_result", "turn": 1, "name": "recruit_adventurer", "result": result},
        {"type": "turn_failed", "turn": 1, "error": "构造失败尝试"},
        {"type": "turn_retry", "turn": 1, "reason": "构造回滚记录"},
        {"type": "turn_started", "turn": 1, "observation": before},
        {"type": "model_request", "turn": 1, "step": 1, "request": {"messages": [{"role": "user", "content": "构造观战验收输入，不代表实际模型决策"}]}},
        {"type": "model_response", "turn": 1, "step": 1, "text": "构造验收：招募冒险者后结束回合。", "usage": {"input_tokens": 120, "output_tokens": 30}},
        {"type": "tool_result", "turn": 1, "name": "recruit_adventurer", "result": result},
        {"type": "tool_result", "turn": 1, "name": "end_turn", "result": {**end, "_observation_after": next_observation}},
        {"type": "turn_completed", "turn": 1, "trace": {}},
        {"type": "turn_scored", "turn": 1, "rank_score": 0},  # 构造评分占位，不执行真实评分。
        {"type": "turn_started", "turn": 2, "observation": next_observation},
        {"type": "worker_stopped"},
    ]
    manager.root.mkdir(parents=True, exist_ok=True)
    jobs = manager.create_batch(CreateBatch(name="观战验收样例（非真实评测）", runs=[
        RunSpec(name=f"观战样例 {i + 1}", preset="default", provider="openai", model=f"fixture-{i + 1}", base_url="http://127.0.0.1:1/v1", api_key="fixture-only") for i in range(4)
    ]))["jobs"]
    for job in jobs:
        path = manager.root / job["id"] / "events.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False) + "\n")
        job.update(status="interrupted", phase="idle", turn=2, max_turns=before["max_turns"], observation=next_observation,
                   scores=[{"turn": 1, "value": 0}], last_score=0, cursor=path.stat().st_size, ended_at=timestamp())
        manager._save(job)
    return jobs, before, after


def main():
    directory = ROOT / "runs" / f".smoke-watch-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    jobs, before, after = seed(app.state.workbench)
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
            page = browser.new_page(viewport={"width": 1700, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_role("button", name="历史存档", exact=True).click()
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator(".watch-grid .spectator")).to_have_count(4)
            expect(page.locator(".spectator-viewpoint.is-live")).to_have_count(4)
            panel = page.locator(".watch-grid .spectator").first
            panel.get_by_role("button", name="暂停跟随", exact=True).click()
            expect(panel.get_by_text("历史局面", exact=True)).to_be_visible()
            expect(page.locator(".spectator-viewpoint.is-live")).to_have_count(3)
            page.get_by_role("button", name="对比当前观战曲线", exact=True).click()
            expect(page.get_by_role("region", name="统一曲线对比工作区", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回原工作区", exact=True).click()
            expect(panel.get_by_text("历史局面", exact=True)).to_be_visible()
            expect(page.locator(".spectator-viewpoint.is-live")).to_have_count(3)
            panel.get_by_role("button", name="专注查看", exact=True).click()
            expect(page.get_by_role("button", name="返回多路观战", exact=True)).to_be_visible()
            expect(page.get_by_text("历史局面", exact=True)).to_be_visible()
            attempts = page.request.get(base + f"/api/workbench/jobs/{jobs[-1]['id']}/watch").json()["attempts"]
            retry = next(a for a in attempts if a["turn"] == 1 and a["attempt"] == 2)
            page.get_by_label("选择观战回合", exact=True).select_option(str(retry["id"]))
            expect(page.get_by_text("回合 1 / 尝试 2", exact=True)).to_be_visible()
            page.get_by_role("button", name="展开最近行动", exact=True).click()
            timeline = page.get_by_role("region", name="观战行动时间线", exact=True)
            expect(timeline.locator(".action-group.is-system").filter(has_text="回合开始")).to_have_attribute("open", "")
            timeline.get_by_role("button", name="回合开始", exact=False).click()
            resources = page.get_by_label("当前观看位置的资源", exact=True)
            expect(resources.get_by_text(str(before["gold"]), exact=True)).to_be_visible()
            page.get_by_role("button", name="返回总览", exact=True).click()
            expect(page.get_by_role("region", name="编队概览", exact=True).get_by_text("此时尚未招募冒险者", exact=True)).to_be_visible()
            page.get_by_role("button", name="展开最近行动", exact=True).click()
            timeline.get_by_role("button", name=f"定位行动 招募 {after['adventurers'][0]['name']}", exact=True).click()
            expect(resources.get_by_text(str(after["gold"]), exact=True)).to_be_visible()
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name=f"查看成员 {after['adventurers'][0]['name']}", exact=True).click()
            expect(page.locator(".scene-inline-inspector .paperdoll")).to_be_visible()
            expect(page.get_by_role("dialog")).to_have_count(0)
            page.get_by_role("button", name="返回总览", exact=True).click()
            page.get_by_role("button", name="展开模型输出", exact=True).click()
            page.get_by_text("完整模型输入", exact=True).click()
            expect(page.get_by_text('构造观战验收输入，不代表实际模型决策', exact=False)).to_be_visible()
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "watch-focus-dark.png"), full_page=True)
            page.get_by_role("button", name="浅色", exact=True).click()
            page.screenshot(path=str(directory / "watch-focus-light.png"), full_page=True)
            page.get_by_role("button", name="返回多路观战", exact=True).click()
            expect(page.locator(".watch-grid .spectator")).to_have_count(4)
            panel = page.locator(".watch-grid .spectator").first
            expect(panel.get_by_text("历史局面", exact=True)).to_be_visible()
            expect(panel.get_by_text(str(after["gold"]), exact=True)).to_be_visible()
            page.screenshot(path=str(directory / "watch-four.png"), full_page=True)
            page.reload()
            expect(page.locator(".watch-grid .spectator")).to_have_count(4)
            # 最早失败尝试也可读取，超过旧在线窗口的 800 条碎片已折叠。
            rolled_back = next(a for a in attempts if a["status"] == "rolled_back")
            panel = page.locator(".watch-grid .spectator").first
            panel.get_by_label("选择观战回合", exact=True).select_option(str(rolled_back["id"]))
            expect(panel.locator(".monitor-warning")).to_have_text("本次尝试已回滚，不代表当前局面")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(directory / "watch-mobile.png"), full_page=True)
            panel.get_by_role("button", name="专注查看", exact=True).click()
            expect(page.get_by_text("历史局面", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(directory / "watch-focus-mobile.png"), full_page=True)
            browser.close()
        if "--lighthouse" in sys.argv:
            report_path = directory / "lighthouse-watch.json"
            environment = {**os.environ, "CHROME_PATH": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"}
            # 直接调用 Node 的 npx 入口，避免 Windows .cmd 将 URL 中的 & 当成命令分隔符。
            npx_cli = Path(shutil.which("npx")).parent / "node_modules/npm/bin/npx-cli.js"
            subprocess.run([shutil.which("node"), str(npx_cli), "--yes", "lighthouse", base + f"/?view=focus&run={jobs[0]['id']}", "--preset=desktop", "--chrome-flags=--headless", "--quiet", "--only-categories=performance,accessibility,best-practices", "--output=json", f"--output-path={report_path}"], check=True, timeout=120, env=environment)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            print("观战 Lighthouse:", {name: value["score"] for name, value in report["categories"].items()})
        assert not errors, errors
        print(f"详细观战验收通过：四路独立跟随、历史局面、专注往返、完整输入、回滚尝试、刷新、明暗主题和窄屏。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
