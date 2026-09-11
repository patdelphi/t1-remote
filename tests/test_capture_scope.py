"""程序说明：验证遥控区域范围和 T1 设备路径过滤逻辑。"""

import unittest

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    CaptureEvent,
    build_capture_coverage,
    build_logical_actions,
    build_physical_mapping_table,
    capture_metadata,
    collection_from_device_path,
    classify_hid_transport,
    is_t1_device_path,
    redacted_device_family,
    selected_capture_button,
    validate_button_number,
)


class CaptureScopeTests(unittest.TestCase):
    """覆盖本轮只采集遥控区域的基础规则。"""

    def test_remote_area_has_fourteen_physical_buttons(self) -> None:
        self.assertEqual(len(REMOTE_BUTTONS), 14)
        self.assertIn("Air Mouse", REMOTE_BUTTONS)
        self.assertNotIn("Empty Mouse Key", REMOTE_BUTTONS)
        self.assertIn("Volume Plus", REMOTE_BUTTONS)
        self.assertIn("Volume Minus", REMOTE_BUTTONS)

    def test_only_air_mouse_is_disabled_for_capture(self) -> None:
        self.assertEqual(DISABLED_CAPTURE_BUTTONS, ("Air Mouse",))
        self.assertNotIn("Power", DISABLED_CAPTURE_BUTTONS)

    def test_unselected_button_does_not_capture(self) -> None:
        self.assertIsNone(selected_capture_button(None))
        self.assertEqual(selected_capture_button("Home"), "Home")
        self.assertIsNone(selected_capture_button("Air Mouse"))
        self.assertIsNone(selected_capture_button("Unknown"))

    def test_t1_path_filter_requires_vid_and_pid(self) -> None:
        t1_path = (
            r"\\?\HID#VID_620A&PID_0407&MI_00#"
            r"7&1234&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}\COL01"
        )
        other_path = r"\\?\HID#VID_046D&PID_C52B#keyboard"

        self.assertTrue(is_t1_device_path(t1_path))
        self.assertTrue(is_t1_device_path(r"HID\{service}_DEV_VID&01620A_PID&0407&COL01"))
        self.assertFalse(is_t1_device_path(other_path))
        self.assertFalse(is_t1_device_path(""))

    def test_path_metadata_is_redacted(self) -> None:
        device_path = (
            r"\\?\HID#VID_620A&PID_0407&MI_00#"
            r"7&F7426D57FBA1&0&0000#{guid}\COL02"
        )

        self.assertEqual(collection_from_device_path(device_path), "COL02")
        self.assertEqual(redacted_device_family(device_path), "T1-Remote/COL02")
        self.assertNotIn("F7426D57FBA1", redacted_device_family(device_path))

    def test_collection_parser_accepts_setupapi_hid_interface_path(self) -> None:
        device_path = (
            r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_"
            r"f7426d57fba1&Col02#b&39f0e088&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"
        )

        self.assertEqual(collection_from_device_path(device_path), "COL02")

    def test_classifies_hid_transport_without_exposing_device_path(self) -> None:
        self.assertEqual(
            classify_hid_transport(r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev"),
            "ble-hid",
        )
        self.assertEqual(
            classify_hid_transport(r"\\?\USB#VID_620A&PID_0407#receiver"),
            "usb-hid",
        )
        self.assertEqual(classify_hid_transport(r"HID#generic"), "hid-unknown")

    def test_button_number_validation(self) -> None:
        self.assertEqual(validate_button_number(1), "Power")
        self.assertEqual(validate_button_number(14), "Volume Minus")
        with self.assertRaises(ValueError):
            validate_button_number(0)
        with self.assertRaises(ValueError):
            validate_button_number(15)

    def test_keyboard_down_up_becomes_one_logical_action(self) -> None:
        events = [
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.000+00:00",
                button="Arrow Left",
                raw_input_type=1,
                collection="COL01",
                device_family="T1-Remote/COL01",
                raw_data_hex="4b 00 02 00 00 00 25 00 00 01 00 00 00 00 00 00",
            ),
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.180+00:00",
                button="Arrow Left",
                raw_input_type=1,
                collection="COL01",
                device_family="T1-Remote/COL01",
                raw_data_hex="4b 00 03 00 00 00 25 00 01 01 00 00 00 00 00 00",
            ),
        ]

        actions = build_logical_actions(events)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].state, "press_release")
        self.assertEqual(actions[0].packet_count, 2)
        self.assertEqual(actions[0].duration_ms, 180)

    def test_consumer_control_down_up_becomes_one_logical_action(self) -> None:
        events = [
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:34.244+00:00",
                button="Mute",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 e2 00",
            ),
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:34.351+00:00",
                button="Mute",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 00 00",
            ),
        ]

        actions = build_logical_actions(events)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].state, "press_release")
        self.assertEqual(actions[0].packet_count, 2)
        self.assertEqual(actions[0].duration_ms, 107)

    def test_capture_metadata_extracts_known_raw_input_fields(self) -> None:
        state, usage_page, usage = capture_metadata(2, "COL02", "02 e9 00")

        self.assertEqual((state, usage_page, usage), ("down", 0x0C, 0xE9))

    def test_capture_coverage_requires_a_paired_action_for_each_enabled_button(self) -> None:
        events = [
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.000+00:00",
                button="Home",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 23 02",
            ),
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.100+00:00",
                button="Home",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 00 00",
            ),
        ]

        coverage = build_capture_coverage(events)

        self.assertEqual(coverage["paired_press_release_buttons"], ["Home"])
        self.assertIn("Power", coverage["missing_buttons"])
        self.assertFalse(coverage["complete"])

    def test_physical_mapping_table_preserves_observed_usage_without_auto_mapping(self) -> None:
        events = [
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.000+00:00",
                button="Home",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 23 02",
                state="down",
                usage_page=0x0C,
                usage=0x223,
            ),
            CaptureEvent(
                timestamp_utc="2026-09-07T05:13:30.100+00:00",
                button="Home",
                raw_input_type=2,
                collection="COL02",
                device_family="T1-Remote/COL02",
                raw_data_hex="02 00 00",
                state="up",
                usage_page=0x0C,
                usage=0,
            ),
        ]

        table = build_physical_mapping_table(events)
        home = next(item for item in table if item["button"] == "Home")
        air_mouse = next(item for item in table if item["button"] == "Air Mouse")

        self.assertEqual(home["status"], "confirmed")
        self.assertEqual(home["usages"], [{"usage_page": "0x0C", "usage": "0x223"}])
        self.assertEqual(home["collections"], ["COL02"])
        self.assertEqual(air_mouse["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
