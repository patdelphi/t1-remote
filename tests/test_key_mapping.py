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
    TriggerConfig,
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

    def test_trigger_config_roundtrip_and_validation(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "OK": KeyAction(
                    "key",
                    "ENTER",
                    trigger=TriggerConfig("long_press", threshold_ms=700),
                ),
            }
        )

        loaded = MappingConfig.from_dict(config.to_dict())

        self.assertEqual(
            loaded.mappings["OK"].trigger,
            TriggerConfig("long_press", threshold_ms=700),
        )
        with self.assertRaises(MappingConfigError):
            TriggerConfig("hold_repeat", interval_ms=10)

    def test_long_press_only_emits_after_threshold(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "OK": KeyAction(
                    "key",
                    "ENTER",
                    trigger=TriggerConfig("long_press", threshold_ms=500),
                ),
            }
        )
        engine = MappingEngine(config)

        self.assertEqual(engine.handle(button_event("OK", "down"), now=0.0), ())
        self.assertEqual(engine.tick(now=0.49), ())
        self.assertEqual(engine.tick(now=0.5)[0].state, "down")
        self.assertEqual(engine.handle(button_event("OK", "up"), now=0.6)[0].state, "up")

    def test_short_release_does_not_emit_long_press(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "OK": KeyAction(
                    "key",
                    "ENTER",
                    trigger=TriggerConfig("long_press", threshold_ms=500),
                ),
            }
        )
        engine = MappingEngine(config)

        self.assertEqual(engine.handle(button_event("OK", "down"), now=0.0), ())
        self.assertEqual(engine.handle(button_event("OK", "up"), now=0.2), ())

    def test_double_click_emits_second_click_only(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "OK": KeyAction(
                    "key",
                    "ENTER",
                    trigger=TriggerConfig("double_click", window_ms=300),
                ),
            }
        )
        engine = MappingEngine(config)

        self.assertEqual(engine.handle(button_event("OK", "down"), now=0.0), ())
        self.assertEqual(engine.handle(button_event("OK", "up"), now=0.1), ())
        self.assertEqual(engine.handle(button_event("OK", "down"), now=0.2)[0].state, "down")
        self.assertEqual(engine.handle(button_event("OK", "up"), now=0.3)[0].state, "up")

    def test_hold_repeat_emits_repeated_down_and_final_up(self) -> None:
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "OK": KeyAction(
                    "key",
                    "ENTER",
                    trigger=TriggerConfig("hold_repeat", interval_ms=100),
                ),
            }
        )
        engine = MappingEngine(config)

        self.assertEqual(engine.handle(button_event("OK", "down"), now=0.0)[0].state, "down")
        self.assertEqual(engine.tick(now=0.09), ())
        self.assertEqual(engine.tick(now=0.1)[0].state, "down")
        self.assertEqual(engine.tick(now=0.21)[0].state, "down")
        self.assertEqual(engine.handle(button_event("OK", "up"), now=0.3)[0].state, "up")

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
