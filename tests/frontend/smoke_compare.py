"""浏览器冒烟测试。运行方式见 README，不访问模型服务或真实存档。"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from profile_browser import check_profile


ROOT = Path(__file__).resolve().parents[2]


def exported_replay() -> tuple[dict, str]:
    """用真实 runner 和存档序列化器验证前端契约，不访问 LLM。"""
    from guild_manager_bench.bench.llm import GuildManagerTools, LlmAgentResponse, LlmRunConfig, LlmToolCall, run_llm_turn
    from guild_manager_bench.bench.llm.archive import build_llm_run_replay
    from guild_manager_bench.game.presets import describe_data_source

    data_dir = ROOT / "data/presets/default"
    tools = GuildManagerTools.from_data_dir(data_dir)
    initial = tools.start_session("comparison-contract")["observation"]
    responses = iter([
        LlmToolCall("recruit_adventurer", {"candidate_id": 1}),
        LlmToolCall("equip_item", {"adventurer_id": 1, "equipment_instance_id": "missing"}),
        LlmToolCall("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]}),
    ])

    class Agent:
        def respond(self, **kwargs):
            return LlmAgentResponse(tool_calls=(next(responses),))

    config = LlmRunConfig(archive_dir=None)
    trace = run_llm_turn(Agent(), tools, "comparison-contract", config=config)
    assert trace.status == "completed"
    record = build_llm_run_replay(
        status="running", session_id="comparison-contract", turns=[trace],
        created_at="contract-test", updated_at="contract-test",
        config={"max_tool_calls_per_turn": config.max_tool_calls_per_turn, "objective": config.objective},
        agent={"type": "TestAgent", "config": {"model": "真实存档协议测试"}},
        data_source=describe_data_source(data_dir),
        final_observation=tools.get_observation("comparison-contract")["observation"],
    )
    return record, initial["recruit_candidates"][0]["name"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=None, help="例如 msedge，默认使用 Playwright Chromium")
    parser.add_argument("--screenshots", type=Path, default=None)
    args = parser.parse_args()
    fixtures_url = (ROOT / "tests/frontend/compare-fixtures.mjs").as_uri()
    data = json.loads(subprocess.check_output([
        "node", "--input-type=module", "-e",
        f"import {{ browserReplays }} from {json.dumps(fixtures_url)}; process.stdout.write(JSON.stringify(browserReplays()));",
    ]))
    data["real"], recruited_name = exported_replay()
    requests: list[tuple[str, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(("GET", path))
            if path == "/api/llm/runs":
                value = {"runs": [
                    {"run_id": key, "model": replay["agent"]["config"]["model"], "preset": "default", "status": replay["status"], "created_at": replay["created_at"] if "created_at" in replay else "synthetic-fixture"}
                    for key, replay in data.items()
                ]}
            elif path in {f"/api/llm/runs/{key}/replay" for key in data}:
                value = data[path.split("/")[-2]]
            else:
                return super().do_GET()
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_error(405, "Comparison must remain read-only")

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "web")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/replay/compare.html?left=a&right=b")
            expect(page.locator("#workspace")).to_be_visible()
            expect(page.locator("#turnTitle")).to_have_text("第 1 回合")
            expect(page.locator("#roundContent")).to_contain_text("招募 · 法师")
            expect(page.locator("#roundContent")).to_contain_text("经验分配 · 先锋 · 80 经验")
            expect(page.locator("#roundContent")).to_contain_text("先锋 → 森林狼")
            page.get_by_role("button", name="分差扩大最多，第 2 回合").click()
            expect(page.locator("#turnTitle")).to_have_text("第 2 回合")
            expect(page.locator(".score-gap")).to_contain_text("75")
            page.locator(".attempt-history > summary").click()
            expect(page.locator(".attempt-record")).to_contain_text("empty_response_limit")
            expect(page.locator(".attempt-record")).to_contain_text("未找到装备实例")
            page.get_by_role("button", name="首个行动分歧，第 1 回合").click()
            if args.screenshots:
                page.evaluate("window.scrollTo(0, 0)")
                args.screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshots / "compare-desktop.png"), full_page=True)

            # 可复制的 URL 恢复实际回合，而不是数组下标。
            page.goto(base + "/replay/compare.html?left=a&right=b&turn=3")
            expect(page.locator("#turnTitle")).to_have_text("第 3 回合")
            page.get_by_role("button", name="上一回合", exact=True).click()
            expect(page.locator("#turnTitle")).to_have_text("第 2 回合")
            api_count = sum(path.startswith("/api/") for _, path in requests)

            page.locator("#sourcePicker > summary").click()
            # 本地文件留在浏览器中，名称、正文和状态字段不能注入 HTML。
            imported = json.loads(json.dumps(data["a"]))
            payload = '<img src=x onerror="window.__compareInjected=1">'
            imported["agent"]["config"]["model"] = payload
            imported["data"]["game_seed"] = 123
            imported["turns"][0]["status"] = payload
            imported["turns"][0]["steps"][0]["content"] = payload
            imported["turns"] = [imported["turns"][0], imported["turns"][2]]
            page.locator("#fileA").set_input_files({"name": "local-replay.json", "mimeType": "application/json", "buffer": json.dumps(imported).encode()})
            expect(page.locator("#sourceA")).to_contain_text("local-replay.json")
            page.locator("#sourcePicker > summary").click()
            expect(page.locator("#conditions")).to_contain_text("游戏种子 · 不同")
            expect(page.get_by_role("button", name="分差扩大最多，无可用回合")).to_be_disabled()
            expect(page.locator(".side-a.side-panel")).to_contain_text("没有第 2 回合的记录")
            page.locator("#turnSelect").select_option("1")
            expect(page.locator("#roundContent")).to_contain_text(payload)
            assert page.evaluate("window.__compareInjected") is None
            assert page.locator("#workspace img").count() == 0
            assert sum(path.startswith("/api/") for _, path in requests) == api_count
            page.locator("#fileB").set_input_files({"name": "invalid.json", "mimeType": "application/json", "buffer": b'{"stats": {}}'})
            expect(page.locator("#loadError")).to_contain_text("请选择 LLM")
            expect(page.locator("#sourceB")).to_contain_text("保留原跑局")

            # 小屏幕不出现横向溢出，关键导航仍然可用。
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(base + "/replay/compare.html?left=a&right=b")
            expect(page.locator("#turnTitle")).to_have_text("第 1 回合")
            page.get_by_role("button", name="首个失败节点，第 2 回合").click()
            expect(page.locator("#turnTitle")).to_have_text("第 2 回合")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            if args.screenshots:
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(args.screenshots / "compare-mobile.png"), full_page=True)
            page.goto(base + "/replay/compare.html?left=real&right=real")
            expect(page.locator("#turnTitle")).to_have_text("第 1 回合")
            expect(page.locator("#roundContent")).to_contain_text(f"招募 · {recruited_name}")
            expect(page.locator("#roundContent")).to_contain_text("未找到装备实例")
            expect(page.locator(".score-gap")).to_have_text("分数差未知")
            check_profile(page, base, data, requests, args.screenshots)
            assert all(method == "GET" for method, _ in requests), requests
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Browser smoke passed: loading, actions, key turns, retry history, deep links, local files, XSS, mobile, read-only requests.")
    if args.screenshots:
        print(f"Screenshots: {args.screenshots}")


if __name__ == "__main__":
    main()
