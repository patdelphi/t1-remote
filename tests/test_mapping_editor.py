"""程序说明：验证映射编辑器使用的纯 Python 表单转换和动作预览逻辑。"""

from __future__ import annotations

import unittest

from t1remote.core.key_mapping import KeyAction, MappingConfigError
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


if __name__ == "__main__":
    unittest.main()
