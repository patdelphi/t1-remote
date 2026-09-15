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

    def test_release_workflow_declares_app_driver_and_install_entries(self) -> None:
        """发布入口必须同时覆盖用户态 App、原生驱动和安装生命周期。"""

        release_script = PROJECT_ROOT / "tools" / "build_release.ps1"
        install_script = PROJECT_ROOT / "tools" / "install_release.ps1"
        uninstall_script = PROJECT_ROOT / "tools" / "uninstall_release.ps1"
        launcher = PROJECT_ROOT / "tools" / "start_release.bat"
        release_doc = PROJECT_ROOT / "Docs" / "release.md"

        for path in (
            release_script,
            install_script,
            uninstall_script,
            launcher,
            release_doc,
        ):
            self.assertTrue(path.exists(), path)

        release_text = release_script.read_text(encoding="utf-8-sig")
        self.assertIn('Join-Path $packageRoot "App"', release_text)
        self.assertIn('Join-Path $packageRoot "Driver"', release_text)
        self.assertIn('Join-Path $packageRoot "Native"', release_text)
        self.assertIn('$buildRoot = Join-Path $releaseOutputRoot ".build-$packageName"', release_text)
        self.assertIn("SHA256SUMS.txt", release_text)
        self.assertIn("Scripts/pyinstaller.exe", release_text)
        self.assertIn("APPDATA", release_text)
        self.assertIn("--icon", release_text)

        install_text = install_script.read_text(encoding="utf-8-sig")
        self.assertIn("pnputil.exe", install_text)
        self.assertIn("Start-T1Remote.bat", install_text)
        self.assertIn("$releaseRoot = $PSScriptRoot", install_text)
        # 交互式安装引导：环境检测、安装项选择、VB-CABLE 可选安装。
        self.assertIn("NonInteractive", install_text)
        self.assertIn("Show-InstallMenu", install_text)
        self.assertIn("VB-CABLE", install_text)
        self.assertIn("VBCABLE_Setup", install_text)

        uninstall_text = uninstall_script.read_text(encoding="utf-8-sig")
        self.assertIn("/delete-driver", uninstall_text)
        self.assertIn("ProgramFiles", uninstall_text)

    def test_release_build_packages_vb_cable_installer(self) -> None:
        """发布包构建脚本必须支持把 VB-CABLE 安装器一起打包进 VBCable/。"""

        release_script = PROJECT_ROOT / "tools" / "build_release.ps1"
        release_text = release_script.read_text(encoding="utf-8-sig")
        self.assertIn("VbCableInstaller", release_text)
        self.assertIn('"VBCable"', release_text)
        self.assertIn("vb_cable_installer", release_text)

        release_doc = PROJECT_ROOT / "Docs" / "release.md"
        doc_text = release_doc.read_text(encoding="utf-8-sig")
        self.assertIn("VBCable/", doc_text)

    def test_main_app_declares_integrated_tabs(self) -> None:
        self.assertEqual(MAIN_TAB_LABELS, ("捕获", "Mapping 设置", "Mapping 服务", "语音测试"))


if __name__ == "__main__":
    unittest.main()
