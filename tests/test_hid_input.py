"""程序说明：验证 T1 HID 直读监听器的设备路径筛选和 Collection 规则。"""

import unittest

from t1remote.windows.hid_input import (
    filter_target_hid_paths,
    normalize_target_collections,
)


class HidInputPathTests(unittest.TestCase):
    """覆盖不依赖 Windows API 的 HID 路径逻辑。"""

    def test_filters_t1_collection_paths_and_deduplicates(self) -> None:
        col02 = (
            r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_REV&0000_"
            r"f7426d57fba1&Col02#b&39f0e088&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"
        )
        col03 = col02.replace("Col02", "Col03").replace("0001}", "0002}")
        col01 = col02.replace("Col02", "Col01")
        other = col02.replace("01620A", "004C00")

        self.assertEqual(
            filter_target_hid_paths(
                [col02, col03, col02, col01, other],
                ("COL02", "COL03"),
            ),
            [("COL02", col02), ("COL03", col03)],
        )

    def test_normalizes_and_rejects_invalid_collection_names(self) -> None:
        self.assertEqual(
            normalize_target_collections(("col03", "COL02", "COL03")),
            ("COL03", "COL02"),
        )
        with self.assertRaises(ValueError):
            normalize_target_collections(("COL2",))
        with self.assertRaises(ValueError):
            normalize_target_collections(("COL00",))


if __name__ == "__main__":
    unittest.main()
