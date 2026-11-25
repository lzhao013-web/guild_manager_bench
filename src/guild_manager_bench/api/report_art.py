"""只嵌入受控缩略图，同一图片在一个导出文件里只编码一次。"""
from base64 import b64encode
import json
from pathlib import Path


_ROOT = Path(__file__).with_name("report_assets")
_PATHS = {(folder, path.stem): path for folder in ("portraits", "equipment")
          for path in (_ROOT / folder).glob("*.webp")}
_EQUIPMENT = json.loads((_ROOT / "equipment-map.json").read_text(encoding="utf-8"))
# Base64 后图片内容最多约 171 KiB。超出预算只省略图像，不省略角色或装备信息。
IMAGE_BUDGET = 128 * 1024


class ReportArt:
    def __init__(self) -> None:
        self._images: dict[tuple[str, str], tuple[str, bytes]] = {}
        self._size = 0

    def image(self, folder: str, identity: str | None, class_name: str) -> str:
        if folder == "equipment":
            identity = _EQUIPMENT.get(identity, identity)
        key = (folder, identity)
        # 由打包的文件清单限定路径，存档中的 ID 不能指定路径或 URL。
        path = _PATHS.get(key)
        if path is None:
            return f'<span class="{class_name} art-placeholder" aria-hidden="true"></span>'
        if key not in self._images:
            image = path.read_bytes()
            if self._size + len(image) > IMAGE_BUDGET:
                return f'<span class="{class_name} art-placeholder" aria-hidden="true"></span>'
            self._images[key] = (f"art-{len(self._images)}", image)
            self._size += len(image)
        name, _ = self._images[key]
        return f'<span class="{class_name} art {name}" aria-hidden="true"></span>'

    def styles(self) -> str:
        return "\n".join(f'.{name}{{background-image:url("data:image/webp;base64,{b64encode(image).decode("ascii")}")}}'
                         for name, image in self._images.values())
