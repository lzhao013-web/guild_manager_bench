from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from time import perf_counter


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(prog="guild-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="启动可视化服务")
    serve_parser.add_argument("--data-dir", default="data")
    serve_parser.add_argument("--preset", default=None)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

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
    bl_parser.add_argument("--full-rebuild", action="store_true", help="忽略缓存，完全重新构建")

    run_parser = subparsers.add_parser("run", help="运行 LLM agent benchmark")
    run_parser.add_argument(
        "--provider", default="openai",
        choices=["openai", "openai-responses", "anthropic"],
        help="LLM provider：OpenAI-compatible Chat Completions、OpenAI Responses 或 Anthropic (默认: openai)",
    )
    run_parser.add_argument("--model", default=None, help="模型名称 (也可通过 OPENAI_MODEL / ANTHROPIC_MODEL 环境变量设置)")
    run_parser.add_argument("--api-key", default=None, help="API Key (也可通过 OPENAI_API_KEY 环境变量设置)")
    run_parser.add_argument("--base-url", default=None, help="API Base URL (也可通过 OPENAI_BASE_URL 环境变量设置)")
    run_parser.add_argument("--data-dir", default="data", help="数据根目录或直接游戏数据目录 (默认: data)")
    run_parser.add_argument("--preset", default=None, help="数据 preset 名称 (默认: default)")
    run_parser.add_argument("--game-seed", type=int, default=None, help="游戏随机种子")
    run_parser.add_argument("--scoring-seed", type=int, default=None, help="评分随机种子")
    run_parser.add_argument("--archive-dir", default="runs/llm", help="存档目录 (默认: runs/llm，设为 none 禁用存档)")
    run_parser.add_argument("--resume", default=None, help="从指定存档目录续跑 (传入 archive run 目录路径)")
    run_parser.add_argument("--max-tool-calls-per-turn", type=int, default=20, help="每回合最大非 end_turn 工具调用次数 (默认: 20)")
    run_parser.add_argument("--reasoning-effort", default=None, choices=["none", "minimal", "low", "medium", "high", "xhigh", "ultra", "max"], help="OpenAI-compatible 推理强度，ultra/max 仅部分模型支持 (默认不传)")
    run_parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None, help="启用或禁用 Anthropic adaptive thinking (默认不传)")
    run_parser.add_argument("--thinking-effort", default=None, choices=["low", "medium", "high", "max"], help="Anthropic 思考强度，max 仅部分模型支持 (默认不传)")
    run_parser.add_argument("--timeout", type=float, default=None, help="API 请求超时秒数 (也可通过 provider 对应环境变量设置)")
    run_parser.add_argument("--no-stream", action="store_true", help="禁用 API 流式响应")
    display = run_parser.add_mutually_exclusive_group()
    display.add_argument("--quiet", "-q", action="store_true", help="只输出最终报告")
    display.add_argument("--verbose", "-v", action="store_true", help="展开队伍、目标、工具参数与结果")
    display.add_argument("--debug", action="store_true", help="展开请求、响应、工具事件和异常堆栈，可能包含敏感内容")
    display.add_argument("--json", dest="json_output", action="store_true", help="stdout 仅输出最终 JSON，运行错误也以 JSON 表示")
    run_parser.add_argument("--show-model-text", action="store_true", help="展示模型实际返回的正文")
    run_parser.add_argument("--show-reasoning", action="store_true", help="展示 API 实际提供的推理文本或摘要")
    run_parser.add_argument("--no-color", action="store_true", help="禁用颜色和文字样式 (也支持 NO_COLOR)")

    sl_parser = subparsers.add_parser("serve-leaderboard", help="启动排行榜静态服务")
    sl_parser.add_argument("--host", default="127.0.0.1")
    sl_parser.add_argument("--port", type=int, default=8080)
    sl_parser.add_argument("--directory", type=Path, default=Path("web/leaderboard"), help="排行榜静态文件目录 (默认: web/leaderboard)")

    args = parser.parse_args()
    if args.command == "serve":
        _serve(args.data_dir, args.preset, args.host, args.port)
    elif args.command == "run":
        if (args.json_output or args.quiet) and (args.show_model_text or args.show_reasoning):
            run_parser.error("--quiet / --json 不能与模型文本展示选项同时使用")
        _run(args)
    elif args.command == "build-leaderboard":
        _build_leaderboard(args.data_dir, args.output, full_rebuild=args.full_rebuild)
    elif args.command == "serve-leaderboard":
        _serve_leaderboard(args.host, args.port, args.directory)


