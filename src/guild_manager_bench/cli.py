from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(prog="guild-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="启动可视化服务")
    serve_parser.add_argument("--data-dir", default="data")
    serve_parser.add_argument("--preset", default=None)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    # ── build-leaderboard ──
    bl_parser = subparsers.add_parser("build-leaderboard", help="构建排行榜数据")
    bl_parser.add_argument(
        "--data-dir", type=Path,
        default=Path("web/leaderboard/data"),
        help="replay JSON 文件目录 (默认: web/leaderboard/data)",
    )
    bl_parser.add_argument(
        "--output", type=Path,
        default=Path("web/leaderboard/leaderboard_data.json"),
        help="输出文件路径 (默认: web/leaderboard/leaderboard_data.json)",
    )

    # ── run ──
    run_parser = subparsers.add_parser("run", help="运行 LLM agent benchmark")
    run_parser.add_argument("--model", default=None, help="模型名称 (也可通过 OPENAI_MODEL 环境变量设置)")
    run_parser.add_argument("--api-key", default=None, help="API Key (也可通过 OPENAI_API_KEY 环境变量设置)")
    run_parser.add_argument("--base-url", default=None, help="API Base URL (也可通过 OPENAI_BASE_URL 环境变量设置)")
    run_parser.add_argument("--data-dir", default="data", help="数据根目录或直接游戏数据目录 (默认: data)")
    run_parser.add_argument("--preset", default=None, help="数据 preset 名称 (默认: default)")
    run_parser.add_argument("--game-seed", type=int, default=None, help="游戏随机种子")
    run_parser.add_argument("--scoring-seed", type=int, default=None, help="评分随机种子")
    run_parser.add_argument("--archive-dir", default="runs/llm", help="存档目录 (默认: runs/llm，设为 none 禁用存档)")
    run_parser.add_argument("--max-tool-calls-per-turn", type=int, default=20, help="每回合最大工具调用次数 (默认: 20)")
    run_parser.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    run_parser.add_argument("--quiet", "-q", action="store_true", help="静默模式，只输出最终结果")

    # ── serve-leaderboard ──
    sl_parser = subparsers.add_parser("serve-leaderboard", help="启动排行榜静态服务")
    sl_parser.add_argument("--host", default="127.0.0.1")
    sl_parser.add_argument("--port", type=int, default=8080)
    sl_parser.add_argument(
        "--directory", type=Path,
        default=Path("web/leaderboard"),
        help="排行榜静态文件目录 (默认: web/leaderboard)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _serve(args.data_dir, args.preset, args.host, args.port)
    elif args.command == "run":
        _run(args)
    elif args.command == "build-leaderboard":
        _build_leaderboard(args.data_dir, args.output)
    elif args.command == "serve-leaderboard":
        _serve_leaderboard(args.host, args.port, args.directory)


def _run(args: argparse.Namespace) -> None:
    """运行 LLM agent benchmark。"""

    import json

    from guild_manager_bench.bench.llm import (
        LlmRunConfig,
        OpenAIChatCompletionsAgent,
        run_llm_game,
    )
    from guild_manager_bench.bench.llm.runner import _compute_run_stats
    from guild_manager_bench.game.presets import resolve_data_source

    # ── 构造 agent ──
    agent = OpenAIChatCompletionsAgent.from_env(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
    )

    # 禁用流式：移除 respond_stream 使 runner 回退到 respond
    if args.no_stream and hasattr(agent, "respond_stream"):
        delattr(agent, "respond_stream")

    data_preset = resolve_data_source(args.data_dir, args.preset)

    # ── 构造配置 ──
    archive_dir = None if args.archive_dir.lower() == "none" else args.archive_dir
    config = LlmRunConfig(
        archive_dir=archive_dir,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        game_seed=args.game_seed,
        scoring_seed=args.scoring_seed,
    )

    quiet = args.quiet

    # ── 事件回调 ──
    def _print(*parts: str) -> None:
        if not quiet:
            print(*parts, flush=True)

    def on_event(event: dict) -> None:
        t = event["type"]

        if t == "run_started":
            _print(f"Run started  session={event['session_id']}")

        elif t == "turn_started":
            _print(f"Turn {event['turn']} started")

        elif t == "tool_call":
            _print(f"  → {event['name']}")

        elif t == "tool_result":
            result = event.get("result", {})
            ok = result.get("ok")
            if ok is True:
                _print(f"    ✓")
            elif event["name"] != "end_turn":
                err = result.get("error", "unknown error")
                _print(f"    ✗ {err}")

        elif t == "turn_completed":
            trace = event.get("trace", {})
            turn = trace.get("turn", "?")
            tool_count = len(trace.get("tool_calls", []))
            _print(f"Turn {turn} completed  ({tool_count} tool calls)")

        elif t == "turn_failed":
            trace = event.get("trace", {})
            turn = trace.get("turn", "?")
            reason = trace.get("failure_reason", "unknown")
            _print(f"Turn {turn} failed  reason={reason}")

        elif t == "retry":
            _print(f"  ↻ retry  turn={event.get('turn')}  reason={event.get('reason')}")

        elif t == "run_completed":
            _print()

        elif t == "run_failed":
            _print()

    # ── 执行 ──
    try:
        run = run_llm_game(
            agent,
            data_dir=data_preset.data_dir,
            config=config,
            event_sink=on_event,
            data_source=data_preset.to_dict(),
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)

    # ── 输出结果 ──
    stats = _compute_run_stats(run)
    score = run.score
    timing = stats["timing"]
    tokens = stats["token_usage"]
    actions = stats["game_actions"]

    lines = [
        "── Results ──",
        f"Status: {run.status}",
        f"Session: {run.session_id}",
    ]
    if score is not None:
        lines.append(f"Score: {score}")
    lines.append(f"Turns: {len(run.turns)}")
    lines.append(
        f"Duration: {timing['total_duration_seconds']}s"
    )
    lines.append(
        f"Token usage: {tokens['input_tokens']} in / {tokens['output_tokens']} out"
    )
    if tokens.get("cache_read_input_tokens"):
        lines.append(f"Cache read: {tokens['cache_read_input_tokens']} tokens")
    lines.append(
        f"Battles: {actions['battles_won']} won / {actions['battles_lost']} lost"
    )
    lines.append(f"Gold earned: {actions['total_gold_earned']}")
    if run.archive_dir:
        lines.append(f"Archive: {run.archive_dir}")
    if run.failure_reason:
        lines.append(f"Failure reason: {run.failure_reason}")

    print("\n".join(lines))

    # ── JSON 详情（调试用）──
    if not quiet:
        print(f"\n(stats json)")
        print(json.dumps(stats, indent=2, ensure_ascii=False))

def _serve(data_dir: str, preset: str | None, host: str, port: int) -> None:
    """启动可视化服务。"""

    import uvicorn

    from guild_manager_bench.api.app import create_app

    uvicorn.run(create_app(data_dir, preset=preset), host=host, port=port)


def _build_leaderboard(data_dir: Path, output: Path) -> None:
    """构建排行榜数据文件。"""

    from guild_manager_bench.bench.leaderboard import build_leaderboard

    build_leaderboard(data_dir.resolve(), output.resolve())


def _serve_leaderboard(host: str, port: int, directory: Path) -> None:
    """启动排行榜静态文件服务。"""

    import http.server
    import functools

    directory = directory.resolve()
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        raise SystemExit(1)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with http.server.HTTPServer((host, port), handler) as httpd:
        print(f"Leaderboard → http://{host}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
