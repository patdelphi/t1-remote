"""程序说明：验证 Key Mapping 运行时诊断计数和最近事件记录。"""

from __future__ import annotations

import unittest

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import KeyAction, MappingEvent
from t1remote.core.mapping_diagnostics import MappingDiagnostics


class MappingDiagnosticsTests(unittest.TestCase):
    def test_snapshot_counts_input_mapping_output_and_errors(self) -> None:
        diagnostics = MappingDiagnostics(recent_limit=3)
        diagnostics.record_input(
            ButtonEvent(
                button="OK",
                state="down",
                collection="COL01",
                source="keyboard",
                report=b"",
            )
        )
        diagnostics.record_input(
            ButtonEvent(
                button=None,
                state="unknown",
                collection="COL05",
                source="unknown",
                report=b"",
            )
        )
        mapping_event = MappingEvent("OK", "down", KeyAction("key", "ENTER"))
        diagnostics.record_mapping(mapping_event)
        diagnostics.record_output(2)
        diagnostics.record_command()
        diagnostics.record_error(RuntimeError("test"))

        snapshot = diagnostics.snapshot()

        self.assertEqual(snapshot.input_events, 2)
        self.assertEqual(snapshot.ignored_inputs, 1)
        self.assertEqual(snapshot.mapping_events, 1)
        self.assertEqual(snapshot.output_events, 2)
        self.assertEqual(snapshot.command_events, 1)
        self.assertEqual(snapshot.errors, 1)
        self.assertEqual(len(snapshot.recent_events), 3)
        self.assertEqual(snapshot.recent_events[-1].result, "error")

    def test_recent_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            MappingDiagnostics(recent_limit=0)


if __name__ == "__main__":
    unittest.main()
