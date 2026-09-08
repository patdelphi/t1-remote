"""程序说明：验证 T1 已确认 HID/Keyboard 报文到语义按键的解码。"""

import unittest

from t1remote.core.input_mapping import T1InputDecoder


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
        self.assertEqual((released.button, released.state), ("Power", "up"))

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


if __name__ == "__main__":
    unittest.main()
