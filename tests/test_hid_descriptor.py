"""程序说明：验证 HID Collection 能力摘要和脱敏输出逻辑。"""

from __future__ import annotations

import unittest

from t1remote.windows.hid_descriptor import (
    HidCollectionInfo,
    HidInputButtonCapability,
    summarize_hid_collections,
)


class HidDescriptorTests(unittest.TestCase):
    def test_summary_contains_collection_and_report_lengths_without_full_path(self) -> None:
        infos = (
            HidCollectionInfo(
                collection="COL02",
                device_path=r"\\?\hid#vid_620a&pid_0407&col02#secret",
                usage_page=0x0C,
                usage=0xE9,
                input_report_length=3,
                output_report_length=0,
                feature_report_length=0,
            ),
        )

        summary = summarize_hid_collections(infos)

        self.assertEqual(summary[0]["collection"], "COL02")
        self.assertEqual(summary[0]["usage_page"], "0x0C")
        self.assertNotIn("secret", str(summary))

    def test_summary_contains_input_button_capabilities(self) -> None:
        infos = (
            HidCollectionInfo(
                collection="COL02",
                device_path="fixture",
                usage_page=0x0C,
                usage=0x01,
                input_report_length=3,
                output_report_length=0,
                feature_report_length=0,
                input_button_capabilities=(
                    HidInputButtonCapability(
                        report_id=2,
                        usage_page=0x0C,
                        usage_min=0xE9,
                        usage_max=0xEA,
                        is_range=True,
                        report_count=1,
                        link_collection=0,
                        is_absolute=True,
                    ),
                ),
            ),
        )

        summary = summarize_hid_collections(infos)

        self.assertEqual(summary[0]["input_button_capabilities"][0]["report_id"], 2)
        self.assertEqual(summary[0]["input_button_capabilities"][0]["usage_min"], "0xE9")
        self.assertEqual(summary[0]["input_button_capabilities"][0]["usage_max"], "0xEA")


if __name__ == "__main__":
    unittest.main()
