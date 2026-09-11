"""程序说明：验证 Key Mapping 运行时诊断计数和最近事件记录。"""

from __future__ import annotations

import csv
import io
import unittest

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import KeyAction, MappingEvent, TriggerConfig
from t1remote.core.mapping_diagnostics import (
    DiagnosticRecord,
    MappingDiagnostics,
    diagnostic_records_to_csv,
)


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

    def test_long_press_mapping_is_visible_in_diagnostics(self) -> None:
        diagnostics = MappingDiagnostics()
        diagnostics.record_mapping(
            MappingEvent(
                "Voice",
                "down",
                KeyAction("key", "D", trigger=TriggerConfig("long_press")),
            )
        )

        record = diagnostics.snapshot().recent_events[-1]
        self.assertEqual(record.state, "long_press")
        self.assertIn("长按", record.detail)

    def test_diagnostic_records_to_csv_keeps_latest_30_newest_first(self) -> None:
        records = tuple(
            DiagnosticRecord(
                timestamp_local=f"2026-09-10T13:22:{index:02d}+08:00",
                button="Voice" if index % 2 else None,
                state="up" if index % 2 else "output",
                action_kind="combo" if index % 2 else None,
                result="mapping" if index % 2 else "output",
                detail=f"说明,{index}\n第二行",
            )
            for index in range(35)
        )

        text = diagnostic_records_to_csv(records)
        rows = list(csv.reader(io.StringIO(text, newline="")))

        self.assertEqual(
            rows[0], ["时间", "结果", "按键", "状态", "动作", "说明"]
        )
        self.assertEqual(len(rows), 31)
        self.assertEqual(rows[1][0], "2026-09-10T13:22:34+08:00")
        self.assertEqual(rows[-1][0], "2026-09-10T13:22:05+08:00")
        self.assertEqual(rows[1][-1], "说明,34\n第二行")
        self.assertIn("\r\n", text)

    def test_diagnostic_records_to_csv_accepts_fewer_records_and_validates_limit(self) -> None:
        record = DiagnosticRecord("now", None, "output", None, "output", "ok")

        text = diagnostic_records_to_csv((record,))

        self.assertEqual(text.count("\r\n"), 2)
        self.assertIn("now,output,-,output,-,ok", text)
        with self.assertRaises(ValueError):
            diagnostic_records_to_csv((record,), limit=0)


if __name__ == "__main__":
    unittest.main()
