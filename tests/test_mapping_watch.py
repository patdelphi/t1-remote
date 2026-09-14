"""程序说明：验证 Key Mapping 配置文件监视器的热加载和错误边界。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from t1remote.core.key_mapping import KeyAction, MappingConfig, save_mapping_config
from t1remote.core.mapping_watch import MappingConfigWatcher


class MappingWatchTests(unittest.TestCase):
    def test_changed_file_is_loaded_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            loaded: list[MappingConfig] = []
            errors: list[Exception] = []
            watcher = MappingConfigWatcher(path, loaded.append, errors.append)

            self.assertFalse(watcher.check_once())
            # 基准与替换用等长文本动作：内容不同、大小相同，只比较 stat
            # 会漏检，必须靠内容摘要触发。
            base = dict(MappingConfig.default().mappings)
            base["Menu"] = KeyAction("text", text="AAAA")
            save_mapping_config(path, MappingConfig(mappings=base))
            self.assertTrue(watcher.check_once())
            original_size = path.stat().st_size
            changed = dict(base)
            changed["Menu"] = KeyAction("text", text="BBBB")
            save_mapping_config(path, MappingConfig(mappings=changed))
            self.assertEqual(path.stat().st_size, original_size)

            # 等长重写必须被检测到（内容摘要签名），并且只加载一次。
            self.assertTrue(watcher.check_once())
            self.assertFalse(watcher.check_once())
            self.assertEqual(loaded[-1].mappings["Menu"].text, "BBBB")
            self.assertEqual(errors, [])

    def test_invalid_file_reports_error_and_keeps_previous_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            errors: list[Exception] = []
            watcher = MappingConfigWatcher(path, lambda _config: None, errors.append)
            self.assertFalse(watcher.check_once())
            path.write_text("{invalid", encoding="utf-8")

            self.assertTrue(watcher.check_once())
            self.assertEqual(len(errors), 1)
            self.assertFalse(watcher.check_once())
            self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
