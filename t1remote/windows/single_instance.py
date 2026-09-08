"""程序说明：提供 T1 工具的跨平台单实例保护。

Windows 使用命名 Mutex，其他平台使用临时目录中的独占锁文件，便于运行
纯 Python 测试。释放时只关闭当前进程持有的句柄或锁文件。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import tempfile


ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """保护同一应用名只启动一个活动进程。"""

    def __init__(self, name: str) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("单实例名称不能为空")
        self.name = normalized_name
        self._mutex: int | None = None
        self._lock_fd: int | None = None
        self._lock_path: Path | None = None

    def acquire(self) -> bool:
        """尝试获取实例；当前对象重复调用时返回 True。"""

        if self._mutex or self._lock_fd is not None:
            return True
        if os.name == "nt":
            return self._acquire_mutex()
        return self._acquire_lock_file()

    def release(self) -> None:
        """释放当前对象持有的实例资源。"""

        if self._mutex:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(self._mutex))
            self._mutex = None
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
            if self._lock_path:
                try:
                    self._lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # 锁句柄已释放，残留临时文件不会影响 Windows Mutex 路径。
                    pass
                self._lock_path = None

    def _acquire_mutex(self) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel32.CreateMutexW(None, False, self.name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex)
            return False
        self._mutex = int(mutex)
        return True

    def _acquire_lock_file(self) -> bool:
        digest = hashlib.sha256(self.name.encode("utf-8")).hexdigest()
        path = Path(tempfile.gettempdir()) / f"t1remote-{digest}.lock"
        try:
            self._lock_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            return False
        self._lock_path = path
        return True


__all__ = ["SingleInstanceGuard"]
