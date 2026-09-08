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
            replacement = MappingConfig(
                mappings={
                    **MappingConfig.default().mappings,
                    "OK": KeyAction("key", "SPACE"),
                }
            )
            save_mapping_config(path, replacement)

            self.assertTrue(watcher.check_once())
            self.assertFalse(watcher.check_once())
            self.assertEqual(loaded[0].mappings["OK"].key, "SPACE")
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
