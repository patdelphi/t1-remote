"""程序说明：提供 Windows 原生通知区域图标和最小托盘菜单。"""

from __future__ import annotations

import os
import threading
from typing import Callable


WM_TRAYICON = 0x0400 + 1
WM_TRAYICON_SET_ICON = WM_TRAYICON + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
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
        icon_path: str | os.PathLike[str] | None = None,
    ) -> None:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("托盘标题不能为空")
        self.title = normalized_title
        self._on_show = on_show
        self._on_exit = on_exit
        self.icon_path = os.fspath(icon_path) if icon_path else None
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._icon_handle: int | None = None
        self._owns_icon = False
        self._owned_icon_handles: set[int] = set()
        self._taskbar_created_message: int | None = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error: Exception | None = None

    def set_icon(self, icon_path: str | os.PathLike[str] | None) -> None:
        """切换托盘图标；消息线程运行时通过窗口消息安全更新。"""

        self.icon_path = os.fspath(icon_path) if icon_path else None
        if not self._hwnd:
            return
        try:
            import win32gui

            win32gui.PostMessage(self._hwnd, WM_TRAYICON_SET_ICON, 0, 0)
        except Exception:
            # 托盘更新失败不应影响 Mapping 会话本身。
            pass

    @property
    def is_running(self) -> bool:
        """返回托盘消息线程是否正在运行。"""

        return bool(self._thread and self._thread.is_alive() and self._hwnd)

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

            self._taskbar_created_message = win32gui.RegisterWindowMessage(
                "TaskbarCreated"
            )
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
            self._replace_icon(operation=NIM_ADD)
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
                for icon_handle in tuple(self._owned_icon_handles):
                    win32gui.DestroyIcon(icon_handle)
            except Exception:
                pass
            self._hwnd = None
            self._icon_handle = None
            self._owns_icon = False
            self._owned_icon_handles.clear()
            self._taskbar_created_message = None

    def _replace_icon(self, *, operation: int = NIM_MODIFY) -> None:
        """在托盘消息线程中加载并替换图标句柄。"""

        import win32con
        import win32gui

        new_handle = None
        if self.icon_path:
            try:
                new_handle = win32gui.LoadImage(
                    0,
                    self.icon_path,
                    win32con.IMAGE_ICON,
                    16,
                    16,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
            except Exception:
                new_handle = None
        if not new_handle:
            new_handle = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            owns_new_handle = False
        else:
            owns_new_handle = True
        self._icon_handle = new_handle
        self._owns_icon = owns_new_handle
        if owns_new_handle:
            # Shell_NotifyIcon 可能仍在使用旧句柄；统一在线程退出时释放，避免图标偶发消失。
            self._owned_icon_handles.add(new_handle)
        if self._hwnd:
            win32gui.Shell_NotifyIcon(
                operation,
                (
                    self._hwnd,
                    0,
                    NIF_MESSAGE | NIF_ICON | NIF_TIP,
                    WM_TRAYICON,
                    new_handle,
                    self.title,
                ),
            )
    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32gui

        if self._taskbar_created_message and message == self._taskbar_created_message:
            # Explorer 重启后通知区域会清空，必须重新注册图标。
            self._replace_icon(operation=NIM_ADD)
        elif message == WM_TRAYICON:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._on_show()
            elif lparam == WM_RBUTTONUP:
                self._show_menu(hwnd)
        elif message == WM_TRAYICON_SET_ICON:
            self._replace_icon()
        elif message == WM_COMMAND:
            command = wparam & 0xFFFF
            if command == ID_SHOW:
                self._on_show()
            elif command == ID_EXIT:
                self._on_exit()
                win32gui.DestroyWindow(hwnd)
        elif message == WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
        elif message == WM_DESTROY:
            win32gui.PostQuitMessage(0)
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _show_menu(self, hwnd: int) -> None:
        import win32gui

        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, MF_STRING, ID_SHOW, "显示窗口")
        win32gui.AppendMenu(menu, MF_STRING, ID_EXIT, "退出")
        x, y = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(hwnd)
        win32gui.TrackPopupMenu(menu, TPM_RIGHTBUTTON, x, y, 0, hwnd, None)
        # 让菜单消息正确派发，同时避免销毁托盘消息窗口和通知区域图标。
        win32gui.PostMessage(hwnd, WM_NULL, 0, 0)


__all__ = ["NIM_ADD", "TrayIcon", "WM_LBUTTONUP", "WM_NULL"]
