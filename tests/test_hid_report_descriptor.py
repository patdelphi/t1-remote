"""程序说明：验证 HID Report Descriptor 字段解析。"""

from __future__ import annotations

import unittest

from t1remote.core.hid_report_descriptor import (
    HidReportDescriptorError,
    parse_hid_report_descriptor,
)


class HidReportDescriptorTests(unittest.TestCase):
    def test_parses_input_fields_and_bit_offsets(self) -> None:
        descriptor = bytes.fromhex(
            "05 0C 09 01 A1 01 85 02 "
            "75 01 95 01 09 E9 81 02 "
            "75 07 95 01 81 01 C0"
        )

        parsed = parse_hid_report_descriptor(descriptor)

        self.assertEqual(len(parsed.fields), 2)
        first, second = parsed.fields
        self.assertEqual(first.report_type, "input")
        self.assertEqual(first.report_id, 2)
        self.assertEqual(first.usage_page, 0x0C)
        self.assertEqual(first.usages, (0xE9,))
        self.assertEqual(first.bit_offset, 0)
        self.assertEqual(first.bit_width, 1)
        self.assertEqual(first.count, 1)
        self.assertEqual(second.bit_offset, 1)
        self.assertEqual(second.bit_width, 7)
        self.assertEqual(parsed.report_bit_lengths[("input", 2)], 8)

    def test_rejects_truncated_short_item(self) -> None:
        with self.assertRaises(HidReportDescriptorError):
            parse_hid_report_descriptor(bytes.fromhex("05"))

    def test_parses_usage_ranges_and_output_fields(self) -> None:
        descriptor = bytes.fromhex(
            "05 01 09 06 A1 01 85 01 75 08 95 02 "
            "19 00 29 7F 91 02 C0"
        )

        field = parse_hid_report_descriptor(descriptor).fields[0]

        self.assertEqual(field.report_type, "output")
        self.assertEqual(field.usage_min, 0)
        self.assertEqual(field.usage_max, 0x7F)
        self.assertEqual(field.count, 2)


if __name__ == "__main__":
    unittest.main()
