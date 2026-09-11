"""程序说明：验证 HID Collection 能力摘要和脱敏输出逻辑。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import ctypes

import t1remote.windows.hid_descriptor as hid_descriptor
from t1remote.windows.hid_descriptor import (
    HidCollectionInfo,
    HidInputButtonCapability,
    HidInputData,
    describe_input_data,
    inspect_preparsed_data,
    parse_input_data,
    summarize_hid_collections,
)


class HidDescriptorTests(unittest.TestCase):
    def test_parse_input_data_returns_hid_data_indices(self) -> None:
        """HidP_GetData 返回的 DataIndex 和值应保持原样。"""

        class FakeFunction:
            def __init__(self, callback) -> None:
                self._callback = callback
                self.restype = None
                self.argtypes = None

            def __call__(self, *args):
                return self._callback(*args)

        def max_data_length(_report_type, _preparsed) -> int:
            return 1

        def get_data(_report_type, data_pointer, count_pointer, *_args) -> int:
            item = data_pointer[0]
            item.DataIndex = 545
            item.RawValue = 1
            ctypes.cast(
                count_pointer,
                ctypes.POINTER(ctypes.c_ulong),
            ).contents.value = 1
            return 0x00110000

        fake_hid = type(
            "FakeHid",
            (),
            {
                "HidP_MaxDataListLength": FakeFunction(max_data_length),
                "HidP_GetData": FakeFunction(get_data),
            },
        )()
        with patch.object(hid_descriptor.os, "name", "nt"), patch.object(
            hid_descriptor.ctypes, "WinDLL", return_value=fake_hid
        ):
            values = parse_input_data(b"opaque-preparsed", bytes.fromhex("02 21 02"))

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].data_index, 545)
        self.assertEqual(values[0].raw_value, 1)

    def test_describe_input_data_maps_range_one_to_one(self) -> None:
        """DataIndex 范围应按 Microsoft 定义一一对应到 Usage 范围。"""

        capabilities = (
            HidInputButtonCapability(
                report_id=2,
                usage_page=0x0C,
                usage_min=0xE9,
                usage_max=0xEA,
                is_range=True,
                report_count=2,
                link_collection=0,
                is_absolute=True,
                data_index_min=4,
                data_index_max=5,
            ),
        )

        descriptions = describe_input_data(
            (
                HidInputData(data_index=5, raw_value=1),
                HidInputData(data_index=99, raw_value=1),
            ),
            capabilities,
        )

        self.assertEqual(descriptions[0].data_index, 5)
        self.assertEqual(descriptions[0].raw_value, 1)
        self.assertEqual(len(descriptions[0].button_matches), 1)
        self.assertEqual(descriptions[0].button_matches[0].usage, 0xEA)
        self.assertEqual(descriptions[0].button_matches[0].usage_page, 0x0C)
        self.assertEqual(descriptions[0].button_matches[0].report_id, 2)
        self.assertEqual(descriptions[1].button_matches, ())

    def test_describe_input_data_preserves_non_range_and_ambiguous_matches(self) -> None:
        """非范围字段和重复 DataIndex 候选都必须保留，不能静默猜一个。"""

        capabilities = (
            HidInputButtonCapability(
                report_id=2,
                usage_page=0x0C,
                usage_min=0x0221,
                usage_max=0x0221,
                is_range=False,
                report_count=1,
                link_collection=0,
                is_absolute=True,
                data_index_min=545,
                data_index_max=545,
            ),
            HidInputButtonCapability(
                report_id=3,
                usage_page=0x01,
                usage_min=0x81,
                usage_max=0x81,
                is_range=False,
                report_count=1,
                link_collection=0,
                is_absolute=True,
                data_index_min=545,
                data_index_max=545,
            ),
        )

        descriptions = describe_input_data(
            (HidInputData(data_index=545, raw_value=1),),
            capabilities,
        )

        self.assertEqual(len(descriptions), 1)
        self.assertEqual(
            [match.usage for match in descriptions[0].button_matches],
            [0x0221, 0x81],
        )
        self.assertEqual(
            [match.capability_index for match in descriptions[0].button_matches],
            [0, 1],
        )

    def test_describe_input_data_rejects_mismatched_ranges(self) -> None:
        """DataIndex 与 Usage 范围长度不一致时不能构造伪映射。"""

        descriptions = describe_input_data(
            (HidInputData(data_index=10, raw_value=1),),
            (
                HidInputButtonCapability(
                    report_id=2,
                    usage_page=0x0C,
                    usage_min=0xE9,
                    usage_max=0xEA,
                    is_range=True,
                    report_count=1,
                    link_collection=0,
                    is_absolute=True,
                    data_index_min=10,
                    data_index_max=12,
                ),
            ),
        )

        self.assertEqual(descriptions[0].button_matches, ())

    def test_inspect_preparsed_data_uses_hid_parser(self) -> None:
        """桥接返回的 opaque bytes 应交给 HID parser，而不是自行拆解。"""

        class FakeFunction:
            def __init__(self, callback) -> None:
                self._callback = callback
                self.restype = None
                self.argtypes = None

            def __call__(self, *args):
                return self._callback(*args)

        def get_caps(_preparsed, caps_pointer) -> int:
            caps = ctypes.cast(
                caps_pointer,
                ctypes.POINTER(hid_descriptor.HIDP_CAPS),
            ).contents
            caps.UsagePage = 0x0C
            caps.Usage = 0x01
            caps.InputReportByteLength = 3
            caps.OutputReportByteLength = 0
            caps.FeatureReportByteLength = 0
            caps.NumberInputButtonCaps = 1
            return 0x00110000

        def get_button_caps(_kind, values_pointer, count_pointer, _preparsed) -> int:
            value = values_pointer[0]
            value.UsagePage = 0x0C
            value.ReportID = 2
            value.IsRange = 1
            value.IsAbsolute = 1
            value.ReportCount = 1
            value.Usage.Range.UsageMin = 0xE9
            value.Usage.Range.UsageMax = 0xEA
            value.Usage.Range.DataIndexMin = 4
            value.Usage.Range.DataIndexMax = 5
            ctypes.cast(
                count_pointer,
                ctypes.POINTER(ctypes.c_ushort),
            ).contents.value = 1
            return 0x00110000

        fake_hid = type(
            "FakeHid",
            (),
            {
                "HidP_GetCaps": FakeFunction(get_caps),
                "HidP_GetButtonCaps": FakeFunction(get_button_caps),
            },
        )()
        with patch.object(hid_descriptor.os, "name", "nt"), patch.object(
            hid_descriptor.ctypes, "WinDLL", return_value=fake_hid
        ):
            info = inspect_preparsed_data(b"opaque-preparsed", collection="COL02")

        self.assertEqual(info.collection, "COL02")
        self.assertEqual(info.usage_page, 0x0C)
        self.assertEqual(info.usage, 0x01)
        self.assertEqual(info.input_report_length, 3)
        self.assertEqual(len(info.input_button_capabilities), 1)
        self.assertEqual(info.input_button_capabilities[0].report_id, 2)
        self.assertEqual(info.input_button_capabilities[0].usage_min, 0xE9)
        self.assertEqual(info.input_button_capabilities[0].usage_max, 0xEA)
        self.assertEqual(info.input_button_capabilities[0].data_index_min, 4)
        self.assertEqual(info.input_button_capabilities[0].data_index_max, 5)

    def test_inspect_preparsed_data_rejects_empty_bytes(self) -> None:
        """空 opaque 数据不能进入 HID parser。"""

        with self.assertRaises(ValueError):
            inspect_preparsed_data(b"", collection="COL02")

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
        self.assertEqual(
            summary[0]["report_descriptor_status"],
            "descriptor_unavailable",
        )
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
                report_descriptor=bytes.fromhex(
                    "05 0C 09 01 A1 01 85 02 75 01 95 01 09 E9 81 02 C0"
                ),
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
                        data_index_min=4,
                        data_index_max=5,
                    ),
                ),
            ),
        )

        summary = summarize_hid_collections(infos)

        self.assertEqual(summary[0]["input_button_capabilities"][0]["report_id"], 2)
        self.assertEqual(summary[0]["input_button_capabilities"][0]["usage_min"], "0xE9")
        self.assertEqual(summary[0]["input_button_capabilities"][0]["usage_max"], "0xEA")
        self.assertEqual(summary[0]["input_button_capabilities"][0]["data_index_min"], 4)
        self.assertEqual(summary[0]["input_button_capabilities"][0]["data_index_max"], 5)
        self.assertEqual(
            summary[0]["report_descriptor_hex"],
            "05 0c 09 01 a1 01 85 02 75 01 95 01 09 e9 81 02 c0",
        )
        self.assertEqual(summary[0]["report_fields"][0]["usage_page"], "0x0C")
        self.assertEqual(summary[0]["report_fields"][0]["usages"], ["0xE9"])
        self.assertEqual(
            summary[0]["report_descriptor_status"],
            "descriptor_available",
        )


if __name__ == "__main__":
    unittest.main()
