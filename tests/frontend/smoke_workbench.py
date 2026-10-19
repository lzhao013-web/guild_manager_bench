"""真实浏览器和隔离进程的工作台验收，不调用外部模型。

uv run --with playwright python -X utf8 tests/frontend/smoke_workbench.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_workbench import model_server
from guild_manager_bench.api.app import create_app


def wait_until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError("等待运行状态超时")


def main():
    directory = ROOT / "runs" / f".smoke-workbench-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    os.chdir(directory)
    app = create_app(ROOT / "data")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    errors = []
    try:
        wait_until(lambda: server.started)
        base = f"http://127.0.0.1:{port}"
        with model_server() as (url, release, calls), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            expect(page.get_by_role("heading", name="运行中心", exact=True)).to_be_visible()
            expect(page.get_by_text("从第一组实验开始", exact=True)).to_be_visible()
            page.get_by_role("button", name="新建实验", exact=True).first.click()
            page.get_by_label("批次名称", exact=True).fill("浏览器验收样例（非真实评测）")
            page.get_by_label("数据 preset", exact=True).select_option("default")
            page.get_by_label("模型名称", exact=True).fill("slow-ui")
            page.get_by_label("服务地址", exact=True).fill(url)
            page.get_by_label("API Key", exact=True).fill("browser-test-key")
            page.get_by_label("每个组合重复次数").fill("3")
            expect(page.get_by_text("3 RUNS", exact=True)).to_be_visible()
            page.get_by_role("button", name="保存为模板", exact=True).click()
            assert "browser-test-key" not in page.evaluate("JSON.stringify(localStorage)")
            page.get_by_role("button", name="加入队列", exact=True).click()
            expect(page.locator('.run-table tbody tr')).to_have_count(3)
            wait_until(lambda: len(calls) == 2)
            wait_until(lambda: sum(j['phase'] == 'waiting_model' for j in page.request.get(base + '/api/workbench').json()['jobs']) == 2)
            expect(page.locator('.phase-text').filter(has_text='等待模型响应').first).to_have_text(re.compile(r'等待模型响应\s*[1-9]\d* 秒'), timeout=5000)
            jobs_before = page.request.get(base + '/api/workbench').json()['jobs']
            assert sum(j['status'] == 'queued' for j in jobs_before) == 1
            assert 'browser-test-key' not in json.dumps(jobs_before)
            page.get_by_role('button', name='深色', exact=True).click()
            page.screenshot(path=str(directory / 'workbench-dark.png'), full_page=True)
            page.get_by_role('button', name='浅色', exact=True).click()
            page.screenshot(path=str(directory / 'workbench-light.png'), full_page=True)
            page.get_by_role('button', name='关闭详情', exact=True).click()
            page.get_by_role('checkbox', name='选择当前筛选的全部运行', exact=True).check()
            page.get_by_role('button', name='观战选中', exact=True).click()
            expect(page.locator('.watch-grid .run-detail')).to_have_count(3)
            page.reload()
            expect(page.locator('.watch-grid .run-detail')).to_have_count(3)
            assert [j['id'] for j in page.request.get(base + '/api/workbench').json()['jobs']] == [j['id'] for j in jobs_before]
            page.locator('.watch-grid .run-detail').filter(has=page.locator('.status-running')).first.get_by_role('button', name='专注查看', exact=True).click()
            page.set_viewport_size({"width": 1280, "height": 720})
            expect(page.locator('.overview-module')).to_have_count(7)
            expect(page.get_by_role('button', name='停止运行', exact=True)).to_be_in_viewport(ratio=1)
            expect(page.get_by_role('button', name='暂停跟随', exact=True)).to_be_in_viewport(ratio=1)
            assert page.locator('.focus-workspace').evaluate('element => element.scrollHeight <= element.clientHeight + 1')
            page.screenshot(path=str(directory / 'focus-running-compact.png'), full_page=True)
            page.get_by_role('button', name='返回多路观战', exact=True).click()
            page.set_viewport_size({"width": 1600, "height": 1000})
            page.locator('.watch-grid .run-detail').filter(has=page.locator('.status-running')).first.get_by_role('button', name='停止运行', exact=True).click()
            page.get_by_role('button', name='确认停止', exact=True).click()
            focused = browser.new_page(viewport={"width": 1366, "height": 768})
            continuing = next(job for job in page.request.get(base + '/api/workbench').json()['jobs'] if job['status'] == 'running')
            focused.goto(base + f"/?view=focus&run={continuing['id']}")
            expect(focused.locator('.focus-workspace .status-running')).to_have_count(1)
            release.set()
            wait_until(lambda: all(j['status'] in {'completed', 'interrupted'} for j in page.request.get(base + '/api/workbench').json()['jobs']))
            results = page.request.get(base + '/api/workbench').json()['jobs']
            assert sum(j['status'] == 'completed' for j in results) == 2, results
            assert sum(j['status'] == 'interrupted' for j in results) == 1, results
            expect(page.locator('.watch-grid .status-completed')).to_have_count(2)
            # 终态由运行状态表达，行动摘要仍保留真实经营动作，不能退回通用“运行已完成”。
            expect(page.locator('.watch-grid .watch-monitor').filter(has=page.locator('.status-completed')).locator('.monitor-actions')).to_contain_text(['结束', '结束'])
            expect(focused.locator('.focus-workspace .status-completed')).to_have_count(1)
            expect(focused.get_by_label('运行结果通知')).to_be_visible()
            focused.screenshot(path=str(directory / 'completed-focus-retained.png'), full_page=True)
            focused.close()
            page.screenshot(path=str(directory / 'workbench-watch.png'), full_page=True)
            page.get_by_role('button', name='总览', exact=True).click()
            page.get_by_role('button', name='关闭详情', exact=True).click()
            page.get_by_role('button', name='历史存档', exact=True).click()
            expect(page.locator('.archive-table tbody tr')).to_have_count(3)
            page.screenshot(path=str(directory / 'history-terminal-runs.png'), full_page=True)
            # 真实续跑：内部多一个执行，界面始终只有同一个 run。
            original = next(job for job in results if job['status'] == 'interrupted')
            release.clear()
            page.get_by_role('button', name='续跑', exact=True).click()
            page.get_by_label('API Key', exact=True).fill('browser-resume-test-key')
            page.get_by_role('button', name='加入队列', exact=True).click()
            expect(page.locator('.run-table tbody tr')).to_have_count(1)
            wait_until(lambda: len(page.request.get(base + '/api/workbench').json()['jobs']) == 4)
            current_jobs = page.request.get(base + '/api/workbench').json()['jobs']
            resumed = next(job for job in current_jobs if job['config'].get('resume_run_id') == original['archive_id'])
            wait_until(lambda: next(job for job in page.request.get(base + '/api/workbench').json()['jobs'] if job['id'] == resumed['id'])['phase'] == 'waiting_model')
            page.get_by_role('button', name='历史存档', exact=True).click()
            expect(page.locator('.archive-table tbody tr')).to_have_count(2)
            page.goto(base + f"/?view=focus&run={original['id']}")
            expect(page.locator('.focus-workspace .status-running')).to_have_count(1)
            release.set()
            wait_until(lambda: next(job for job in page.request.get(base + '/api/workbench').json()['jobs'] if job['id'] == resumed['id'])['status'] == 'completed')
            expect(page.locator('.focus-workspace .status-completed')).to_have_count(1)
            page.get_by_role('button', name='返回运行列表', exact=True).click()
            page.get_by_role('button', name='历史存档', exact=True).click()
            expect(page.locator('.archive-table tbody tr')).to_have_count(3)
            resumed_row = page.locator('.archive-table tbody tr').filter(has_text=original['archive_id'])
            expect(resumed_row).to_have_count(1)
            expect(resumed_row).to_contain_text('已完成')
            expect(resumed_row).not_to_contain_text('已中断')
            page.screenshot(path=str(directory / 'history-resumed-once.png'), full_page=True)
            page.get_by_role('button', name='运行中心', exact=True).click()
            expect(page.locator('.run-table tbody tr')).to_have_count(0)
            expect(page.get_by_text('当前没有排队或执行中的 run', exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role('heading', name='运行中心', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(directory / 'workbench-mobile.png'), full_page=True)
            page.get_by_role('button', name='历史存档', exact=True).click()
            page.get_by_role('button', name='查看详情', exact=True).first.click()
            expect(page.get_by_role('button', name='返回历史存档', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('button', name='返回历史存档', exact=True).click()
            page.get_by_role('button', name='实验批次', exact=True).click()
            expect(page.get_by_role('heading', name='实验批次', exact=True)).to_be_visible()
            expect(page.locator('.batch-row')).to_have_count(1)
            expect(page.locator('.batch-info')).to_contain_text('3 个 run')
            page.get_by_role('button', name='模型模板', exact=True).click()
            expect(page.get_by_role('button', name='使用模板', exact=True)).to_be_visible()
            browser.close()
        if '--lighthouse' in sys.argv:
            environment = {**os.environ, 'CHROME_PATH': r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'}
            report_path = directory / 'lighthouse.json'
            subprocess.run([shutil.which('npx'), '--yes', 'lighthouse', base,
                            '--preset=desktop', '--chrome-flags=--headless', '--quiet',
                            '--only-categories=performance,accessibility,best-practices', '--output=json',
                            f'--output-path={report_path}'], check=True, timeout=100, env=environment)
            report = json.loads(report_path.read_text(encoding='utf-8'))
            print('Lighthouse:', {name: category['score'] for name, category in report['categories'].items()})
        assert not errors, errors
        print(f"工作台验收通过：并行、排队、停止、终态转历史、通知、保留详情、真实续跑不重复、旧链接定位、批次不重复计数、凭证隔离、明暗主题与窄屏。截图：{directory}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        os.chdir(ROOT)


if __name__ == '__main__':
    main()
