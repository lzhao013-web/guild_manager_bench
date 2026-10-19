"""多路观战空间验收：六人构造局面、长输出、查询与失败，不调用模型或评分。"""
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
from smoke_scene import rich_snapshot
from smoke_watch import seed
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.api.workbench import timestamp


def assert_space(page, count, *, allow_page_scroll=False):
    expect(page.locator(".watch-grid .watch-monitor")).to_have_count(count)
    problems = page.evaluate("""allowPageScroll => {
        const issues = [];
        if (!allowPageScroll && document.scrollingElement.scrollHeight > innerHeight + 1) issues.push('页面纵向溢出');
        if (document.scrollingElement.scrollWidth > innerWidth) issues.push('页面横向溢出');
        for (const selector of ['.watch-workspace', '.watch-grid', '.watch-monitor', '.monitor-body', '.monitor-party', '.monitor-monsters', '.monitor-actions', '.monitor-previews', '.monitor-preview-grid', '.monitor-preview-card', '.business-rows', '.monitor-controls']) {
            for (const node of document.querySelectorAll(selector)) {
                if (allowPageScroll && selector === '.watch-workspace') continue;
                if (node.scrollHeight > node.clientHeight + 1) issues.push(`${selector} 内容纵向溢出 ${node.scrollHeight - node.clientHeight}px`);
                if (node.scrollWidth > node.clientWidth + 1) issues.push(`${selector} 内容横向溢出 ${node.scrollWidth - node.clientWidth}px`);
            }
        }
        for (const panel of document.querySelectorAll('.watch-monitor')) {
            const box = panel.getBoundingClientRect();
            if (!allowPageScroll && (box.top < 0 || box.bottom > innerHeight + 1)) issues.push('观战面板未完整进入视口');
            const body = panel.querySelector('.monitor-body')?.getBoundingClientRect();
            const footer = panel.querySelector('.monitor-controls').getBoundingClientRect();
            if (panel.querySelector('.monitor-model')?.getBoundingClientRect().height > 52) issues.push('模型摘要超过两行预算');
            for (const node of panel.querySelectorAll('.monitor-member, .monitor-current, .monitor-model')) {
                const rect = node.getBoundingClientRect();
                if (rect.top < body.top - 1 || rect.bottom > footer.top + 1) issues.push(`${node.className} 被裁切`);
            }
        }
        return issues;
    }""", allow_page_scroll)
    if problems:
        page.screenshot(path="multiview-space-failure.png", full_page=True)
        sizes = page.locator(".watch-monitor").first.evaluate("""panel => Object.fromEntries(['.monitor-header', '.monitor-metrics', '.monitor-body', '.monitor-party', '.monitor-monsters', '.monitor-actions', '.monitor-model', '.monitor-controls'].map(selector => { const n = panel.querySelector(selector); return [selector, {height: n.getBoundingClientRect().height, content: n.scrollHeight}]; }))""")
        raise AssertionError({"viewport": page.viewport_size, "count": count, "problems": problems, "first_panel": sizes})


def main():
    directory = ROOT / "runs" / f".smoke-multiview-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    jobs, _, _ = seed(app.state.workbench)
    before, after = rich_snapshot()
    events = [
        {"type": "run_started", "observation": before},
        {"type": "turn_started", "turn": after["turn"], "observation": after},
        {"type": "model_request", "turn": after["turn"], "step": 1, "request": {"messages": [{"role": "user", "content": "构造多路空间验收输入"}]}},
        {"type": "model_response", "turn": after["turn"], "step": 1, "text": "构造模型输出，不代表实际模型决策。" * 100},
        {"type": "tool_result", "turn": after["turn"], "name": "equip_item", "result": {"ok": False, "error": "构造装备失败：用于检查失败摘要不被查询覆盖。" * 10}},
        *({"type": "tool_result", "turn": after["turn"], "name": "get_party", "content": "构造队伍查询", "result": {"ok": True}} for _ in range(20)),
        {"type": "worker_stopped"},
    ]
    for i, job in enumerate(jobs):
        job["name"] = f"六人多路样例 {i + 1}（构造）"
        job["config"]["preset"] = "full"
        path = app.state.workbench.root / job["id"] / "events.jsonl"
        path.write_text("".join(json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
        job.update(observation=after, turn=after["turn"], max_turns=after["max_turns"], cursor=path.stat().st_size)
        if i == 0:
            job.update(status="failed", error="构造模型接口异常，用于确认长错误信息不挤占局面。" * 10)
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
            page.goto(base + "/?section=archives")
            page.get_by_role("checkbox", name="选择当前筛选的全部历史 run", exact=True).check()
            page.get_by_role("button", name="观战选中", exact=True).click()
            expect(page.locator(".monitor-member")).to_have_count(24)
            for width, height in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
                page.set_viewport_size({"width": width, "height": height})
                assert_space(page, 4)
                page.screenshot(path=str(directory / f"four-{width}x{height}.png"), full_page=True)
            for width, height in [(1024, 600), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert_space(page, 4, allow_page_scroll=True)
                page.screenshot(path=str(directory / f"four-small-{width}x{height}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("button", name="深色", exact=True).click()
            page.screenshot(path=str(directory / "four-dark.png"), full_page=True)
            panel = page.locator(".watch-monitor").first
            expect(panel.locator(".monitor-actions .failed")).to_be_visible()
            member = after["adventurers"][0]
            panel.get_by_role("button", name=f"查看成员 {member['name']}", exact=True).click()
            expect(page.locator(".scene-inline-inspector .paperdoll")).to_be_visible()
            expect(page.get_by_role("button", name="返回多路观战", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回多路观战", exact=True).click()
            panel = page.locator(".watch-monitor").first
            panel.get_by_role("button", name="查看完整模型输出", exact=True).click()
            expect(page.get_by_role("heading", name="模型输出模块标题", exact=True)).to_be_visible()
            expect(page.locator(".watch-model-text")).to_contain_text("构造模型输出")
            page.get_by_role("button", name="返回多路观战", exact=True).click()
            for count in [3, 2, 1]:
                page.locator(".watch-monitor").last.get_by_role("button", name="关闭详情", exact=True).click()
                page.set_viewport_size({"width": 1280, "height": 720})
                assert_space(page, count)
                page.screenshot(path=str(directory / f"count-{count}.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert_space(page, 1, allow_page_scroll=True)
            page.screenshot(path=str(directory / "mobile.png"), full_page=True)
            browser.close()
        assert not errors, errors
        print(f"多路空间验收通过：六人四路无嵌套滚动、1/2/3/4 路视口适配、长输出与异常摘要、直达人物和模型模块、明暗主题及手机。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
