"""程序说明：验证 T1 Mapping 主前台可以创建并显示诊断页。"""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
import unittest

from tools.t1_app import MappingMonitorApp


class MappingAppGuiTests(unittest.TestCase):
    def test_monitor_window_builds_without_starting_device_session(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = MappingMonitorApp(root, Path("config") / "t1-key-mapping.json")
            root.update()
            self.assertEqual(app.root.title(), "T1 Remote Mapping")
            self.assertTrue(app.recent_tree.exists(""))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
