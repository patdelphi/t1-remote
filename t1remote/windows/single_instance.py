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
import time


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


def activate_window_by_title(
    title: str,
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.05,
) -> bool:
    """查找旧实例窗口并恢复到前台。"""

    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("窗口标题不能为空")
    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise ValueError("窗口唤起参数必须为有效的正数")
    if os.name != "nt":
        return False

    try:
        import win32con
        import win32gui
    except ImportError:
        # 非 Windows 打包环境可能没有 pywin32，不能影响单实例判断。
        return False

    deadline = time.monotonic() + timeout_seconds
    while True:
        matched_hwnds: list[int] = []

        def collect_window(hwnd: int, _extra: object) -> None:
            try:
                if win32gui.GetWindowText(hwnd).strip() == normalized_title:
                    matched_hwnds.append(hwnd)
            except Exception:
                # 单个窗口读取失败时继续检查其他窗口。
                pass

        try:
            win32gui.EnumWindows(collect_window, None)
            if matched_hwnds:
                hwnd = matched_hwnds[0]
                # Tk 的 withdraw 和 Windows 最小化都需要显式恢复显示。
                show_mode = (
                    win32con.SW_SHOW
                    if not win32gui.IsWindowVisible(hwnd)
                    else win32con.SW_RESTORE
                )
                win32gui.ShowWindow(hwnd, show_mode)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
                return True
        except Exception:
            # 窗口可能正处于销毁或重建过程中，按超时策略重试。
            pass

        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval_seconds)


__all__ = ["SingleInstanceGuard", "activate_window_by_title"]
