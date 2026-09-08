"""程序说明：验证脱敏采集夹具的离线验收报告。"""

from __future__ import annotations

import unittest

from t1remote.core.capture_scope import CaptureEvent
from tools.t1_capture_validate import build_validation_report


class CaptureValidateTests(unittest.TestCase):
    def test_report_contains_coverage_and_physical_mapping(self) -> None:
        report = build_validation_report(
            [
                CaptureEvent(
                    timestamp_utc="2026-09-08T12:00:00+00:00",
                    button="Home",
                    raw_input_type=2,
                    collection="COL02",
                    device_family="T1-Remote/COL02",
                    raw_data_hex="02 23 02",
                ),
            ]
        )

        self.assertEqual(report["event_count"], 1)
        self.assertIn("capture_coverage", report)
        self.assertEqual(report["physical_mapping"][9]["button"], "Home")
        self.assertFalse(report["complete"])


if __name__ == "__main__":
    unittest.main()
