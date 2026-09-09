"""程序说明：验证 T1 Mapping 主前台可以创建并显示诊断页。"""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest

from tools.t1_app import APP_ICON_PATH, MappingMonitorApp


class MappingAppGuiTests(unittest.TestCase):
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
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
