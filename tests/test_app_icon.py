"""程序说明：验证 Windows 任务栏图标可以从项目 ICO 文件加载。"""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from t1remote.windows.app_icon import destroy_icon_handles, load_icon_handles


class AppIconTests(unittest.TestCase):
    def test_project_icon_loads_as_small_and_large_windows_icons(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows 原生图标仅在 Windows 上验证")
        asset_root = Path(__file__).parents[1] / "assets"
        for filename in ("t1-remote-icon.ico", "t1-remote-icon-red.ico"):
            handles = load_icon_handles(asset_root / filename)
            try:
                self.assertEqual(len(handles), 2)
                self.assertTrue(all(handles))
            finally:
                destroy_icon_handles(handles)


if __name__ == "__main__":
    unittest.main()
