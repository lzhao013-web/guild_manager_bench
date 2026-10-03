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
        assert detail.locator(".card-detail-viewport").evaluate("el => el.getAnimations().length") == 0
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
    expect(detail.locator('[data-action="collapse-detail"]')).to_have_count(0)
    expect(row.locator(".expand-label")).to_have_count(0)
    detail.locator(".run-card").last.scroll_into_view_if_needed()
    if width > 640:
        assert_sticky_header(page)
    if screenshots:
        page.screenshot(path=str(screenshots / f"leaderboard-detail-{width}.png"))
    position_row(page, row)
    row.locator(".expand-button").click()
    expect(detail).to_be_hidden()
    assert page.evaluate("""() => window.savedTable === document.querySelector('.ranking-table') &&
        window.savedHero === document.querySelector('.hero') &&
        window.savedRows.every((row, index) => row === document.querySelectorAll('.model-card')[index])""")
    assert requests == requests_before, requests

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


def check_animations(page, base: str, width: int) -> None:
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": width, "height": 900})
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(base + "?expand=5")
    row = page.locator('.model-card[data-rank="5"]')
    detail = page.locator("#model-detail-4")
    expect(detail).to_be_visible()
    viewport = detail.locator(".card-detail-viewport")
    # 恢复链接中的展开状态时不播放入场动画.
    assert viewport.evaluate("el => el.getAnimations().length") == 0
    position_row(page, row)
    requests_before = list(requests)
    page.evaluate("""() => {
        window.savedTable = document.querySelector('.ranking-table');
        window.savedDetail = document.querySelector('#model-detail-4 .card-detail');
    }""")

    def toggle_at(progress: float) -> dict:
        # 暂停在指定进度, 验证真实布局的中间帧, 不依赖固定等待时间.
        return row.evaluate("""(row, progress) => {
            const viewport = row.nextElementSibling.querySelector('.card-detail-viewport');
            const before = viewport.getBoundingClientRect().height;
            row.querySelector('.expand-button').click();
            const animation = viewport.getAnimations()[0];
            animation.pause();
            animation.currentTime = 0;
            const start = viewport.getBoundingClientRect().height;
            animation.currentTime = animation.effect.getTiming().duration * progress;
            return {before, start, height: viewport.getBoundingClientRect().height,
                full: viewport.scrollHeight, opacity: Number(getComputedStyle(viewport).opacity),
                inert: row.nextElementSibling.inert, scrollY};
        }""", progress)

    def finish() -> None:
        viewport.evaluate("el => el.getAnimations().forEach(animation => animation.finish())")
        page.wait_for_function("document.querySelector('#model-detail-4 .card-detail-viewport').getAnimations().length === 0")

    scroll_y = page.evaluate("scrollY")
    closing = toggle_at(.5)
    assert 0 < closing["height"] < closing["full"], closing
    assert 0 < closing["opacity"] < 1 and closing["inert"], closing
    assert abs(closing["start"] - closing["before"]) <= 1, closing
    assert abs(closing["scrollY"] - scroll_y) <= 1, closing
    expect(detail).to_be_visible()
    expect(row.locator(".expand-button")).to_have_attribute("aria-expanded", "false")

    reopening = toggle_at(.5)
    assert abs(reopening["start"] - closing["height"]) <= 1, reopening
    assert closing["height"] < reopening["height"] < reopening["full"], reopening
    assert closing["opacity"] < reopening["opacity"] < 1 and not reopening["inert"], reopening
    finish()
    expect(detail).to_be_visible()
    expect(row.locator(".model-name")).to_have_attribute("aria-expanded", "true")

    toggle_at(.5)
    finish()
    expect(detail).to_be_hidden()
    opening = toggle_at(.5)
    assert opening["start"] == 0 and 0 < opening["height"] < opening["full"], opening
    assert 0 < opening["opacity"] < 1, opening
    reversed_close = toggle_at(.5)
    assert abs(reversed_close["start"] - opening["height"]) <= 1, reversed_close
    assert 0 < reversed_close["height"] < opening["height"], reversed_close
    finish()
    expect(detail).to_be_hidden()
    assert abs(page.evaluate("scrollY") - scroll_y) <= 1
    assert page.evaluate("window.savedTable === document.querySelector('.ranking-table') && window.savedDetail === document.querySelector('#model-detail-4 .card-detail')")
    assert requests == requests_before, requests

    # 自然播放结束后恢复内容高度, 缩放窗口不能截断详情.
    row.locator(".expand-button").click()
    page.wait_for_function("document.querySelector('#model-detail-4 .card-detail-viewport').getAnimations().length === 0")
    page.set_viewport_size({"width": 390 if width > 640 else 1440, "height": 900})
    assert viewport.evaluate("el => Math.abs(el.getBoundingClientRect().height - el.scrollHeight) <= 1")
    assert viewport.evaluate("el => el.style.height") == ""

    # 动画途中筛选重绘, 新节点应恢复目标状态, 不继承旧动画.
    position_row(page, row)
    toggle_at(.5)
    row.evaluate("el => el.querySelector('.expand-button').click()")
    page.locator("#leaderboardSearchInput").fill("测试模型 05")
    expect(page.locator(".model-card")).to_have_count(1)
    restored = page.locator(".model-detail-row")
    expect(restored).to_be_visible()
    assert restored.locator(".card-detail-viewport").evaluate("el => el.getAnimations().length") == 0
    page.locator(".model-card .expand-button").click()
    expect(restored).to_be_hidden()
    assert not errors, errors


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
                base = f"http://127.0.0.1:{server.server_port}/"
                check_interactions(page, base, width, args.screenshots)
                page.close()
                page = context.new_page()
                check_animations(page, base, width)
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Leaderboard smoke passed: animated toggles, rapid reversal, reduced motion, keyboard, local updates, sticky header, filters, selection, mobile.")


if __name__ == "__main__":
    main()
