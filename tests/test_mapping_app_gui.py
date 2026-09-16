"""程序说明：验证 T1 Mapping 主前台可以创建并显示诊断页。"""

from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tools.t1_app import (
    APP_ICON_PATH,
    MappingMonitorApp,
    _newest_first_records,
    _resolve_runtime_root,
)
from tools.t1_inspector_gui import CaptureTabController


class MappingAppGuiTests(unittest.TestCase):
    def test_runtime_root_uses_pyinstaller_internal_directory(self) -> None:
        """PyInstaller 包内的资源应从 _internal 目录读取。"""

        bundled_root = Path("C:/T1Remote/_internal")
        with patch.object(sys, "_MEIPASS", str(bundled_root), create=True):
            self.assertEqual(_resolve_runtime_root(), bundled_root)

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
            root.update()

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
            root.update()

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
            self.assertTrue(app._voice_continuous_var.get())
            self.assertEqual(str(app._voice_duration_entry.cget("state")), "disabled")

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
            root.update()
            self.assertEqual(root.state(), "normal")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_minimize_to_tray_hides_main_window_from_taskbar(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            app.tray = type("RunningTray", (), {"is_running": True})()
            with patch.object(root, "iconify") as iconify, patch.object(root, "withdraw") as withdraw:
                app.minimize_to_tray()

            withdraw.assert_called_once_with()
            iconify.assert_not_called()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_title_bar_minimize_is_forwarded_to_tray(self) -> None:
        """标题栏最小化完成后，主窗口应改为隐藏到托盘。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            with patch.object(root, "state", return_value="iconic"), patch.object(
                app, "minimize_to_tray"
            ) as minimize:
                app._on_window_unmap(object())
                root.update()

            minimize.assert_called_once_with()
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

    def test_tray_mapping_toggle_starts_session_when_idle(self) -> None:
        """托盘「Mapping 开关」在没有运行会话时启动 mapping。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            self.assertFalse(app._tray_mapping_active())
            with patch.object(app, "start_session") as start, patch.object(
                app, "stop_session"
            ) as stop:
                app._tray_toggle_mapping()
                root.update()
            start.assert_called_once_with()
            stop.assert_not_called()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_tray_mapping_toggle_stops_running_session(self) -> None:
        """托盘「Mapping 开关」在会话运行时是勾选态，再次点击停止。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            class _RunningSession:
                """运行中的假会话：close() 会调用 stop()，必须提供。"""

                def status(self) -> object:
                    return type("Status", (), {"state": "running"})()

                def stop(self) -> None:
                    pass

            app._session = _RunningSession()
            self.assertTrue(app._tray_mapping_active())
            with patch.object(app, "start_session") as start, patch.object(
                app, "stop_session"
            ) as stop:
                app._tray_toggle_mapping()
                root.update()
            stop.assert_called_once_with()
            start.assert_not_called()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_tray_reports_mapping_failure_instead_of_silence(self) -> None:
        """映射启动失败时必须用托盘气泡说明原因，不能静默无反应。"""

        class _RecordingTray:
            is_running = True

            def __init__(self) -> None:
                self.notices: list[tuple[str, str]] = []

            def notify(self, message: str, *, level: str = "info") -> None:
                self.notices.append((message, level))

        class _FailingSession:
            def __init__(self) -> None:
                self._status = type(
                    "Status",
                    (),
                    {
                        "state": "error",
                        "message": "T1Bridge_GetPreparsedData 失败，错误码：21",
                    },
                )()

            def status(self) -> object:
                return self._status

            def stop(self) -> None:
                pass

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            tray = _RecordingTray()
            app.tray = tray
            app._session = _FailingSession()

            app._report_mapping_result(True)

            self.assertEqual(len(tray.notices), 1)
            message, level = tray.notices[0]
            self.assertEqual(level, "error")
            self.assertIn("错误码：21", message)
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_tray_continuous_voice_toggle_requires_address(self) -> None:
        """托盘「持续收音」在地址为空时引导用户去语音页，不空转启动。"""

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            app._voice_address_var.set("")
            with patch.object(app, "start_voice_session") as start, patch.object(
                app, "stop_voice_session"
            ) as stop, patch.object(app, "show_window") as show:
                app._tray_toggle_continuous_voice()
                root.update()
            start.assert_not_called()
            stop.assert_not_called()
            show.assert_called_once_with()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
