"""跨进程任务锁，防止采集器或调度器重复运行。"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def process_lock(path: Path, *, blocking: bool = False) -> Iterator[TextIO]:
    """持有基于 flock 的进程锁；非阻塞模式下冲突会抛出 RuntimeError。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            raise RuntimeError(f"任务已在运行，锁文件: {path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
