"""程序说明：验证 Key Mapping 配置、状态机和热加载边界。"""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import (
    KeyAction,
    MacroStep,
    MappingConfig,
    MappingConfigError,
    MappingEngine,
    MappingEvent,
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
        self.assertEqual(config.mappings["Menu"], KeyAction("mouse", "RIGHT_CLICK"))
        self.assertNotIn("Air Mouse", config.mappings)

    def test_modifier_keys_can_be_configured_as_single_keys(self) -> None:
        for key in ("CTRL", "SHIFT", "ALT", "WIN"):
            self.assertEqual(KeyAction("key", key).key, key)

    def test_mouse_action_accepts_only_supported_buttons(self) -> None:
        self.assertEqual(KeyAction("mouse", "RIGHT_CLICK").key, "RIGHT_CLICK")
        with self.assertRaises(MappingConfigError):
            KeyAction("mouse", "WHEEL_UP")

    def test_legacy_air_mouse_mapping_is_dropped_when_loading(self) -> None:
        config = MappingConfig.from_dict(
            {
                "version": 1,
                "mappings": {"Air Mouse": {"type": "key", "key": "A"}},
            }
        )

        self.assertNotIn("Air Mouse", config.mappings)

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

    def test_active_config_uses_keyboard_context_menu_for_codex(self) -> None:
        """当前活动配置用 Shift+F10 发送标准键盘上下文菜单。"""

        config_path = Path(__file__).parents[1] / "config" / "t1-key-mapping.json"
        config = load_mapping_config(config_path)

        self.assertEqual(
            config.mappings["Menu"],
            KeyAction("combo", "F10", ("SHIFT",)),
        )

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

    def test_text_action_roundtrip_and_limits(self) -> None:
        action = KeyAction("text", text="你好，T1", append_enter=True)
        loaded = MappingConfig.from_dict(
            MappingConfig(
                mappings={**MappingConfig.default().mappings, "Voice": action}
            ).to_dict()
        )

        self.assertEqual(loaded.mappings["Voice"], action)
        with self.assertRaises(MappingConfigError):
            KeyAction("text", text="")
        with self.assertRaises(MappingConfigError):
            KeyAction("text", text="x" * 101)
        with self.assertRaises(MappingConfigError):
            KeyAction("text", text="x", append_enter="yes")

    def test_macro_roundtrip_preserves_key_chords_and_delays(self) -> None:
        action = KeyAction(
            "macro",
            macro=(
                MacroStep("key", "C", ("CTRL",), 120),
                MacroStep("key", "V", ("CTRL",), 0),
            ),
        )

        loaded = MappingConfig.from_dict(
            MappingConfig(
                mappings={**MappingConfig.default().mappings, "Voice": action}
            ).to_dict()
        )

        self.assertEqual(loaded.mappings["Voice"], action)

    def test_macro_rejects_hold_repeat_and_empty_steps(self) -> None:
        with self.assertRaises(MappingConfigError):
            KeyAction("macro", macro=())
        with self.assertRaises(MappingConfigError):
            KeyAction(
                "macro",
                macro=(MacroStep("key", "A"),),
                trigger=TriggerConfig("hold_repeat"),
            )

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
        with self.assertRaises(MappingConfigError):
            TriggerConfig("long_press", threshold_ms=50)

    def test_active_voice_mapping_keeps_normal_long_press_threshold(self) -> None:
        """活动配置不能把普通约 100ms 按键误判为长按。"""

        config_path = Path(__file__).parents[1] / "config" / "t1-key-mapping.json"
        config = load_mapping_config(config_path)

        self.assertEqual(
            config.mappings["Voice"].trigger,
            TriggerConfig("long_press", threshold_ms=500),
        )

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

    def test_long_press_keeps_output_until_physical_release(self) -> None:
        """长按触发后保持活动，只有真实 up 才生成释放事件。"""

        action = KeyAction(
            "key",
            "ENTER",
            trigger=TriggerConfig("long_press", threshold_ms=100),
        )
        engine = MappingEngine(
            MappingConfig(mappings={**MappingConfig.default().mappings, "Voice": action})
        )

        self.assertEqual(engine.handle(button_event("Voice", "down"), now=0.0), ())
        self.assertEqual(engine.tick(now=0.1), (MappingEvent("Voice", "down", action),))
        self.assertEqual(engine.tick(now=0.2), ())
        self.assertEqual(
            engine.handle(button_event("Voice", "up"), now=0.3),
            (MappingEvent("Voice", "up", action),),
        )
        self.assertEqual(engine.handle(button_event("Voice", "up"), now=0.4), ())

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
