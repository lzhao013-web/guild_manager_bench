"""下载官方网页字体及授权文件，生成仅引用本地资源的字体声明。"""

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public" / "fonts"
WEIGHTS = (400, 500, 600, 700)
MISANS_CSS = "https://font.sec.miui.com/font/css?family=MiSans:{weight}:Chinese_Simplify,Latin&display=swap"
MISANS_LICENSE = "https://hyperos.mi.com/font-download/MiSans%E5%AD%97%E4%BD%93%E7%9F%A5%E8%AF%86%E4%BA%A7%E6%9D%83%E8%AE%B8%E5%8F%AF%E5%8D%8F%E8%AE%AE.pdf"
JETBRAINS = "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/v2.304"


def read_url(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return response.read()


def save_download(item: tuple[str, Path, bytes]) -> int:
    url, path, signature = item
    data = read_url(url)
    if not data.startswith(signature):
        raise ValueError(f"资源格式与预期不符：{url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def main() -> None:
    declarations = ["/* 官方字体文件原样保存。来源与授权见 public/fonts/NOTICE.txt。 */"]
    downloads: list[tuple[str, Path, bytes]] = []
    for weight in WEIGHTS:
        css = read_url(MISANS_CSS.format(weight=weight)).decode("utf-8")
        blocks = re.findall(r"@font-face\s*\{([^}]+)\}", css)
        if not blocks:
            raise ValueError(f"官方 MiSans {weight} 样式中未找到字体声明")
        for block in blocks:
            source = re.search(r'url\("([^"]+\.woff2)"\)', block)
            if source is None:
                raise ValueError(f"官方 MiSans {weight} 声明缺少 WOFF2 文件")
            url = source.group(1)
            relative = Path("misans") / str(weight) / Path(urlparse(url).path).name
            downloads.append((url, PUBLIC / relative, b"wOF2"))
            local_url = f"/workbench/fonts/{relative.as_posix()}"
            block = re.sub(r"src:\s*[^;]+;", f'src: url("{local_url}") format("woff2");', block, count=1)
            declarations.append("@font-face {" + block + "}")

    for weight, name in zip(WEIGHTS, ("Regular", "Medium", "SemiBold", "Bold")):
        relative = Path("jetbrains-mono") / f"JetBrainsMono-{name}.woff2"
        downloads.append((f"{JETBRAINS}/fonts/webfonts/{relative.name}", PUBLIC / relative, b"wOF2"))
        declarations.append(
            '@font-face {\n  font-family: "JetBrains Mono";\n  font-style: normal;\n'
            f"  font-weight: {weight};\n  font-display: swap;\n"
            f'  src: url("/workbench/fonts/{relative.as_posix()}") format("woff2");\n}}'
        )

    downloads.extend([
        (MISANS_LICENSE, PUBLIC / "misans" / "LICENSE.pdf", b"%PDF"),
        (f"{JETBRAINS}/OFL.txt", PUBLIC / "jetbrains-mono" / "OFL.txt", b"Copyright"),
    ])
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(save_download, downloads))
    (ROOT / "src" / "fonts.css").write_text("\n".join(declarations) + "\n", encoding="utf-8")
    print(f"已保存 {len(downloads) - 2} 个 WOFF2 文件和 2 份许可证，共 {sum(sizes):,} 字节。")


if __name__ == "__main__":
    main()
