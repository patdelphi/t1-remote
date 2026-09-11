"""程序说明：验证语义按键到 Windows 键盘输出事件的转换。"""

import ctypes
import unittest
from unittest.mock import patch

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import KeyAction, MacroStep, MappingEvent
from t1remote.windows.send_input import (
    INPUT,
    KEY_VIRTUAL_KEY_NAMES,
    MOUSE_ACTION_NAMES,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MouseOutput,
    SPECIAL_HID_KEY_NAMES,
    WindowsInputEmitter,
    binding_from_action,
    build_mapping_output_events,
    build_macro_step_events,
    build_output_events,
    build_text_output_events,
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
    def test_input_structure_matches_windows_abi_size(self) -> None:
        """SendInput 的 cbSize 必须匹配完整 INPUT 联合体布局。"""

        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected_size)

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

    def test_key_catalog_contains_punctuation_modifiers_and_extended_function_keys(self) -> None:
        self.assertIn("SEMICOLON", KEY_VIRTUAL_KEY_NAMES)
        self.assertIn("BACKSLASH", KEY_VIRTUAL_KEY_NAMES)
        self.assertIn("CTRL", KEY_VIRTUAL_KEY_NAMES)
        self.assertIn("WIN", KEY_VIRTUAL_KEY_NAMES)
        self.assertIn("F24", KEY_VIRTUAL_KEY_NAMES)
        self.assertIn("POWER", SPECIAL_HID_KEY_NAMES)
        self.assertIn("MEDIA_NEXT_TRACK", SPECIAL_HID_KEY_NAMES)

        punctuation = binding_from_action(KeyAction("key", "SEMICOLON"))
        modifier = binding_from_action(KeyAction("key", "CTRL"))
        self.assertEqual(punctuation.virtual_key, 0xBA)
        self.assertEqual(modifier.virtual_key, 0x11)

    def test_mouse_action_emits_right_button_down_and_up(self) -> None:
        self.assertIn("RIGHT_CLICK", MOUSE_ACTION_NAMES)
        down = build_mapping_output_events(
            MappingEvent("Menu", "down", KeyAction("mouse", "RIGHT_CLICK"))
        )
        up = build_mapping_output_events(
            MappingEvent("Menu", "up", KeyAction("mouse", "RIGHT_CLICK"))
        )

        self.assertIsInstance(down[0], MouseOutput)
        self.assertEqual(down[0].flags, MOUSEEVENTF_RIGHTDOWN)
        self.assertEqual(up[0].flags, MOUSEEVENTF_RIGHTUP)

    def test_windows_emitter_writes_mouse_input_union(self) -> None:
        class FakeSendInput:
            def __init__(self) -> None:
                self.calls = []

            def __call__(self, count, pointer, _size):
                self.calls.append(
                    [(pointer[index].type, pointer[index].mi.dwFlags) for index in range(count)]
                )
                return count

        class FakeUser32:
            def __init__(self) -> None:
                self.SendInput = FakeSendInput()

        fake_user32 = FakeUser32()
        emitter = WindowsInputEmitter()
        with patch("t1remote.windows.send_input.ctypes.WinDLL", return_value=fake_user32):
            emitter.emit((MouseOutput("RIGHT_CLICK", MOUSEEVENTF_RIGHTDOWN),))

        self.assertEqual(fake_user32.SendInput.calls, [[(0, MOUSEEVENTF_RIGHTDOWN)]])

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

    def test_text_output_uses_unicode_events_and_optional_enter(self) -> None:
        outputs = build_text_output_events("中A", append_enter=True)

        self.assertEqual(
            [(item.virtual_key, item.scan_code, item.flags) for item in outputs],
            [
                (0, 0x4E2D, 0x04),
                (0, 0x4E2D, 0x06),
                (0, 0x41, 0x04),
                (0, 0x41, 0x06),
                (0x0D, 0, 0),
                (0x0D, 0, 0x02),
            ],
        )


if __name__ == "__main__":
    unittest.main()
