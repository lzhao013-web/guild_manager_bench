"""API 配置的浏览器验收。只使用本地假 API 和内存凭证库。

uv run --with playwright python -X utf8 tests/frontend/smoke_api_profiles.py
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from pathlib import Path
import socket
import sys
import threading
import time
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_api_profiles import MemoryVault, catalog_server
from guild_manager_bench.api.api_profiles import ApiProfiles
from guild_manager_bench.api.app import create_app


def wait_until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("等待 API 配置验收状态超时")


def main():
    directory = ROOT / "runs" / f".smoke-api-profiles-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    m = app.state.workbench
    vault = MemoryVault()
    m.api_profiles = ApiProfiles(m.root, vault)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors = []
    try:
        wait_until(lambda: server.started)
        with catalog_server() as (url, requests), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_role("button", name="API 配置", exact=True).click()
            page.get_by_role("button", name="新增 API 配置", exact=True).first.click()
            page.get_by_label("API 配置名称", exact=True).fill("浏览器验收 API（示例）")
            page.get_by_label("服务地址", exact=True).fill(url + "/v1")
            page.get_by_label("API Key", exact=True).fill("fake-secret")
            page.get_by_role("button", name="获取模型", exact=True).click()
            expect(page.get_by_text("API 返回 2 个模型", exact=False)).to_be_visible()
            page.get_by_label("模型名称", exact=True).fill("discovered-model")
            page.screenshot(path=str(directory / "api-model-catalog.png"), full_page=True)
            page.get_by_role("button", name="保存 API 配置", exact=True).click()
            row = page.locator(".api-profile-table tbody tr")
            expect(row).to_have_count(1)
            metadata = page.request.get(base + "/api/workbench/api-profiles")
            assert "fake-secret" not in metadata.text()
            profile_id = metadata.json()["profiles"][0]["id"]
            assert "fake-secret" not in page.evaluate("JSON.stringify(localStorage)")
            row.get_by_role("button", name="新建实验", exact=True).click()
            expect(page.get_by_role("combobox", name="API 配置", exact=True)).to_have_value(
                profile_id
            )
            assert page.get_by_label("API Key", exact=True).count() == 0
            expect(page.get_by_label("模型名称", exact=True)).to_have_value("discovered-model")
            expect(page.get_by_label("批次名称", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="保存为模板", exact=True)).to_have_count(0)
            page.get_by_role("button", name="加入队列", exact=True).click()
            wait_until(
                lambda: (
                    page.request.get(base + "/api/workbench").json()["jobs"][0]["status"]
                    == "completed"
                )
            )
            job = page.request.get(base + "/api/workbench").json()["jobs"][0]
            assert job["config"]["profile_id"] == profile_id
            assert job["config"]["model"] == "discovered-model"
            assert all(
                r.get("headers", {}).get("Authorization") == "Bearer fake-secret" for r in requests
            )
            assert "fake-secret" not in (m.root / "api_profiles.json").read_text(encoding="utf-8")
            assert "fake-secret" not in page.evaluate("JSON.stringify(localStorage)")
            if page.get_by_role("button", name="关闭详情", exact=True).count():
                page.get_by_role("button", name="关闭详情", exact=True).click()
            page.get_by_role("button", name="API 配置", exact=True).click()
            page.locator(".api-profile-table tbody tr").get_by_role(
                "button", name="编辑 / 模型", exact=True
            ).click()
            key_input = page.get_by_label("API Key", exact=True)
            expect(key_input).to_have_value("fake-secret")
            expect(key_input).to_have_attribute("type", "text")
            expect(page.get_by_role("button", name="获取模型", exact=True)).to_be_enabled()
            expect(page.get_by_role("checkbox")).to_have_count(0)
            page.get_by_role("dialog").evaluate(
                "node => Promise.all(node.getAnimations().map(animation => animation.finished))"
            )
            page.screenshot(path=str(directory / "api-key-editor.png"), full_page=True)
            page.get_by_label("API 配置名称", exact=True).fill("改名后的 API 配置")
            page.get_by_role("button", name="保存 API 配置", exact=True).click()
            expect(page.locator(".api-profile-table tbody tr")).to_contain_text("改名后的 API 配置")
            assert list(vault.values.values()) == ["fake-secret"]
            page.locator(".api-profile-table tbody tr").get_by_role("button", name="编辑 / 模型", exact=True).click()
            expect(key_input).to_have_value("fake-secret")
            key_input.fill("replacement-fake-key")
            expect(page.get_by_role("button", name="获取模型", exact=True)).to_be_disabled()
            page.get_by_role("button", name="保存 API 配置", exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert list(vault.values.values()) == ["replacement-fake-key"]
            page.reload()
            expect(page.get_by_role("heading", name="API 配置", exact=True)).to_be_visible()
            page.get_by_role("button", name="API 配置", exact=True).click()
            expect(page.locator(".api-profile-table tbody tr")).to_contain_text("改名后的 API 配置")
            page.screenshot(path=str(directory / "api-profiles-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(directory / "api-profiles-mobile.png"), full_page=True)
            page.locator(".api-profile-table tbody tr").get_by_role("button", name="编辑 / 模型", exact=True).click()
            expect(key_input).to_have_value("replacement-fake-key")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            key_input.fill("")
            page.get_by_role("button", name="保存 API 配置", exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert vault.values == {}
            expect(page.locator(".api-profile-table tbody tr")).to_contain_text("使用环境变量")
            metadata = page.request.get(base + "/api/workbench/api-profiles")
            assert "replacement-fake-key" not in metadata.text()
            stored = page.evaluate("JSON.stringify([localStorage, sessionStorage])")
            assert "fake-secret" not in stored and "replacement-fake-key" not in stored
            assert all(not any(secret in path.read_text(encoding="utf-8") for secret in ("fake-secret", "replacement-fake-key")) for path in (m.root / "api_profiles.json", m.root / job["id"] / "state.json", m.root / job["id"] / "events.jsonl"))
            page.get_by_role("button", name="删除 API 配置 改名后的 API 配置", exact=True).click()
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(page.locator(".api-profile-table tbody tr")).to_have_count(0)
            assert vault.values == {}
            assert (
                page.request.get(base + "/api/workbench").json()["jobs"][0]["status"] == "completed"
            )
            browser.close()
        if "--lighthouse" in sys.argv:
            report_path = directory / "lighthouse-api-profiles.json"
            subprocess.run(
                [
                    shutil.which("npx"),
                    "--yes",
                    "lighthouse",
                    base + "/?section=apis",
                    "--preset=desktop",
                    "--chrome-flags=--headless",
                    "--quiet",
                    "--only-categories=performance,accessibility,best-practices",
                    "--output=json",
                    f"--output-path={report_path}",
                ],
                check=True,
                timeout=100,
                env={
                    **os.environ,
                    "CHROME_PATH": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                },
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            print(
                "API 配置 Lighthouse:",
                {name: category["score"] for name, category in report["categories"].items()},
            )
        assert not errors, errors
        print(
            f"API 配置浏览器验收通过：保存、模型目录、复用运行、列表不含密钥、明文编辑 Key、刷新后保留、清空和删除、无持久明文及窄屏。截图：{directory}"
        )
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == "__main__":
    main()
