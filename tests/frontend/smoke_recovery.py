"""分支重跑及跨版本确认的浏览器验收，只使用本地假模型。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
from test_workbench import model_server
from test_run_recovery import EndAgent
from smoke_workbench import wait_until
from guild_manager_bench.api.app import create_app
from guild_manager_bench.bench.llm import LlmRunConfig, run_llm_game, runner
from guild_manager_bench.bench import metrics


def main():
    directory = ROOT / "runs" / f".smoke-recovery-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    manager = app.state.workbench
    runner.ProcessPoolExecutor = ThreadPoolExecutor
    metrics.ProcessPoolExecutor = ThreadPoolExecutor
    errors = []
    with model_server() as (url, release, calls):
        original = run_llm_game(EndAgent(), data_dir=ROOT / "data/presets/default", config=LlmRunConfig(archive_dir=manager.archive_root))
        parent = Path(original.archive_dir)
        previous = set(manager.archive_root.iterdir())
        try:
            run_llm_game(EndAgent(3), data_dir=ROOT / "data/presets/default", config=LlmRunConfig(archive_dir=manager.archive_root))
        except RuntimeError as exc:
            assert str(exc) == "fixture stop"
        interrupted = (set(manager.archive_root.iterdir()) - previous).pop()
        for source in (parent, interrupted):
            path = source / "replay.json"
            replay = json.loads(path.read_text(encoding="utf-8"))
            replay["data"]["protocol_hash"] = "fixture-previous-protocol"
            replay["agent"]["config"] = {"model": "本地构造来源（非真实评测）", "base_url": url, "timeout": 30}
            path.write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
        preserved = {path: path.read_bytes() for path in (parent / "replay.json", parent / "trace.jsonl")}
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            wait_until(lambda: server.started)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/?section=archives")
                row = page.locator(".archive-table tbody tr").filter(has_text=parent.name)
                expect(row.get_by_role("button", name="续跑", exact=True)).to_have_count(0)
                row.get_by_role("button", name="从指定回合重跑", exact=True).click()
                expect(page.get_by_role("dialog", name="从指定回合分支重跑", exact=True)).to_be_visible()
                page.get_by_label("重跑起始回合", exact=True).fill("3")
                page.get_by_label("模型名称", exact=True).fill("fast-rerun-browser")
                page.get_by_label("API Key", exact=True).fill("recovery-fixture-secret")
                page.get_by_role("button", name="加入队列", exact=True).click()
                warning = page.get_by_role("alertdialog", name="存档版本与当前版本不一致", exact=True)
                expect(warning).to_be_visible()
                expect(warning).to_contain_text("fixture-previous-protocol")
                expect(warning).to_contain_text("从 T3 分支")
                assert not manager.jobs and calls == []
                page.screenshot(path=str(directory / "version-confirmation.png"), animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                expect(warning.get_by_role("button", name="确认风险，强行执行", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(directory / "version-confirmation-mobile.png"), animations="disabled")
                warning.get_by_role("button", name="取消", exact=True).click()
                expect(warning).not_to_be_visible()
                assert not manager.jobs and calls == []
                assert all(path.read_bytes() == raw for path, raw in preserved.items())
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="加入队列", exact=True).click()
                warning.get_by_role("button", name="确认风险，强行执行", exact=True).click()
                wait_until(lambda: len(manager.jobs) == 1 and all(job["status"] in {"completed", "failed"} for job in manager.jobs.values()), timeout=60)
                branch = next(iter(manager.jobs.values()))
                assert branch["status"] == "completed", branch["error"]
                assert branch["archive_id"] != parent.name and branch["config"]["rerun_from_turn"] == 3
                assert branch["data"]["protocol_status"] == "mixed" and len(calls) == 6
                assert all(path.read_bytes() == raw for path, raw in preserved.items())
                assert "recovery-fixture-secret" not in page.evaluate("JSON.stringify(localStorage)")
                page.goto(base + "/?section=archives")
                expect(page.locator(".archive-table tbody tr")).to_have_count(3)
                child_row = page.locator(".archive-table tbody tr").filter(has_text=branch["archive_id"])
                expect(child_row).to_contain_text("从 T3 分支")
                expect(child_row).to_contain_text("跨版本执行")
                page.screenshot(path=str(directory / "rerun-history.png"), full_page=True)
                # 未完成来源仍走原地续跑，确认取消同样不请求模型。
                source_row = page.locator(".archive-table tbody tr").filter(has_text=interrupted.name)
                source_row.get_by_role("button", name="续跑", exact=True).click()
                page.get_by_label("模型名称", exact=True).fill("fast-resume-browser")
                page.get_by_label("API Key", exact=True).fill("resume-fixture-secret")
                page.get_by_role("button", name="加入队列", exact=True).click()
                expect(warning).to_be_visible()
                warning.get_by_role("button", name="取消", exact=True).click()
                assert len(manager.jobs) == 1 and len(calls) == 6
                page.get_by_role("button", name="加入队列", exact=True).click()
                warning.get_by_role("button", name="确认风险，强行执行", exact=True).click()
                wait_until(lambda: len(manager.jobs) == 2 and all(job["status"] in {"completed", "failed"} for job in manager.jobs.values()), timeout=60)
                resumed = next(job for job in manager.jobs.values() if job["id"] != branch["id"])
                assert resumed["status"] == "completed", resumed["error"]
                assert resumed["archive_id"] == interrupted.name and resumed["data"]["protocol_status"] == "mixed"
                assert len(calls) == 11
                assert all(path.read_bytes() == raw for path, raw in preserved.items())
                replay = json.loads((interrupted / "replay.json").read_text(encoding="utf-8"))
                assert replay["resume_history"][-1]["forced_version"] is True
                assert "resume-fixture-secret" not in page.evaluate("JSON.stringify(localStorage)")
                browser.close()
            assert not errors, errors
            print(f"恢复验收通过：已完成存档分支、原记录不变、起始回合、取消零请求、版本确认、跨版本续跑、来源与版本标记、桌面与手机。截图：{directory}")
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            sock.close()
            os.chdir(ROOT)


if __name__ == "__main__":
    main()
