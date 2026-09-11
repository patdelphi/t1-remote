"""程序说明：验证驱动事件采集的状态解析和脱敏 JSON 结构。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from t1remote.windows.driver_bridge import DriverInputEvent
from t1remote.windows.hid_input import HidInputEvent
from tools.t1_driver_inspector import (
    build_capture_document,
    run,
    serialize_driver_event,
)


class _FakeInspectorGuard:
    """测试 Driver Inspector 的单实例生命周期，不创建实际锁。"""

    def __init__(self, _name: str) -> None:
        self.released = False

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True


class _FakeInspectorBridge:
    """模拟桥接队列，并记录 Inspector 的资源释放顺序。"""

    def __init__(self, events: list[DriverInputEvent] | None = None) -> None:
        self.is_open = False
        self.events = list(events or [])
        self.lifecycle: list[str] = []
        self.read_finished = threading.Event()
        self.read_calls = 0
        self.descriptor_calls: list[str] = []

    def open(self, _policy: object) -> None:
        self.is_open = True
        self.lifecycle.append("bridge_open")

    def start(self) -> None:
        self.lifecycle.append("bridge_start")

    def heartbeat(self) -> None:
        self.lifecycle.append("bridge_heartbeat")

    def flush_events(self) -> None:
        self.lifecycle.append("bridge_flush")

    def get_report_descriptor(self, collection: str) -> bytes:
        self.descriptor_calls.append(collection)
        return b""

    def status(self) -> object:
        class _Status:
            state = "running"
            attached_collections = 0x06
            lease_active = True

        return _Status()

    def read_event(self) -> DriverInputEvent | None:
        self.read_calls += 1
        if self.events:
            self.read_finished.set()
            return self.events.pop(0)
        return None

    def stop(self) -> None:
        self.lifecycle.append("bridge_stop")
        self.is_open = False

    def close(self) -> None:
        self.lifecycle.append("bridge_close")


class _FakeInspectorHidListener:
    """模拟 HID ReadFile 泵，并允许测试启动异常及回调重复风险。"""

    def __init__(
        self,
        on_event,
        on_error,
        target_collections,
        *,
        lifecycle: list[str],
        fail_on_start: bool = False,
        emit_event_on_start: bool = False,
    ) -> None:
        self.on_event = on_event
        self.on_error = on_error
        self.target_collections = tuple(target_collections)
        self.lifecycle = lifecycle
        self.fail_on_start = fail_on_start
        self.emit_event_on_start = emit_event_on_start
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.lifecycle.append("hid_start")
        if self.fail_on_start:
            raise RuntimeError("HID 读请求启动失败")
        self.started = True
        if self.emit_event_on_start:
            self.on_event(
                HidInputEvent(
                    device_path="redacted",
                    collection="COL02",
                    report=bytes.fromhex("02 23 02"),
                )
            )

    def stop(self) -> None:
        self.lifecycle.append("hid_stop")
        self.stopped = True


class DriverInspectorTests(unittest.TestCase):
    """覆盖 COL02/COL03 驱动事件的最小采集夹具。"""

    def _run_with_commands(
        self,
        output_path: Path,
        bridge: _FakeInspectorBridge,
        hid_listener_factory,
    ) -> int:
        """让命令线程先选择 Home，再等驱动队列读取完成后退出。"""

        command_count = 0

        def fake_input(_prompt: str) -> str:
            nonlocal command_count
            command_count += 1
            if command_count == 1:
                return "1"
            bridge.read_finished.wait(timeout=1)
            time.sleep(0.05)
            return "q"

        with patch("builtins.input", side_effect=fake_input):
            with patch(
                "tools.t1_driver_inspector.T1BridgeClient",
                return_value=bridge,
            ), patch(
                "tools.t1_driver_inspector.SingleInstanceGuard",
                _FakeInspectorGuard,
            ):
                return run(output_path, hid_listener_factory=hid_listener_factory)

    def test_starts_hid_read_pump_for_consumer_collections(self) -> None:
        """Inspector 启动桥接后应为 COL02/COL03 建立持续读请求。"""

        bridge = _FakeInspectorBridge()
        listeners: list[_FakeInspectorHidListener] = []

        def listener_factory(**kwargs):
            listener = _FakeInspectorHidListener(
                **kwargs,
                lifecycle=bridge.lifecycle,
            )
            listeners.append(listener)
            return listener

        with tempfile.TemporaryDirectory() as directory:
            result = self._run_with_commands(
                Path(directory) / "capture.json",
                bridge,
                listener_factory,
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(listeners), 1)
        self.assertEqual(listeners[0].target_collections, ("COL02", "COL03"))
        self.assertTrue(listeners[0].started)
        self.assertTrue(listeners[0].stopped)
        self.assertEqual(bridge.descriptor_calls, ["COL02", "COL03"])
        self.assertLess(
            bridge.lifecycle.index("hid_start"),
            bridge.lifecycle.index("bridge_stop"),
        )

    def test_stops_hid_listener_before_closing_bridge(self) -> None:
        """退出时先取消 ReadFile，再停止并关闭桥接会话。"""

        bridge = _FakeInspectorBridge()

        def listener_factory(**kwargs):
            return _FakeInspectorHidListener(
                **kwargs,
                lifecycle=bridge.lifecycle,
            )

        with tempfile.TemporaryDirectory() as directory:
            result = self._run_with_commands(
                Path(directory) / "capture.json",
                bridge,
                listener_factory,
            )

        self.assertEqual(result, 0)
        self.assertLess(
            bridge.lifecycle.index("hid_stop"),
            bridge.lifecycle.index("bridge_stop"),
        )
        self.assertLess(
            bridge.lifecycle.index("bridge_stop"),
            bridge.lifecycle.index("bridge_close"),
        )

    def test_listener_start_failure_still_cleans_up_listener_and_bridge(self) -> None:
        """HID 读请求启动失败时，已创建的监听器和桥接都必须释放。"""

        bridge = _FakeInspectorBridge()
        listeners: list[_FakeInspectorHidListener] = []

        def listener_factory(**kwargs):
            listener = _FakeInspectorHidListener(
                **kwargs,
                lifecycle=bridge.lifecycle,
                fail_on_start=True,
            )
            listeners.append(listener)
            return listener

        with tempfile.TemporaryDirectory() as directory, patch(
            "builtins.input",
            side_effect=AssertionError("启动失败后不应进入命令线程"),
        ):
            with patch(
                "tools.t1_driver_inspector.T1BridgeClient",
                return_value=bridge,
            ), patch(
                "tools.t1_driver_inspector.SingleInstanceGuard",
                _FakeInspectorGuard,
            ):
                result = run(
                    Path(directory) / "capture.json",
                    hid_listener_factory=listener_factory,
                )

        self.assertEqual(result, 1)
        self.assertEqual(len(listeners), 1)
        self.assertTrue(listeners[0].stopped)
        self.assertIn("bridge_stop", bridge.lifecycle)
        self.assertIn("bridge_close", bridge.lifecycle)

    def test_hid_callback_does_not_duplicate_driver_queue_event(self) -> None:
        """HID 直读仅保持请求，采集事件仍只从桥接队列读取一次。"""

        bridge = _FakeInspectorBridge(
            [
                DriverInputEvent(
                    sequence=1,
                    usage_page=0x0C,
                    usage=0x223,
                    collection="COL02",
                    report=bytes.fromhex("02 23 02"),
                )
            ]
        )

        def listener_factory(**kwargs):
            return _FakeInspectorHidListener(
                **kwargs,
                lifecycle=bridge.lifecycle,
                emit_event_on_start=True,
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.json"
            result = self._run_with_commands(output, bridge, listener_factory)
            document = json.loads(output.read_text(encoding="utf-8-sig"))

        self.assertEqual(result, 0)
        self.assertGreaterEqual(bridge.read_calls, 1)
        self.assertEqual(len(document["events"]), 1)
        self.assertEqual(document["events"][0]["sequence"], 1)

    def test_serializes_consumer_press_and_redacts_device_identity(self) -> None:
        event = DriverInputEvent(
            sequence=7,
            usage_page=0x0C,
            usage=0x223,
            collection="COL02",
            report=b"\x02\x23\x02",
            timestamp_100ns=123,
        )

        record = serialize_driver_event(
            event,
            "Home",
            timestamp_utc="2026-09-08T11:00:00+00:00",
        )

        self.assertEqual(record["state"], "down")
        self.assertEqual(record["usage_page_hex"], "0x000C")
        self.assertEqual(record["raw_data_hex"], "02 23 02")
        self.assertEqual(record["report_category"], "consumer")
        self.assertIsNone(record["report_id"])
        self.assertEqual(record["descriptor_status"], "descriptor_unavailable")
        self.assertNotIn("VID_", str(record))
        self.assertNotIn("PID_", str(record))

    def test_system_control_release_is_detected(self) -> None:
        event = DriverInputEvent(
            sequence=8,
            usage_page=0x01,
            usage=0,
            collection="COL03",
            report=b"\x03\x00",
        )

        record = serialize_driver_event(event, "Power")

        self.assertEqual(record["state"], "up")
        self.assertEqual(record["device_family"], "T1-Remote/COL03")

    def test_document_has_driver_source_and_events(self) -> None:
        document = build_capture_document([{"button": "Home"}])

        self.assertEqual(document["source"], "T1Bridge_ReadEvent")
        self.assertEqual(document["events"], [{"button": "Home"}])
        self.assertEqual(
            document["hid_evidence"]["descriptor_status"],
            "descriptor_unavailable",
        )
        self.assertEqual(document["hid_evidence"]["events"], [])

    def test_document_keeps_numeric_consumer_usage_and_pairs_driver_reports(self) -> None:
        down = serialize_driver_event(
            DriverInputEvent(
                sequence=1,
                usage_page=0x0C,
                usage=0x0221,
                collection="COL02",
                report=bytes.fromhex("02 21 02"),
                timestamp_100ns=1_000_000,
            ),
            "Voice",
            timestamp_utc="2026-09-10T01:00:00+00:00",
        )
        up = serialize_driver_event(
            DriverInputEvent(
                sequence=2,
                usage_page=0x0C,
                usage=0,
                collection="COL02",
                report=bytes.fromhex("02 00 00"),
                timestamp_100ns=1_600_000,
            ),
            "Voice",
            timestamp_utc="2026-09-10T01:00:00.060+00:00",
        )

        document = build_capture_document([down, up])
        evidence = document["hid_evidence"]

        self.assertEqual(evidence["events"][0]["usage"], 0x0221)
        self.assertIsNone(evidence["events"][0]["report_id"])
        self.assertEqual(evidence["events"][1]["event_kind"], "up")
        self.assertEqual(evidence["actions"][0]["duration_ms"], 60)
        self.assertNotIn("Voice", str(evidence))

    def test_document_keeps_driver_report_descriptors_and_uses_them_for_evidence(self) -> None:
        """采集夹具保存真实描述符，并用它确认 Report ID。"""

        descriptor = bytes.fromhex(
            "05 0C 09 01 A1 01 85 02 75 01 95 01 09 E9 81 02 C0"
        )
        down = serialize_driver_event(
            DriverInputEvent(
                sequence=1,
                usage_page=0x0C,
                usage=0xE9,
                collection="COL02",
                report=bytes.fromhex("02 E9 00"),
                timestamp_100ns=1_000_000,
            ),
            "Volume Plus",
        )
        up = serialize_driver_event(
            DriverInputEvent(
                sequence=2,
                usage_page=0x0C,
                usage=0,
                collection="COL02",
                report=bytes.fromhex("02 00 00"),
                timestamp_100ns=2_200_000,
            ),
            "Volume Plus",
        )

        document = build_capture_document(
            [down, up],
            report_descriptors={"COL02": descriptor},
        )

        self.assertEqual(
            document["hid_descriptors"]["COL02"]["raw_descriptor_hex"],
            descriptor.hex(" "),
        )
        self.assertEqual(document["hid_evidence"]["descriptor_status"], "descriptor_available")
        self.assertEqual(document["hid_evidence"]["events"][0]["report_id"], 2)


if __name__ == "__main__":
    unittest.main()
