from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"


def copy_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def replace_in_file(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")


def build_site(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    copy_contents(WEB_DIR / "static", output_dir)

    for name in ("assets", "replay"):
        src = WEB_DIR / name
        if src.exists():
            shutil.copytree(src, output_dir / name)

    leaderboard_dir = WEB_DIR / "leaderboard"
    if leaderboard_dir.exists():
        shutil.copytree(
            leaderboard_dir,
            output_dir / "leaderboard",
            ignore=shutil.ignore_patterns("data", ".build_cache.json"),
        )

    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    replace_in_file(
        output_dir / "index.html",
        {
            'href="/styles.css"': 'href="styles.css"',
            'href="/replay/"': 'href="replay/"',
            'src="/app.js?': 'src="app.js?',
        },
    )
    replace_in_file(
        output_dir / "app.js",
        {
            "`/assets/": "`assets/",
        },
    )
    replace_in_file(
        output_dir / "replay" / "index.html",
        {
            'href="/replay/styles.css"': 'href="styles.css"',
            'href="/" class="btn btn-ghost"': 'href="../" class="btn btn-ghost"',
            'src="/replay/app.js"': 'src="app.js"',
        },
    )
    replace_in_file(
        output_dir / "replay" / "app.js",
        {
            "`/assets/": "`../assets/",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the GitHub Pages static site.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "_site",
        help="Output directory for the Pages artifact.",
    )
    args = parser.parse_args()
    build_site(args.output)


if __name__ == "__main__":
    main()
