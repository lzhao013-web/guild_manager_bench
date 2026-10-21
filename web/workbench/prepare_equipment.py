"""从装备美术原稿生成 96px 透明无损 WebP。

uv run --with pillow python web/workbench/prepare_equipment.py --source output/imagegen/equipment-pixel-v1/originals
"""
import argparse
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="装备 PNG 原稿目录")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "assets/icons/equipment"
    catalog = json.loads((destination / "index.json").read_text(encoding="utf-8"))
    encoded: dict[str, bytes] = {}
    for item in catalog:
        aid = item["asset_id"]
        if aid not in encoded:
            with Image.open(args.source / f"{aid}.png") as image:
                icon = ImageOps.pad(image.convert("RGBA"), (96, 96), Image.Resampling.NEAREST, color=(0, 0, 0, 0))
            output = BytesIO()
            icon.save(output, "WEBP", lossless=True, exact=True, method=6)
            encoded[aid] = output.getvalue()
    total = 0
    for item in catalog:
        data = encoded[item["asset_id"]]
        (destination / f"{item['id']}.webp").write_bytes(data)
        total += len(data)
    print(f"生成 {len(catalog)} 个装备图标，{len(encoded)} 个独立设计，共 {total:,} 字节")


if __name__ == "__main__":
    main()
