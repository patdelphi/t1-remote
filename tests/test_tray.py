"""程序说明：验证托盘组件在未启动原生窗口前的状态边界。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from t1remote.windows.tray import NIM_ADD, WM_LBUTTONUP, WM_NULL, TrayIcon


class TrayTests(unittest.TestCase):
    def test_tray_starts_stopped_and_validates_title(self) -> None:
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)
        self.assertFalse(tray.is_running)
        tray.stop()

    def test_tray_keeps_custom_icon_path(self) -> None:
        icon_path = Path("assets") / "t1-remote-icon.ico"
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, icon_path=icon_path)
        self.assertEqual(tray.icon_path, str(icon_path))
        red_path = Path("assets") / "t1-remote-icon-red.ico"
        tray.set_icon(red_path)
        self.assertEqual(tray.icon_path, str(red_path))

    def test_empty_title_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrayIcon(" ", lambda: None, lambda: None)

    def test_taskbar_recreation_readds_icon(self) -> None:
        """任务栏重启后，托盘窗口应重新注册通知区域图标。"""

        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)
        tray._hwnd = 123
        tray._icon_handle = 456
        tray._taskbar_created_message = 789
        with patch("win32gui.Shell_NotifyIcon") as notify_icon:
            with patch("win32gui.DefWindowProc", return_value=0):
                tray._window_proc(123, 789, 0, 0)
        self.assertEqual(notify_icon.call_args.args[0], NIM_ADD)

    def test_single_click_shows_window_without_destroying_tray(self) -> None:
        shown: list[bool] = []
        tray = TrayIcon("T1 Remote", lambda: shown.append(True), lambda: None)

        with patch("win32gui.DefWindowProc", return_value=0):
            tray._window_proc(123, tray._taskbar_created_message or 0, 0, 0)
            tray._window_proc(123, 0x0401, 0, WM_LBUTTONUP)

        self.assertEqual(shown, [True])

    def test_popup_does_not_close_tray_message_window(self) -> None:
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)

        with patch("win32gui.CreatePopupMenu", return_value=1), patch(
            "win32gui.AppendMenu"
        ), patch("win32gui.GetCursorPos", return_value=(10, 20)), patch(
            "win32gui.SetForegroundWindow"
        ), patch("win32gui.TrackPopupMenu"), patch(
            "win32gui.PostMessage"
        ) as post_message:
            tray._show_menu(123)

        post_message.assert_called_once_with(123, WM_NULL, 0, 0)


if __name__ == "__main__":
    unittest.main()
