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
    elif args.command == "build-leaderboard":
        _build_leaderboard(args.data_dir, args.output)
    elif args.command == "serve-leaderboard":
        _serve_leaderboard(args.host, args.port, args.directory)


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
