"""程序说明：提供 Windows 原生通知区域图标和最小托盘菜单。"""

from __future__ import annotations

import os
import threading
from typing import Callable


WM_TRAYICON = 0x0400 + 1
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
ID_SHOW = 1001
ID_EXIT = 1002


class TrayIcon:
    """在后台线程维护一个 Windows 通知区域图标。"""

    def __init__(
        self,
        title: str,
        on_show: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("托盘标题不能为空")
        self.title = normalized_title
        self._on_show = on_show
        self._on_exit = on_exit
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error: Exception | None = None

    @property
    def is_running(self) -> bool:
        """返回托盘消息线程是否正在运行。"""

        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        """创建托盘图标并启动消息循环。"""

        if os.name != "nt":
            raise RuntimeError("Windows 托盘只能在 Windows 上运行")
        if self.is_running:
            return
        self._ready.clear()
        self._stop_requested.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="t1-remote-tray",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise TimeoutError("等待 Windows 托盘启动超时")
        if self._startup_error:
            raise RuntimeError("Windows 托盘启动失败") from self._startup_error

    def stop(self) -> None:
        """关闭托盘图标和后台消息循环。"""

        self._stop_requested.set()
        if self._hwnd:
            try:
                import win32gui

                win32gui.PostMessage(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _message_loop(self) -> None:
        try:
            import win32api
            import win32con
            import win32gui

            class_name = f"T1RemoteTray_{id(self)}"
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = win32api.GetModuleHandle(None)
            window_class.lpszClassName = class_name
            window_class.lpfnWndProc = self._window_proc
            win32gui.RegisterClass(window_class)
            self._hwnd = win32gui.CreateWindowEx(
                0,
                class_name,
                self.title,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                window_class.hInstance,
                None,
            )
            icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            win32gui.Shell_NotifyIcon(
                NIM_ADD,
                (
                    self._hwnd,
                    0,
                    NIF_MESSAGE | NIF_ICON | NIF_TIP,
                    WM_TRAYICON,
                    icon,
                    self.title,
                ),
            )
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as error:
            self._startup_error = error
            self._ready.set()
        finally:
            try:
                import win32gui

                if self._hwnd:
                    win32gui.Shell_NotifyIcon(NIM_DELETE, (self._hwnd, 0))
            except Exception:
                pass
            self._hwnd = None

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32gui

        if message == WM_TRAYICON:
            if lparam == WM_LBUTTONDBLCLK:
                self._on_show()
            elif lparam == WM_RBUTTONUP:
                self._show_menu(hwnd)
        elif message == WM_COMMAND:
            command = wparam & 0xFFFF
            if command == ID_SHOW:
                self._on_show()
            elif command == ID_EXIT:
                self._on_exit()
                win32gui.DestroyWindow(hwnd)
        elif message == WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _show_menu(self, hwnd: int) -> None:
        import win32gui

        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, MF_STRING, ID_SHOW, "显示窗口")
        win32gui.AppendMenu(menu, MF_STRING, ID_EXIT, "退出")
        x, y = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(hwnd)
        win32gui.TrackPopupMenu(menu, TPM_RIGHTBUTTON, x, y, 0, hwnd, None)
        win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)


__all__ = ["TrayIcon"]
