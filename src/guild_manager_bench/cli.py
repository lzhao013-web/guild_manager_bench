from __future__ import annotations

import argparse


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(prog="guild-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--data-dir", default="data")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.command == "serve":
        _serve(args.data_dir, args.host, args.port)


def _serve(data_dir: str, host: str, port: int) -> None:
    """启动可视化服务。"""

    import uvicorn

    from guild_manager_bench.api.app import create_app

    uvicorn.run(create_app(data_dir), host=host, port=port)


if __name__ == "__main__":
    main()
