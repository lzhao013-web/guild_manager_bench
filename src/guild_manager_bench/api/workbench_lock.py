"""进程退出时由操作系统释放的单机文件锁，不用 PID 猜测锁是否失效。"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


class FileLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file = None

    def __enter__(self):
        owner = self.path.open("a+b")
        owner.seek(0, 2)
        if owner.tell() == 0:
            owner.write(b"0")
            owner.flush()
        owner.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            owner.close()
            raise
        self.file = owner
        return self

    def __exit__(self, *args) -> None:
        if self.file is None:
            return
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.file = None


@contextmanager
def archive_gate(directory: Path):
    directory = directory.resolve()
    locks = directory.parent / ".locks"
    if locks.resolve() != locks:
        raise ValueError("存档锁目录不能使用链接")
    locks.mkdir(exist_ok=True)
    with FileLease(locks / f"{directory.name}.lock"):
        yield


@contextmanager
def archive_lease(directory: Path):
    with archive_gate(directory), FileLease(directory / "workbench.lock"):
        yield
