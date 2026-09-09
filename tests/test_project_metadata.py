"""程序说明：验证 Python 项目元数据和 Windows 构建入口存在。"""

from __future__ import annotations

from pathlib import Path
import tomllib
import unittest

from tools.t1_app import MAIN_TAB_LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProjectMetadataTests(unittest.TestCase):
    def test_pyproject_declares_supported_entry_points(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
            document = tomllib.load(file)
        scripts = document["project"]["scripts"]

        self.assertIn("t1-remote", scripts)
        self.assertIn("t1-remote-mapping-test", scripts)
        self.assertIn("pywin32>=306", document["project"]["dependencies"])

    def test_windows_build_script_and_documentation_exist(self) -> None:
        self.assertTrue((PROJECT_ROOT / "tools" / "build_windows.ps1").exists())
        self.assertTrue((PROJECT_ROOT / "Docs" / "build-windows.md").exists())

    def test_main_app_declares_three_integrated_tabs(self) -> None:
        self.assertEqual(MAIN_TAB_LABELS, ("捕获", "Mapping 设置", "Mapping 服务"))


if __name__ == "__main__":
    unittest.main()
