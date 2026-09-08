"""程序说明：验证 T1 Key Mapping 运行时的端到端事件串联。"""

import unittest

from t1remote.core.key_mapping import KeyAction, MacroStep, MappingConfig, MappingEngine
from t1remote.windows.mapping_runtime import T1MappingRuntime


class _FakeEmitter:
    def __init__(self) -> None:
        self.outputs = []

    def emit(self, outputs) -> None:
        self.outputs.extend(outputs)


class _FakeCommandExecutor:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv) -> None:
        self.calls.append(argv)


class _FakeMacroExecutor:
    def __init__(self) -> None:
        self.calls = []
        self.stop_calls = 0

    def run(self, steps) -> None:
        self.calls.append(steps)

    def stop(self) -> None:
        self.stop_calls += 1


class MappingRuntimeTests(unittest.TestCase):
    def test_report_is_decoded_mapped_and_emitted(self) -> None:
        emitter = _FakeEmitter()
        runtime = T1MappingRuntime(emitter=emitter)

        pressed = runtime.process_report(
            "COL02", 2, bytes.fromhex("02 e9 00")
        )
        released = runtime.process_report(
            "COL02", 2, bytes.fromhex("02 00 00")
        )

        self.assertEqual(pressed[0].button, "Volume Plus")
        self.assertEqual(released[0].state, "up")
        self.assertEqual(
            [(item.virtual_key, item.flags) for item in emitter.outputs],
            [(0xAF, 0), (0xAF, 0x02)],
        )

    def test_unbound_power_does_not_emit_output(self) -> None:
        emitter = _FakeEmitter()
        runtime = T1MappingRuntime(emitter=emitter)

        runtime.process_report("COL03", 2, bytes.fromhex("03 01"))
        runtime.process_report("COL03", 2, bytes.fromhex("03 00"))

        self.assertEqual(emitter.outputs, [])

    def test_runtime_uses_custom_mapping_engine(self) -> None:
        emitter = _FakeEmitter()
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Home": KeyAction("key", "W"),
            }
        )
        runtime = T1MappingRuntime(
            emitter=emitter,
            engine=MappingEngine(config),
        )

        runtime.process_report("COL02", 2, bytes.fromhex("02 23 02"))

        self.assertEqual(emitter.outputs[0].virtual_key, 0x57)

    def test_reload_emits_release_for_old_active_mapping(self) -> None:
        emitter = _FakeEmitter()
        runtime = T1MappingRuntime(emitter=emitter)
        runtime.process_report("COL01", 1, bytes.fromhex("48 00 02 00 00 00 26 00 00 01 00 00 00 00 00 00"))
        replacement = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Arrow Up": KeyAction("key", "W"),
            }
        )

        runtime.reload(replacement)

        self.assertEqual(
            [(item.virtual_key, item.flags) for item in emitter.outputs],
            [(0x26, 0x01), (0x26, 0x03)],
        )

    def test_command_mapping_runs_once_on_press_without_keyboard_output(self) -> None:
        emitter = _FakeEmitter()
        command_executor = _FakeCommandExecutor()
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction("command", argv=("notepad.exe",)),
            }
        )
        runtime = T1MappingRuntime(
            emitter=emitter,
            engine=MappingEngine(config),
            command_executor=command_executor,
        )

        runtime.process_report("COL02", 2, bytes.fromhex("02 21 02"))
        runtime.process_report("COL02", 2, bytes.fromhex("02 00 00"))

        self.assertEqual(command_executor.calls, [("notepad.exe",)])
        self.assertEqual(emitter.outputs, [])

    def test_text_mapping_types_text_and_optional_enter_on_press(self) -> None:
        emitter = _FakeEmitter()
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction("text", text="Hi", append_enter=True),
            }
        )
        runtime = T1MappingRuntime(
            emitter=emitter,
            engine=MappingEngine(config),
        )

        runtime.process_report("COL02", 2, bytes.fromhex("02 21 02"))
        runtime.process_report("COL02", 2, bytes.fromhex("02 00 00"))

        self.assertEqual(
            [(item.virtual_key, item.scan_code, item.flags) for item in emitter.outputs],
            [
                (0, 0x48, 0x04),
                (0, 0x48, 0x06),
                (0, 0x69, 0x04),
                (0, 0x69, 0x06),
                (0x0D, 0, 0),
                (0x0D, 0, 0x02),
            ],
        )

    def test_macro_mapping_starts_on_press_and_stops_on_reset(self) -> None:
        emitter = _FakeEmitter()
        macro_executor = _FakeMacroExecutor()
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction(
                    "macro",
                    macro=(MacroStep("key", "C", ("CTRL",), 100),),
                ),
            }
        )
        runtime = T1MappingRuntime(
            emitter=emitter,
            engine=MappingEngine(config),
            macro_executor=macro_executor,
        )

        runtime.process_report("COL02", 2, bytes.fromhex("02 21 02"))
        runtime.process_report("COL02", 2, bytes.fromhex("02 00 00"))
        runtime.reset()

        self.assertEqual(len(macro_executor.calls), 1)
        self.assertEqual(macro_executor.calls[0][0].key, "C")
        self.assertEqual(macro_executor.stop_calls, 1)
        self.assertEqual(emitter.outputs, [])

    def test_runtime_exposes_input_mapping_and_output_diagnostics(self) -> None:
        emitter = _FakeEmitter()
        runtime = T1MappingRuntime(emitter=emitter)

        runtime.process_report("COL02", 2, bytes.fromhex("02 e9 00"))
        runtime.process_report("COL02", 2, bytes.fromhex("02 00 00"))

        diagnostics = runtime.diagnostics

        self.assertEqual(diagnostics.input_events, 2)
        self.assertEqual(diagnostics.mapping_events, 2)
        self.assertEqual(diagnostics.output_events, 2)
        self.assertEqual(diagnostics.errors, 0)
        self.assertTrue(diagnostics.recent_events)


if __name__ == "__main__":
    unittest.main()
