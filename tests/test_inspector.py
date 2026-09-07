"""程序说明：验证 Inspector 不会用空采集结果覆盖已有 JSON。"""

from pathlib import Path
import tempfile
import unittest

from tools.t1_inspector import _write_capture_if_nonempty


class InspectorSaveTests(unittest.TestCase):
    """覆盖采集文件的安全保存规则。"""

    def test_empty_capture_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "capture.json"
            output_path.write_text("existing", encoding="utf-8")

            saved = _write_capture_if_nonempty(output_path, [])

            self.assertFalse(saved)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "existing")

