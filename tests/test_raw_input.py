"""程序说明：验证 Raw Input 注册覆盖 T1 的键盘、Consumer 和 System Control 页面。"""

import unittest

from t1remote.windows.raw_input import (
    _is_class_already_exists_error,
    WM_INPUT_DEVICE_CHANGE,
    WM_POWERBROADCAST,
    RIDEV_DEVNOTIFY,
    RIDEV_INPUTSINK,
    RIDEV_PAGEONLY,
    RawInputDeviceInfo,
    build_raw_input_registrations,
    summarize_raw_input_devices,
)


class RawInputRegistrationTests(unittest.TestCase):
    """确保 HID 页面注册不会漏掉 T1 的遥控区域 Collection。"""

    def test_registers_keyboard_consumer_page_and_system_control(self) -> None:
        registrations = build_raw_input_registrations(123)
        registered = {
            (item.usUsagePage, item.usUsage, item.dwFlags)
            for item in registrations
        }
        base_flags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY

        self.assertIn((0x01, 0x06, base_flags), registered)
        self.assertIn((0x0C, 0x00, base_flags | RIDEV_PAGEONLY), registered)
        self.assertIn((0x01, 0x80, base_flags), registered)

    def test_device_and_power_notification_constants_are_declared(self) -> None:
        self.assertEqual(WM_INPUT_DEVICE_CHANGE, 0x00FE)
        self.assertEqual(WM_POWERBROADCAST, 0x0218)

    def test_device_summary_redacts_path_and_handle(self) -> None:
        devices = summarize_raw_input_devices(
            (
                RawInputDeviceInfo(
                    device_handle=123,
                    raw_input_type=2,
                    device_path=r"\\?\hid#vid_620a&pid_0407&col02#secret-address",
                ),
            )
        )

        self.assertEqual(devices[0]["collection"], "COL02")
        self.assertEqual(devices[0]["device_family"], "T1-Remote/COL02")
        self.assertEqual(devices[0]["transport_hint"], "hid-unknown")
        self.assertNotIn("secret-address", str(devices))
        self.assertNotIn("123", str(devices))

    def test_register_class_restart_error_is_recognized(self) -> None:
        class FakeWinError(Exception):
            winerror = 1410

        self.assertTrue(_is_class_already_exists_error(FakeWinError()))
        self.assertFalse(_is_class_already_exists_error(OSError(5, "拒绝访问")))


if __name__ == "__main__":
    unittest.main()
