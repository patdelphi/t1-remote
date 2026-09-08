"""程序说明：验证 Key Mapping 配置、状态机和热加载边界。"""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import (
    KeyAction,
    MappingConfig,
    MappingConfigError,
    MappingEngine,
    load_mapping_config,
    save_mapping_config,
)


def button_event(button: str, state: str) -> ButtonEvent:
    return ButtonEvent(
        button=button,
        state=state,
        collection="COL01",
        source="keyboard",
        report=b"",
    )


class KeyMappingTests(unittest.TestCase):
    def test_default_config_contains_safe_mvp_bindings(self) -> None:
        config = MappingConfig.default()

        self.assertEqual(config.mappings["Arrow Up"].key, "UP")
        self.assertEqual(config.mappings["Volume Plus"].key, "VOLUME_UP")
        self.assertEqual(config.mappings["Power"].kind, "none")
        self.assertEqual(config.mappings["Voice"].kind, "none")

    def test_config_roundtrip_uses_versioned_json(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Menu": KeyAction("shortcut", "TAB", ("ALT",)),
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, config)
            loaded = load_mapping_config(path)

        self.assertEqual(loaded.version, 1)
        self.assertEqual(loaded.mappings["Menu"], config.mappings["Menu"])

    def test_invalid_config_is_rejected(self) -> None:
        with self.assertRaises(MappingConfigError):
            MappingConfig.from_dict(
                {
                    "version": 2,
                    "mappings": {"Not A Button": {"type": "key", "key": "A"}},
                }
            )

    def test_command_and_special_actions_roundtrip(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction(
                    "command",
                    argv=("notepad.exe", "C:\\temp\\note.txt"),
                ),
                "Power": KeyAction("special", "SLEEP"),
            }
        )

        loaded = MappingConfig.from_dict(config.to_dict())

        self.assertEqual(
            loaded.mappings["Voice"].argv,
            ("notepad.exe", "C:\\temp\\note.txt"),
        )
        self.assertEqual(loaded.mappings["Power"].kind, "special")

    def test_command_action_requires_nonempty_argv(self) -> None:
        with self.assertRaises(MappingConfigError):
            KeyAction("command")

    def test_engine_suppresses_duplicate_down_and_orphan_up(self) -> None:
        engine = MappingEngine(MappingConfig.default())

        first_down = engine.handle(button_event("Arrow Up", "down"))
        duplicate_down = engine.handle(button_event("Arrow Up", "down"))
        release = engine.handle(button_event("Arrow Up", "up"))
        orphan_release = engine.handle(button_event("Arrow Up", "up"))

        self.assertEqual(len(first_down), 1)
        self.assertEqual(first_down[0].action.key, "UP")
        self.assertEqual(duplicate_down, ())
        self.assertEqual(release[0].state, "up")
        self.assertEqual(orphan_release, ())

    def test_reload_returns_releases_for_active_old_actions(self) -> None:
        engine = MappingEngine(MappingConfig.default())
        engine.handle(button_event("Arrow Up", "down"))
        replacement = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Arrow Up": KeyAction("key", "W"),
            }
        )

        releases = engine.reload(replacement)

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].state, "up")
        self.assertEqual(releases[0].action.key, "UP")


if __name__ == "__main__":
    unittest.main()
