"""程序说明：验证托盘组件在未启动原生窗口前的状态边界。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from t1remote.windows.tray import (
    MF_CHECKED,
    MF_SEPARATOR,
    MF_STRING,
    NIF_INFO,
    NIIF_INFO,
    NIM_ADD,
    NIM_MODIFY,
    TPM_RETURNCMD,
    TPM_RIGHTBUTTON,
    WM_COMMAND,
    WM_LBUTTONUP,
    WM_NULL,
    TrayIcon,
    TrayMenuItem,
)


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

    def test_notify_sends_balloon_with_info_flags(self) -> None:
        """托盘提示必须带 NIF_INFO 标志，否则不会显示气泡。"""

        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)
        tray._hwnd = 123
        tray._icon_handle = 456

        with patch("win32gui.Shell_NotifyIcon") as notify_icon:
            tray.notify("Mapping 已启动", level="info")

        operation, payload = notify_icon.call_args.args
        self.assertEqual(operation, NIM_MODIFY)
        self.assertTrue(payload[2] & NIF_INFO, payload)
        self.assertEqual(payload[6], "Mapping 已启动")
        self.assertEqual(payload[7], NIIF_INFO)

    def test_notify_is_noop_before_window_exists(self) -> None:
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)

        with patch("win32gui.Shell_NotifyIcon") as notify_icon:
            tray.notify("ignored")

        notify_icon.assert_not_called()

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

    def test_menu_appends_checked_state_from_callable(self) -> None:
        """勾选菜单项在弹出时读取当前状态，不缓存过期结果。"""

        state = {"on": False}
        item = TrayMenuItem(
            label="Mapping 开关",
            item_id=2001,
            on_select=lambda: None,
            is_checked=lambda: state["on"],
        )
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, menu_items=(item,))

        appended: list[tuple[object, ...]] = []
        with patch("win32gui.CreatePopupMenu", return_value=1), patch(
            "win32gui.AppendMenu", side_effect=lambda *args: appended.append(args)
        ), patch("win32gui.GetCursorPos", return_value=(10, 20)), patch(
            "win32gui.SetForegroundWindow"
        ), patch("win32gui.TrackPopupMenu"), patch("win32gui.PostMessage"):
            tray._show_menu(123)
            state["on"] = True
            tray._show_menu(123)

        mapping_flags = [
            args[1] for args in appended if args[2] == 2001
        ]
        self.assertEqual(mapping_flags[0] & MF_CHECKED, 0)
        self.assertEqual(mapping_flags[1] & MF_CHECKED, MF_CHECKED)

    def test_menu_item_command_invokes_callback(self) -> None:
        called: list[str] = []
        item = TrayMenuItem(
            label="重新加载映射配置",
            item_id=2002,
            on_select=lambda: called.append("reload"),
        )
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, menu_items=(item,))

        with patch("win32gui.DefWindowProc", return_value=0):
            tray._window_proc(123, WM_COMMAND, 2002, 0)

        self.assertEqual(called, ["reload"])

    def test_popup_dispatches_command_from_return_value(self) -> None:
        """托盘菜单用 TPM_RETURNCMD 返回值分发，不依赖 WM_COMMAND 投递。"""

        called: list[str] = []
        item = TrayMenuItem(
            label="Mapping 开关",
            item_id=2001,
            on_select=lambda: called.append("mapping"),
        )
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, menu_items=(item,))
        tracked: list[int] = []

        def fake_track_popup(_menu: object, flags: int, *_args: object) -> int:
            tracked.append(flags)
            return 2001

        with patch("win32gui.CreatePopupMenu", return_value=1), patch(
            "win32gui.AppendMenu"
        ), patch("win32gui.GetCursorPos", return_value=(10, 20)), patch(
            "win32gui.SetForegroundWindow"
        ), patch("win32gui.TrackPopupMenu", side_effect=fake_track_popup), patch(
            "win32gui.PostMessage"
        ):
            tray._show_menu(123)

        self.assertEqual(called, ["mapping"])
        self.assertTrue(tracked[0] & TPM_RETURNCMD, tracked)
        self.assertTrue(tracked[0] & TPM_RIGHTBUTTON, tracked)

    def test_popup_ignores_cancelled_selection(self) -> None:
        """用户点空白处取消菜单时（返回 0）不能触发任何命令。"""

        called: list[str] = []
        item = TrayMenuItem(
            label="Mapping 开关",
            item_id=2001,
            on_select=lambda: called.append("mapping"),
        )
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, menu_items=(item,))

        with patch("win32gui.CreatePopupMenu", return_value=1), patch(
            "win32gui.AppendMenu"
        ), patch("win32gui.GetCursorPos", return_value=(10, 20)), patch(
            "win32gui.SetForegroundWindow"
        ), patch("win32gui.TrackPopupMenu", return_value=0), patch(
            "win32gui.PostMessage"
        ):
            tray._show_menu(123)

        self.assertEqual(called, [])

    def test_menu_items_are_separated_from_show_and_exit(self) -> None:
        item = TrayMenuItem(label="Mapping 开关", item_id=2003, on_select=lambda: None)
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, menu_items=(item,))

        appended: list[tuple[object, ...]] = []
        with patch("win32gui.CreatePopupMenu", return_value=1), patch(
            "win32gui.AppendMenu", side_effect=lambda *args: appended.append(args)
        ), patch("win32gui.GetCursorPos", return_value=(10, 20)), patch(
            "win32gui.SetForegroundWindow"
        ), patch("win32gui.TrackPopupMenu"), patch("win32gui.PostMessage"):
            tray._show_menu(123)

        order = [args[2] if args[1] != MF_SEPARATOR else "sep" for args in appended]
        self.assertEqual(
            order,
            [1001, "sep", 2003, "sep", 1002],
        )
        # win32gui.AppendMenu 不接受 None 文本（真实调用会抛 TypeError），
        # mock 不会暴露这一点，因此在这里锁定参数类型。
        self.assertTrue(
            all(isinstance(args[3], str) for args in appended),
            appended,
        )


if __name__ == "__main__":
    unittest.main()
