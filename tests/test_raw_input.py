"""程序说明：验证 Raw Input 注册覆盖 T1 的键盘、Consumer 和 System Control 页面。"""

import unittest

from t1remote.windows.raw_input import (
    RIDEV_DEVNOTIFY,
    RIDEV_INPUTSINK,
    RIDEV_PAGEONLY,
    build_raw_input_registrations,
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


if __name__ == "__main__":
    unittest.main()
