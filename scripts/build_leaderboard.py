#!/usr/bin/env python3
"""
Build leaderboard data from replay files.

Scans web/leaderboard/data/ for replay.json files, aggregates by model,
and outputs web/leaderboard/leaderboard_data.json.

Supports incremental builds — unchanged files are cached for speed.

Usage::

    # via uv CLI (推荐)
    uv run guild-manager build-leaderboard
    uv run guild-manager build-leaderboard --data-dir path/to/replays --output path/to/out.json
    uv run guild-manager build-leaderboard --full-rebuild

    # or directly
    python scripts/build_leaderboard.py
    python scripts/build_leaderboard.py --data-dir path/to/replays
    python scripts/build_leaderboard.py --full-rebuild
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Build leaderboard data from replay files")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "web" / "leaderboard" / "data",
                        help="Directory containing replay JSON files")
    parser.add_argument("--output", type=Path, default=ROOT / "web" / "leaderboard" / "leaderboard_data.json",
                        help="Output JSON file path")
    parser.add_argument("--full-rebuild", action="store_true",
                        help="Ignore cache, rebuild everything from scratch")
    args = parser.parse_args()

    # Ensure the package is importable when running the script directly
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from guild_manager_bench.bench.leaderboard import build_leaderboard
    build_leaderboard(args.data_dir, args.output, incremental=not args.full_rebuild)


if __name__ == "__main__":
    main()
