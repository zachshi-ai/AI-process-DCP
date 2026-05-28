"""
仅用于 Windows 的 fcntl 兼容层。

说明：
Python 标准库的 fcntl 仅在类 Unix 系统上存在；但本项目的 crawler 会直接 import fcntl 并使用 flock。
为让 Windows 上的后端可运行（并可被 PyInstaller 打包），这里提供一个最小可用的 flock/常量实现，
内部用 msvcrt.locking 来模拟“文件互斥锁”。
"""

from __future__ import annotations

import errno
import msvcrt

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(fd: int, operation: int) -> None:
    """
    用 Windows 的 msvcrt.locking 模拟 fcntl.flock。

    参数：
    - fd: 文件描述符（通常来自 fileobj.fileno()）
    - operation: 锁操作（LOCK_EX/LOCK_UN 等，可与 LOCK_NB 按位或）
    """
    try:
        if operation & LOCK_UN:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
            raise BlockingIOError(e.errno, e.strerror) from e
        raise

