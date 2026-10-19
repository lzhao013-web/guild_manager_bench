"""进程退出时由操作系统释放的单机文件锁，不用 PID 猜测锁是否失效。"""
from __future__ import annotations

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
