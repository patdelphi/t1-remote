"""程序说明：验证映射编辑器使用的纯 Python 表单转换和动作预览逻辑。"""

from __future__ import annotations

import unittest

from t1remote.core.key_mapping import KeyAction, MacroStep, MappingConfigError
from t1remote.core.mapping_editor import (
    action_to_form,
    build_action_from_form,
    build_command_argv,
    format_action_summary,
)


class MappingEditorTests(unittest.TestCase):
    def test_form_roundtrip_supports_single_key_and_combo(self) -> None:
        single = build_action_from_form(
            "key",
            key="ENTER",
            modifiers=(),
            program="",
            argument_lines=(),
        )
        combo = build_action_from_form(
            "combo",
            key="TAB",
            modifiers=("ALT",),
            program="",
            argument_lines=(),
        )

        self.assertEqual(single, KeyAction("key", "ENTER"))
        self.assertEqual(combo, KeyAction("combo", "TAB", ("ALT",)))
        self.assertEqual(action_to_form(combo)["modifiers"], ("ALT",))

    def test_special_and_none_forms_are_supported(self) -> None:
        special = build_action_from_form(
            "special",
            key="MEDIA_PLAY_PAUSE",
            modifiers=(),
            program="",
            argument_lines=(),
        )
        none = build_action_from_form(
            "none",
            key="",
            modifiers=(),
            program="",
            argument_lines=(),
        )

        self.assertEqual(special.kind, "special")
        self.assertEqual(special.key, "MEDIA_PLAY_PAUSE")
        self.assertEqual(none, KeyAction("none"))

    def test_trigger_fields_are_preserved_by_form_conversion(self) -> None:
        action = build_action_from_form(
            "key",
            key="ENTER",
            modifiers=(),
            program="",
            argument_lines=(),
            trigger_kind="long_press",
            threshold_ms="750",
        )

        fields = action_to_form(action)

        self.assertEqual(action.trigger.kind, "long_press")
        self.assertEqual(fields["threshold_ms"], 750)

    def test_command_form_uses_one_argument_per_line(self) -> None:
        action = build_action_from_form(
            "command",
            key="",
            modifiers=(),
            program="notepad.exe",
            argument_lines=("C:\\temp\\note.txt", "--new-window"),
        )

        self.assertEqual(
            action.argv,
            ("notepad.exe", "C:\\temp\\note.txt", "--new-window"),
        )
        self.assertEqual(
            build_command_argv("notepad.exe", ("", "  --new-window  ")),
            ("notepad.exe", "--new-window"),
        )

    def test_text_form_preserves_text_and_optional_enter(self) -> None:
        action = build_action_from_form(
            "text",
            key="",
            modifiers=(),
            program="",
            argument_lines=(),
            text="打开设置",
            append_enter=True,
        )

        self.assertEqual(action.text, "打开设置")
        self.assertTrue(action.append_enter)
        self.assertEqual(action_to_form(action)["text"], "打开设置")

    def test_macro_form_supports_key_chords_and_delays(self) -> None:
        steps = (
            MacroStep("key", "C", ("CTRL",), 100),
            MacroStep("key", "V", ("CTRL",), 0),
        )
        action = build_action_from_form(
            "macro",
            key="",
            modifiers=(),
            program="",
            argument_lines=(),
            macro_steps=steps,
        )

        self.assertEqual(action.macro, steps)
        self.assertEqual(action_to_form(action)["macro_steps"], steps)

    def test_invalid_form_is_rejected_before_save(self) -> None:
        with self.assertRaises(MappingConfigError):
            build_action_from_form(
                "combo",
                key="TAB",
                modifiers=(),
                program="",
                argument_lines=(),
            )
        with self.assertRaises(MappingConfigError):
            build_action_from_form(
                "command",
                key="",
                modifiers=(),
                program="",
                argument_lines=(),
            )
        with self.assertRaises(MappingConfigError):
            build_action_from_form(
                "text",
                key="",
                modifiers=(),
                program="",
                argument_lines=(),
                text="x" * 101,
            )

    def test_summary_is_human_readable(self) -> None:
        self.assertEqual(format_action_summary(KeyAction("none")), "未映射")
        self.assertEqual(
            format_action_summary(KeyAction("combo", "TAB", ("ALT",))),
            "ALT+TAB",
        )
        self.assertEqual(
            format_action_summary(KeyAction("command", argv=("app.exe", "--x"))),
            "命令：app.exe --x",
        )
        self.assertEqual(
            format_action_summary(
                KeyAction("macro", macro=(MacroStep("key", "A"),))
            ),
            "宏：1 步",
        )


if __name__ == "__main__":
    unittest.main()
