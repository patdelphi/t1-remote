"""程序说明：验证驱动事件采集的状态解析和脱敏 JSON 结构。"""

from __future__ import annotations

import unittest

from t1remote.windows.driver_bridge import DriverInputEvent
from tools.t1_driver_inspector import build_capture_document, serialize_driver_event


class DriverInspectorTests(unittest.TestCase):
    """覆盖 COL02/COL03 驱动事件的最小采集夹具。"""

    def test_serializes_consumer_press_and_redacts_device_identity(self) -> None:
        event = DriverInputEvent(
            sequence=7,
            usage_page=0x0C,
            usage=0x223,
            collection="COL02",
            report=b"\x02\x23\x02",
            timestamp_100ns=123,
        )

        record = serialize_driver_event(
            event,
            "Home",
            timestamp_utc="2026-09-08T11:00:00+00:00",
        )

        self.assertEqual(record["state"], "down")
        self.assertEqual(record["usage_page_hex"], "0x000C")
        self.assertEqual(record["raw_data_hex"], "02 23 02")
        self.assertNotIn("VID_", str(record))
        self.assertNotIn("PID_", str(record))

    def test_system_control_release_is_detected(self) -> None:
        event = DriverInputEvent(
            sequence=8,
            usage_page=0x01,
            usage=0,
            collection="COL03",
            report=b"\x03\x00",
        )

        record = serialize_driver_event(event, "Power")

        self.assertEqual(record["state"], "up")
        self.assertEqual(record["device_family"], "T1-Remote/COL03")

    def test_document_has_driver_source_and_events(self) -> None:
        document = build_capture_document([{"button": "Home"}])

        self.assertEqual(document["source"], "T1Bridge_ReadEvent")
        self.assertEqual(document["events"], [{"button": "Home"}])


if __name__ == "__main__":
    unittest.main()
