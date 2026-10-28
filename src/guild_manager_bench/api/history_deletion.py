"""先移出可见存储，再永久删除，避免服务重启恢复已删记录。"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from guild_manager_bench.api.workbench_lock import FileLease, archive_gate


class ArchiveBusyError(RuntimeError):
    pass


def history_directory(root: Path, identity: str) -> Path:
    if not identity or identity in {".", ".."} or any(char in identity for char in "\\/:"):
        raise ValueError("无效的存档或运行编号")
    root = root.resolve()
    path = root / identity
    resolved = path.resolve()
    if resolved != path:
        raise ValueError("不能删除链接目录或存储目录之外的文件")
    return resolved


def stage_history_deletion(archive: Path | None, executions: list[Path]) -> list[Path]:
    sources = [path for path in ([archive] if archive is not None else []) + executions if path.exists()]
    operation = uuid4().hex
    staged: list[tuple[Path, Path]] = []
    containers: list[Path] = []
    with ExitStack() as stack:
        if archive is not None and archive.exists():
            try:
                stack.enter_context(archive_gate(archive))
                with FileLease(archive / "workbench.lock"):
                    pass
            except OSError as exc:
                raise ArchiveBusyError("存档被占用或暂不可访问，请稍后重试") from exc
        try:
            for source in sources:
                staging = history_directory(source.parent, ".deleting") / operation
                if staging not in containers:
                    staging.mkdir(parents=True)
                    containers.append(staging)
                target = staging / source.name
                source.rename(target)
                staged.append((source, target))
        except (OSError, ValueError):
            for source, target in reversed(staged):
                target.rename(source)
            raise
    return containers
