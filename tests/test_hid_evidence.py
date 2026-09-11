"""程序说明：验证 HID 报告类型分类、Report ID 解析和按键时序证据。"""

from __future__ import annotations

import unittest

from t1remote.core.hid_evidence import (
    HidReportSample,
    build_hid_evidence_report,
    classify_hid_report,
)


class HidEvidenceTests(unittest.TestCase):
    """覆盖协议证据与业务按键语义分离的最小场景。"""

    def test_classifies_standard_pages_and_keeps_unknown_unknown(self) -> None:
        self.assertEqual(
            classify_hid_report(collection="COL01", usage_page=0x07, usage=0x04),
            "keyboard",
        )
        self.assertEqual(
            classify_hid_report(collection="COL02", usage_page=0x0C, usage=0x0221),
            "consumer",
        )
        self.assertEqual(
            classify_hid_report(collection="COL03", usage_page=0x01, usage=0x81),
            "system",
        )
        self.assertEqual(
            classify_hid_report(collection="COL04", usage_page=0x01, usage=0x02),
            "mouse",
        )
        self.assertEqual(
            classify_hid_report(collection="COL05", usage_page=0xFF00, usage=0x01),
            "vendor",
        )
        self.assertEqual(
            classify_hid_report(collection="COL99", usage_page=None, usage=None),
            "unknown",
        )

    def test_descriptor_confirms_report_id_and_pairs_press_release(self) -> None:
        descriptor = bytes.fromhex(
            "05 0C 09 01 A1 01 85 02 75 01 95 01 09 E9 81 02 C0"
        )
        samples = (
            HidReportSample(
                timestamp_100ns=1_000_000,
                collection="COL02",
                usage_page=0x0C,
                usage=0xE9,
                report=bytes.fromhex("02 E9 00"),
                state="down",
            ),
            HidReportSample(
                timestamp_100ns=2_200_000,
                collection="COL02",
                usage_page=0x0C,
                usage=0,
                report=bytes.fromhex("02 00 00"),
                state="up",
            ),
        )

        evidence = build_hid_evidence_report(
            samples,
            report_descriptor=descriptor,
        )

        self.assertEqual(evidence["descriptor_status"], "descriptor_available")
        self.assertEqual(evidence["events"][0]["report_id"], 2)
        self.assertEqual(evidence["events"][0]["report_category"], "consumer")
        self.assertEqual(evidence["events"][0]["raw_report_hex"], "02 e9 00")
        self.assertEqual(evidence["events"][0]["event_kind"], "down")
        self.assertEqual(evidence["events"][1]["event_kind"], "up")
        self.assertEqual(evidence["events"][1]["down_up_duration_ms"], 120)
        self.assertEqual(evidence["actions"][0]["usage"], 0xE9)
        self.assertEqual(evidence["actions"][0]["duration_ms"], 120)

    def test_repeated_down_reports_are_marked_repeat(self) -> None:
        samples = (
            HidReportSample(
                timestamp_100ns=0,
                collection="COL02",
                usage_page=0x0C,
                usage=0x223,
                report=bytes.fromhex("02 23 02"),
                state="down",
            ),
            HidReportSample(
                timestamp_100ns=500_000,
                collection="COL02",
                usage_page=0x0C,
                usage=0x223,
                report=bytes.fromhex("02 23 02"),
                state="down",
            ),
            HidReportSample(
                timestamp_100ns=1_500_000,
                collection="COL02",
                usage_page=0x0C,
                usage=0,
                report=bytes.fromhex("02 00 00"),
                state="up",
            ),
        )

        evidence = build_hid_evidence_report(samples)

        self.assertEqual(
            [event["event_kind"] for event in evidence["events"]],
            ["down", "repeat", "up"],
        )
        self.assertEqual(evidence["actions"][0]["repeat_count"], 1)
        self.assertEqual(evidence["actions"][0]["duration_ms"], 150)

    def test_missing_descriptor_does_not_guess_report_id_or_voice_name(self) -> None:
        samples = (
            HidReportSample(
                collection="COL02",
                usage_page=0x0C,
                usage=0x0221,
                report=bytes.fromhex("02 21 02"),
                state="down",
            ),
        )

        evidence = build_hid_evidence_report(samples)
        serialized = str(evidence)

        self.assertEqual(evidence["descriptor_status"], "descriptor_unavailable")
        self.assertIsNone(evidence["events"][0]["report_id"])
        self.assertEqual(evidence["events"][0]["usage_page"], 0x0C)
        self.assertEqual(evidence["events"][0]["usage"], 0x0221)
        self.assertNotIn("Voice", serialized)


if __name__ == "__main__":
    unittest.main()
