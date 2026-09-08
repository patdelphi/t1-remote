"""程序说明：验证语义按键到 Windows 键盘输出事件的转换。"""

import unittest

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import KeyAction, MacroStep, MappingEvent
from t1remote.windows.send_input import (
    binding_from_action,
    build_mapping_output_events,
    build_macro_step_events,
    build_output_events,
)


def button_event(button: str | None, state: str) -> ButtonEvent:
    return ButtonEvent(
        button=button,
        state=state,
        collection="COL02",
        source="hid",
        report=b"",
    )


class SendInputTests(unittest.TestCase):
    def test_arrow_press_and_release_become_keyboard_events(self) -> None:
        pressed = build_output_events(button_event("Arrow Up", "down"))
        released = build_output_events(button_event("Arrow Up", "up"))

        self.assertEqual(len(pressed), 1)
        self.assertEqual(pressed[0].virtual_key, 0x26)
        self.assertEqual(pressed[0].flags, 0x01)
        self.assertEqual(released[0].flags, 0x03)

    def test_media_buttons_use_media_virtual_keys(self) -> None:
        mute = build_output_events(button_event("Mute", "down"))
        volume_up = build_output_events(button_event("Volume Plus", "down"))

        self.assertEqual(mute[0].virtual_key, 0xAD)
        self.assertEqual(volume_up[0].virtual_key, 0xAF)

    def test_power_voice_and_unknown_are_unbound_by_default(self) -> None:
        self.assertEqual(build_output_events(button_event("Power", "down")), ())
        self.assertEqual(build_output_events(button_event("Voice", "down")), ())
        self.assertEqual(build_output_events(button_event(None, "unknown")), ())

    def test_shortcut_emits_modifiers_and_releases_in_reverse_order(self) -> None:
        event = MappingEvent(
            button="Menu",
            state="down",
            action=KeyAction("shortcut", "TAB", ("ALT",)),
        )
        release = MappingEvent(
            button="Menu",
            state="up",
            action=event.action,
        )

        pressed = build_mapping_output_events(event)
        released = build_mapping_output_events(release)

        self.assertEqual(
            [(item.virtual_key, item.flags) for item in pressed],
            [(0x12, 0), (0x09, 0)],
        )
        self.assertEqual(
            [(item.virtual_key, item.flags) for item in released],
            [(0x09, 0x02), (0x12, 0x02)],
        )

    def test_special_hid_function_is_converted_to_virtual_key(self) -> None:
        event = MappingEvent(
            button="Mute",
            state="down",
            action=KeyAction("special", "MEDIA_PLAY_PAUSE"),
        )

        output = build_mapping_output_events(event)

        self.assertEqual(output[0].virtual_key, 0xB3)
        self.assertEqual(binding_from_action(KeyAction("command", argv=("x",))), None)

    def test_macro_step_emits_a_chord_then_releases_in_reverse_order(self) -> None:
        down, up = build_macro_step_events(MacroStep("key", "1", ("CTRL", "SHIFT")))

        self.assertEqual(
            [(item.virtual_key, item.flags) for item in down],
            [(0x11, 0), (0x10, 0), (0x31, 0)],
        )
        self.assertEqual(
            [(item.virtual_key, item.flags) for item in up],
            [(0x31, 0x02), (0x10, 0x02), (0x11, 0x02)],
        )


if __name__ == "__main__":
    unittest.main()