def _run(args: argparse.Namespace) -> None:
    from rich.console import Console

    from guild_manager_bench.bench.llm import LlmRunConfig, run_llm_game
    from guild_manager_bench.cli_output import RunReporter
    from guild_manager_bench.game.presets import resolve_data_source

    mode = (
        "json" if args.json_output else "quiet" if args.quiet
        else "debug" if args.debug else "verbose" if args.verbose else "watch"
    )
    console = Console(
        highlight=False, markup=False, force_terminal=sys.stdout.isatty(),
        color_system=None if args.no_color or "NO_COLOR" in os.environ else "auto",
    )
    reporter = RunReporter(
        console, mode=mode, provider=args.provider,
        show_model_text=args.show_model_text, show_reasoning=args.show_reasoning,
    )
    started = perf_counter()
    with reporter:
        try:
            if args.provider == "anthropic":
                from guild_manager_bench.bench.llm import AnthropicMessagesAgent

                agent = AnthropicMessagesAgent.from_env(
                    model=args.model, api_key=args.api_key, base_url=args.base_url,
                    timeout=args.timeout, thinking=args.thinking, effort=args.thinking_effort,
                )
            elif args.provider == "openai-responses":
                from guild_manager_bench.bench.llm import OpenAIResponsesAgent

                agent = OpenAIResponsesAgent.from_env(
                    model=args.model, api_key=args.api_key, base_url=args.base_url,
                    reasoning_effort=args.reasoning_effort, timeout=args.timeout,
                )
            else:
                from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent

                agent = OpenAIChatCompletionsAgent.from_env(
                    model=args.model, api_key=args.api_key, base_url=args.base_url,
                    reasoning_effort=args.reasoning_effort, timeout=args.timeout,
                )

            # 在实例上遮蔽流式方法，保持 provider 的非流式实现不变。
            if args.no_stream:
                agent.respond_stream = None
            reporter.configure(agent.config, streaming=not args.no_stream)
            data = resolve_data_source(args.data_dir, args.preset)
            config = LlmRunConfig(
                archive_dir=None if args.archive_dir.lower() == "none" else args.archive_dir,
                max_tool_calls_per_turn=args.max_tool_calls_per_turn,
                game_seed=args.game_seed, scoring_seed=args.scoring_seed,
            )
            run = run_llm_game(
                agent, data_dir=data.data_dir, config=config, event_sink=reporter.on_event,
                resume_archive_dir=args.resume, data_source=data.to_dict(),
            )
            reporter.finish(run, wall_time=perf_counter() - started)
        except KeyboardInterrupt:
            reporter.abort("运行已中断", interrupted=True)
            raise SystemExit(130) from None
        except Exception as exc:
            reporter.abort(str(exc), exception=exc)
            raise SystemExit(1) from None
    if run.status != "completed":
        raise SystemExit(1)


def _serve(data_dir: str, preset: str | None, host: str, port: int) -> None:
    """启动可视化服务。"""
    import uvicorn
    from guild_manager_bench.api.app import create_app

    uvicorn.run(create_app(data_dir, preset=preset), host=host, port=port)


def _build_leaderboard(data_dir: Path, output: Path, *, full_rebuild: bool = False) -> None:
    """构建排行榜数据文件。"""
    from guild_manager_bench.bench.leaderboard import build_leaderboard

    build_leaderboard(data_dir.resolve(), output.resolve(), incremental=not full_rebuild)


def _serve_leaderboard(host: str, port: int, directory: Path) -> None:
    """启动排行榜静态文件服务。"""
    import functools
    import http.server

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
