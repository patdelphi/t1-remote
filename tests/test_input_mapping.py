"""程序说明：验证 T1 已确认 HID/Keyboard 报文到语义按键的解码。"""

import unittest

from t1remote.core.input_mapping import T1InputDecoder, button_input_kind


class InputMappingTests(unittest.TestCase):
    def test_consumer_control_decodes_press_and_release(self) -> None:
        decoder = T1InputDecoder()

        pressed = decoder.feed("COL02", 2, bytes.fromhex("02 e9 00"))
        released = decoder.feed("COL02", 2, bytes.fromhex("02 00 00"))

        self.assertEqual(pressed.button, "Volume Plus")
        self.assertEqual(pressed.state, "down")
        self.assertEqual(pressed.usage, 0xE9)
        self.assertEqual(released.button, "Volume Plus")
        self.assertEqual(released.state, "up")

    def test_driver_release_metadata_does_not_retrigger_button(self) -> None:
        decoder = T1InputDecoder()

        decoder.feed("COL02", 2, bytes.fromhex("02 e9 00"), usage_page=0x0C, usage=0xE9)
        released = decoder.feed(
            "COL02",
            2,
            bytes.fromhex("02 00 00"),
            usage_page=0x0C,
            usage=0xE9,
        )

        self.assertEqual(released.button, "Volume Plus")
        self.assertEqual(released.state, "up")

    def test_consumer_control_supports_two_byte_usage(self) -> None:
        decoder = T1InputDecoder()

        event = decoder.feed("COL02", 2, bytes.fromhex("02 23 02"))

        self.assertEqual(event.button, "Home")
        self.assertEqual(event.usage, 0x223)
        self.assertEqual(event.usage_page, 0x0C)

    def test_system_control_decodes_power(self) -> None:
        decoder = T1InputDecoder()

        pressed = decoder.feed("COL03", 2, bytes.fromhex("03 01"))
        released = decoder.feed("COL03", 2, bytes.fromhex("03 00"))

        self.assertEqual((pressed.button, pressed.state), ("Power", "down"))
        self.assertEqual(pressed.usage, 0x81)
        self.assertEqual((released.button, released.state), ("Power", "up"))

    def test_power_release_metadata_and_unknown_layout(self) -> None:
        """释放包不能被旧 Usage 重新触发；未知布局不能猜成 Power。"""
        decoder = T1InputDecoder()
        pressed = decoder.feed("COL03", 2, b"\x03\x01", usage_page=1, usage=0x81)
        self.assertEqual(pressed.usage, 0x81)
        released = decoder.feed("COL03", 2, b"\x03\x00", usage_page=1, usage=0x81)
        self.assertEqual((released.button, released.state), ("Power", "up"))
        for report in (b"\x03\x03", b"\x03\x80", b"\x04\x01", b"\x03\x01\x00", b"\x03"):
            self.assertEqual(decoder.feed("COL03", 2, report).state, "unknown")

    def test_system_parser_usage_is_not_replaced_by_payload(self) -> None:
        """有效 Usage 优先，未支持的 Sleep 不得误报为 Power。"""
        event = T1InputDecoder().feed("COL03", 2, b"\x03\x01", usage_page=1, usage=0x82)
        self.assertEqual(event.usage, 0x82)
        self.assertIsNone(event.button)

    def test_keyboard_payload_decodes_direction_and_release(self) -> None:
        decoder = T1InputDecoder()

        pressed = decoder.feed(
            "COL01",
            1,
            bytes.fromhex("48 00 02 00 00 00 26 00 00 01 00 00 00 00 00 00"),
        )
        released = decoder.feed(
            "COL01",
            1,
            bytes.fromhex("48 00 03 00 00 00 26 00 01 01 00 00 00 00 00 00"),
        )

        self.assertEqual((pressed.button, pressed.state), ("Arrow Up", "down"))
        self.assertEqual((released.button, released.state), ("Arrow Up", "up"))

    def test_unknown_usage_is_observable_but_not_mapped(self) -> None:
        decoder = T1InputDecoder()

        event = decoder.feed("COL02", 2, bytes.fromhex("02 99 00"))

        self.assertIsNone(event.button)
        self.assertEqual(event.state, "unknown")
        self.assertEqual(event.usage, 0x99)

    def test_button_input_kind_distinguishes_keyboard_hid_and_unsupported_mouse(self) -> None:
        self.assertEqual(button_input_kind("Arrow Up"), "keyboard")
        self.assertEqual(button_input_kind("Volume Plus"), "hid")
        self.assertEqual(button_input_kind("Air Mouse"), "mouse")
        self.assertEqual(button_input_kind("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
