"""程序说明：验证 T1 Mapping 主前台可以创建并显示诊断页。"""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tools.t1_app import APP_ICON_PATH, MappingMonitorApp, _newest_first_records
from tools.t1_inspector_gui import CaptureTabController


class MappingAppGuiTests(unittest.TestCase):
    def test_diagnostics_records_are_newest_first(self) -> None:
        self.assertEqual(_newest_first_records(("old", "middle", "new")), ("new", "middle", "old"))

    def test_monitor_window_builds_without_starting_device_session(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            root.update()
            self.assertEqual(app.root.title(), "T1 Remote Mapping")
            self.assertFalse(app._dry_run_var.get())
            self.assertTrue(APP_ICON_PATH.exists())
            self.assertEqual(len(app._native_icon_handles), 2)
            self.assertTrue(app.recent_tree.exists(""))
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_capture_tab_keeps_save_and_clear_buttons_visible(self) -> None:
        """验证整合到主窗口后捕获页仍保留原有操作按钮。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            root.update_idletasks()

            def collect_buttons(widget: tk.Misc) -> list[str]:
                button_texts: list[str] = []
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Button):
                        button_texts.append(str(child.cget("text")))
                    button_texts.extend(collect_buttons(child))
                return button_texts

            capture_buttons = collect_buttons(app._tab_by_name["捕获"])
            self.assertIn("保存", capture_buttons)
            self.assertIn("清空记录", capture_buttons)
            self.assertNotIn("打开按键映射编辑器", capture_buttons)

            service_buttons = collect_buttons(app._tab_by_name["Mapping 服务"])
            self.assertNotIn("转到 Mapping 设置", service_buttons)
            self.assertNotIn("转到捕获", service_buttons)
            self.assertIn("退出应用", service_buttons)
            self.assertIn("复制最新30条", service_buttons)

            mapping_buttons = collect_buttons(app._tab_by_name["Mapping 设置"])
            self.assertIn("恢复当前键默认", mapping_buttons)
            self.assertIn("恢复全部默认", mapping_buttons)
            self.assertIn("mouse", app._mapping_editor.kind_radios)

            voice_buttons = collect_buttons(app._tab_by_name["语音测试"])
            self.assertIn("启动语音测试", voice_buttons)
            self.assertIn("停止语音测试", voice_buttons)
            self.assertIn("播放最近录音", voice_buttons)
            self.assertIn("扫描并预填", voice_buttons)

            self.assertIn("取消选择", capture_buttons)
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_voice_continuous_switch_starts_session_with_zero_duration(self) -> None:
        """持续收音开关勾选后禁用秒数输入，并按 0 秒（不限时长）启动会话。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            root.update_idletasks()

            def collect_checkbuttons(widget: tk.Misc) -> list[str]:
                texts: list[str] = []
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Checkbutton):
                        texts.append(str(child.cget("text")))
                    texts.extend(collect_checkbuttons(child))
                return texts

            self.assertIn(
                "持续收音（不限时长）",
                collect_checkbuttons(app._tab_by_name["语音测试"]),
            )

            class _RecordingVoiceSession:
                def __init__(self) -> None:
                    self.calls: list[dict[str, object]] = []

                def start(self, address: str, **kwargs: object) -> None:
                    self.calls.append({"address": address, **kwargs})

                def stop(self, *, wait: bool = True) -> None:
                    pass

            recorder = _RecordingVoiceSession()
            app._voice_session = recorder
            app._voice_address_var.set("test-address")
            app._voice_duration_var.set("25")

            app._voice_continuous_var.set(True)
            app._on_voice_continuous_toggle()
            self.assertEqual(str(app._voice_duration_entry.cget("state")), "disabled")
            app.start_voice_session()

            app._voice_continuous_var.set(False)
            app._on_voice_continuous_toggle()
            self.assertEqual(str(app._voice_duration_entry.cget("state")), "normal")
            app.start_voice_session()

            self.assertEqual(recorder.calls[0]["address"], "test-address")
            self.assertEqual(recorder.calls[0]["duration_seconds"], 0.0)
            self.assertEqual(recorder.calls[1]["duration_seconds"], 25.0)
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_copy_diagnostics_works_without_a_running_session(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            with patch.object(root, "clipboard_clear") as clear, patch.object(
                root, "clipboard_append"
            ) as append, patch.object(root, "update") as update:
                app._copy_diagnostics()

            clear.assert_called_once_with()
            append.assert_called_once()
            update.assert_called_once_with()
            self.assertTrue(append.call_args.args[0].startswith("时间,结果,按键,状态,动作,说明"))
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_capture_tab_controller_forwards_mapping_lifecycle(self) -> None:
        calls: list[object] = []
        controller = CaptureTabController(
            lambda: calls.append("cleanup"),
            lambda active: calls.append(active),
        )

        controller.set_mapping_active(True)
        controller()

        self.assertEqual(calls, [True, "cleanup"])

    def test_tray_loss_restores_hidden_main_window(self) -> None:
        """托盘线程退出后，隐藏的主窗口应自动恢复。"""

        class StoppedTray:
            is_running = False

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            app.tray = StoppedTray()
            app._monitor_tray()
            root.update_idletasks()
            self.assertEqual(root.state(), "normal")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_close_button_keeps_main_window_taskbar_entry_minimized(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            app.tray = type("RunningTray", (), {"is_running": True})()
            with patch.object(root, "iconify") as iconify, patch.object(
                root, "withdraw"
            ) as withdraw:
                app.minimize_to_tray()

            iconify.assert_called_once_with()
            withdraw.assert_not_called()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_main_frontend_can_schedule_mapping_autostart(self) -> None:
        """主前台的自动启动选项应在 Tk 事件循环中调用 Mapping。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            with patch.object(MappingMonitorApp, "scan_voice_devices"), patch.object(
                MappingMonitorApp, "start_session"
            ) as start_session:
                app = MappingMonitorApp(
                    root,
                    Path("config") / "t1-key-mapping.json",
                    auto_start_mapping=True,
                )
                root.after(300, root.quit)
                root.mainloop()
                start_session.assert_called_once_with()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_exit_button_requires_confirmation(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            with patch("tools.t1_app.messagebox.askyesno", return_value=False):
                app.request_exit()
            self.assertFalse(app._closed)
            with patch("tools.t1_app.messagebox.askyesno", return_value=True):
                with patch.object(app, "close") as close:
                    app.request_exit()
            close.assert_called_once_with()
        finally:
            if app is not None and not app._closed:
                app.close()
            elif app is None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
