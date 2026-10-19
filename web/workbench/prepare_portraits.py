"""生成适合页面尺寸的 PNG 和无损 WebP，使用最近邻缩放保留像素边缘。

uv run --with pillow python web/workbench/prepare_portraits.py
uv run --with pillow python web/workbench/prepare_portraits.py --source output/imagegen/gothic-pixel-v5/originals
"""
import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="包含 classes/ 和 monsters/ 的原稿目录；默认使用项目 PNG")
    args = parser.parse_args()
    workbench = Path(__file__).resolve().parent
    png_root = workbench.parent / "assets/icons"
    source_root = args.source.resolve() if args.source else png_root
    destination = workbench / "src/portraits"
    prepared: list[tuple[Path, bytes]] = []
    count = png_total = webp_total = 0
    for folder, size in (("classes", 320), ("monsters", 224)):
        source = source_root / folder
        png_dir = png_root / folder
        png_dir.mkdir(parents=True, exist_ok=True)
        target_dir = destination if folder == "classes" else destination / "monsters"
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob("*.png")):
            png_target = png_dir / path.name
            target = target_dir / f"{path.stem}.webp"
            with Image.open(path) as image:
                mode = "RGBA" if "A" in image.getbands() else "RGB"
                thumbnail = ImageOps.contain(image.convert(mode), (size, size), Image.Resampling.NEAREST)
            png_data, webp_data = BytesIO(), BytesIO()
            thumbnail.save(png_data, "PNG", optimize=True)
            thumbnail.save(webp_data, "WEBP", lossless=True, method=6)
            prepared.extend(((png_target, png_data.getvalue()), (target, webp_data.getvalue())))
            png_total += png_data.getbuffer().nbytes
            webp_total += webp_data.getbuffer().nbytes
            count += 1
    for target, data in prepared:
        target.write_bytes(data)
    print(f"生成 {count} 组资源：PNG {png_total:,} 字节，WebP {webp_total:,} 字节，合计 {png_total + webp_total:,} 字节")


if __name__ == "__main__":
    main()
