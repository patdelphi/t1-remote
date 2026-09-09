"""程序说明：验证托盘组件在未启动原生窗口前的状态边界。"""

from __future__ import annotations

import unittest
from pathlib import Path

from t1remote.windows.tray import TrayIcon


class TrayTests(unittest.TestCase):
    def test_tray_starts_stopped_and_validates_title(self) -> None:
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None)
        self.assertFalse(tray.is_running)
        tray.stop()

    def test_tray_keeps_custom_icon_path(self) -> None:
        icon_path = Path("assets") / "t1-remote-icon.ico"
        tray = TrayIcon("T1 Remote", lambda: None, lambda: None, icon_path=icon_path)
        self.assertEqual(tray.icon_path, str(icon_path))

    def test_empty_title_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrayIcon(" ", lambda: None, lambda: None)


if __name__ == "__main__":
    unittest.main()
