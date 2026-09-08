"""程序说明：验证 Inspector 不会用空采集结果覆盖已有 JSON。"""

from pathlib import Path
import tempfile
import unittest

from tools.t1_inspector import (
    _append_capture_if_nonempty,
    _load_capture,
    _write_capture_if_nonempty,
)


class InspectorSaveTests(unittest.TestCase):
    """覆盖采集文件的安全保存规则。"""

    def test_empty_capture_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "capture.json"
            output_path.write_text("existing", encoding="utf-8")

            saved = _write_capture_if_nonempty(output_path, [])

            self.assertFalse(saved)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "existing")

    def test_load_capture_preserves_existing_events_for_next_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "capture.json"
            _write_capture_if_nonempty(
                output_path,
                [
                    _sample_capture_event(),
                ],
            )

            events = _load_capture(output_path)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].button, "Home")
            self.assertEqual(events[0].raw_data_hex, "02 23 02")

    def test_append_capture_keeps_old_events_and_adds_only_new_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "capture.json"
            old_event = _sample_capture_event()
            new_event = _sample_capture_event()
            new_event = new_event.__class__(
                timestamp_utc="2026-09-08T08:30:00.000+00:00",
                button="Power",
                raw_input_type=2,
                collection="COL03",
                device_family="T1-Remote/COL03",
                raw_data_hex="03 01",
            )
            _write_capture_if_nonempty(output_path, [old_event])

            saved = _append_capture_if_nonempty(output_path, [new_event])

            self.assertTrue(saved)
            events = _load_capture(output_path)
            self.assertEqual([event.button for event in events], ["Home", "Power"])


def _sample_capture_event():
    """构造最小的现有采集事件，供文件恢复测试使用。"""

    from t1remote.core.capture_scope import CaptureEvent

    return CaptureEvent(
        timestamp_utc="2026-09-08T08:29:38.139+00:00",
        button="Home",
        raw_input_type=2,
        collection="COL02",
        device_family="T1-Remote/COL02",
        raw_data_hex="02 23 02",
    )
