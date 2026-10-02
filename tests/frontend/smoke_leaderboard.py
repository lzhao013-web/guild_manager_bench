"""排行榜交互回归: uv run --with playwright python tests/frontend/smoke_leaderboard.py --channel msedge"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]


def leaderboard_fixture() -> dict:
    models = []
    for rank in range(1, 29):
        runs = [
            {
                "run_id": f"model-{rank}-run-{run}",
                "created_at": "20261002-120000",
                "rank_score": 1000 - rank * 10 + run,
                "turns": 30,
                "max_turns": 30,
                "party_size": 4,
                "party_size_limit": 4,
                "token_usage": {"output_tokens": rank * 1000},
                "timing": {"total_seconds": 60 + rank},
            }
            for run in range(3)
        ]
        models.append({
            "model": f"测试模型 {rank:02}",
            "rank": rank,
            "runs": len(runs),
            "rank_score": {"mean": 1000 - rank * 10, "best": 1002 - rank * 10},
            "efficiency": {
                "output_tokens": {"mean": rank * 1000},
                "duration_seconds": {"mean": 60 + rank},
            },
            "run_details": runs,
        })
    return {"models": models, "total_runs": 84, "generated_at": "2026-10-02"}


def position_row(page, row) -> float:
    row.evaluate("el => window.scrollTo({top: el.getBoundingClientRect().top + scrollY - 180, behavior: 'instant'})")
    return page.evaluate("scrollY")


def assert_sticky_header(page) -> None:
    geometry = page.evaluate("""() => {
        const header = document.querySelector('.header').getBoundingClientRect();
        const cells = [...document.querySelectorAll('.ranking-table thead th')];
        return cells.map(cell => {
            const rect = cell.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            return {top: rect.top, expected: header.bottom, onTop: cell.contains(hit)};
        });
    }""")
    assert all(abs(cell["top"] - cell["expected"]) <= 1 and cell["onTop"] for cell in geometry), geometry


def check_interactions(page, base: str, width: int, screenshots: Path | None) -> None:
    page.set_viewport_size({"width": width, "height": 900 if width > 640 else 844})
    requests, errors = [], []
    page.on("request", lambda request: requests.append((request.resource_type, request.url)))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    expect(page.locator(".model-card")).to_have_count(28)
    page.evaluate("document.fonts.ready")
    row = page.locator('.model-card[data-rank="5"]')
    other = page.locator('.model-card[data-rank="6"]')
    detail = page.locator("#" + row.locator(".expand-button").get_attribute("aria-controls"))
    scroll_y = position_row(page, row)
    if width > 640:
        assert_sticky_header(page)
    else:
        expect(row.locator(".score-cell .mobile-label")).to_be_visible()
        expect(row.locator(".output-cell .mobile-label")).to_be_visible()

    page.evaluate("""() => {
        window.savedTable = document.querySelector('.ranking-table');
        window.savedRows = [...document.querySelectorAll('.model-card')];
        window.savedHero = document.querySelector('.hero');
    }""")
    requests_before = list(requests)
    for key in ("Enter", "Space"):
        row.locator(".expand-button").focus()
        row.locator(".expand-button").press(key)
        expect(detail).to_be_visible()
        expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
        expect(row.locator(".model-name")).to_have_attribute("aria-expanded", "true")
        expect(other.locator(".expand-button")).to_have_attribute("aria-expanded", "false")
        assert abs(page.evaluate("scrollY") - scroll_y) <= 1
        page.evaluate("window.savedDetail = document.querySelector('#model-detail-4 .card-detail').firstElementChild")
        row.locator(".model-name").click()
        expect(detail).to_be_hidden()
        expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "false")
        expect(row.locator(".model-name")).to_have_attribute("aria-expanded", "false")
        assert abs(page.evaluate("scrollY") - scroll_y) <= 1
        assert page.evaluate("window.savedDetail === document.querySelector('#model-detail-4 .card-detail').firstElementChild")

    row.locator(".expand-button").click()
    assert page.evaluate("window.savedDetail === document.querySelector('#model-detail-4 .card-detail').firstElementChild")
    # 长详情底部收起后, 焦点回到当前条目, 不重新创建榜单或请求数据.
    bottom = detail.locator('[data-action="collapse-detail"]').last
    bottom.scroll_into_view_if_needed()
    if width > 640:
        assert_sticky_header(page)
    if screenshots:
        page.screenshot(path=str(screenshots / f"leaderboard-detail-{width}.png"))
    bottom.click()
    expect(detail).to_be_hidden()
    expect(row.locator(".expand-button")).to_be_focused()
    row_box = row.bounding_box()
    header_box = page.locator(".header").bounding_box()
    min_top = header_box["height"]
    if width > 640:
        min_top += page.locator(".ranking-table thead").bounding_box()["height"]
    assert row_box["y"] >= min_top - 1, (row_box, min_top)
    assert row_box["y"] + row_box["height"] <= page.viewport_size["height"]
    assert page.evaluate("""() => window.savedTable === document.querySelector('.ranking-table') &&
        window.savedHero === document.querySelector('.hero') &&
        window.savedRows.every((row, index) => row === document.querySelectorAll('.model-card')[index])""")
    assert requests == requests_before, requests

    row.locator(".expand-button").click()
    detail.locator('[data-action="collapse-detail"]').first.click()
    expect(detail).to_be_hidden()
    row.locator(".expand-button").click()
    other.locator(".expand-button").click()
    expect(detail).to_be_visible()
    expect(other.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
    position_row(page, row)
    row.locator("input[type=checkbox]").check()
    expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#compareCount")).to_have_text("已选 1 / 4")

    # 筛选和排序允许重绘, 但展开状态与对比选择必须按模型保留.
    page.locator("#leaderboardSearchInput").fill("测试模型 05")
    expect(page.locator(".model-card")).to_have_count(1)
    expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
    expect(row.locator("input[type=checkbox]")).to_be_checked()
    page.locator("#leaderboardSearchClear").click()
    page.locator("#leaderboardSort").select_option("duration")
    expect(page.locator(".model-card")).to_have_count(28)
    expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
    expect(other.locator(".expand-button")).to_have_attribute("aria-expanded", "true")
    expect(row.locator("input[type=checkbox]")).to_be_checked()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    position_row(page, row)
    if width > 640:
        assert_sticky_header(page)
    if screenshots:
        page.screenshot(path=str(screenshots / f"leaderboard-{width}.png"))
    assert not errors, errors
    assert sum(kind == "document" for kind, _ in requests) == 1, requests
    if width == 390:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_function("""() => Math.abs(
            parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--header-height')) -
            document.querySelector('.header').getBoundingClientRect().height) < 1""")
        position_row(page, row)
        assert_sticky_header(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=None)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "web/leaderboard")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel, headless=True)
            context = browser.new_context(reduced_motion="reduce")
            context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
            context.route("https://fonts.gstatic.com/**", lambda route: route.abort())
            context.route("**/leaderboard_data.json", lambda route: route.fulfill(json=leaderboard_fixture()))
            context.route("**/model_notes.json", lambda route: route.fulfill(json={}))
            for width in (1440, 768, 390):
                page = context.new_page()
                check_interactions(page, f"http://127.0.0.1:{server.server_port}/", width, args.screenshots)
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Leaderboard smoke passed: toggles, keyboard, local updates, scroll position, sticky header, filters, selection, mobile.")


if __name__ == "__main__":
    main()
