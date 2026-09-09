"""程序说明：验证 T1 Mapping 会话的启动、停止和 Raw Input 路由。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from t1remote.core.key_mapping import KeyAction, MappingConfig, save_mapping_config
from t1remote.windows.driver_bridge import BridgeStatus, DriverInputEvent
from t1remote.windows.mapping_session import T1MappingSession
from t1remote.windows.raw_input import RawInputEvent


class _FakeBridge:
    def __init__(self) -> None:
        self.is_open = False
        self.policy = None
        self.reads = 0
        self.closed = False

    def open(self, policy) -> None:
        self.policy = policy
        self.is_open = True

    def start(self) -> None:
        pass

    def heartbeat(self) -> None:
        pass

    def status(self) -> BridgeStatus:
        return BridgeStatus(
            state="running",
            last_error=0,
            dropped_reports=0,
            abi_version=2,
            attached_collections=0x06,
            lease_active=bool(self.policy and self.policy.lease_required),
        )

    def read_event(self):
        self.reads += 1
        return None

    def stop(self) -> None:
        self.is_open = False

    def close(self) -> None:
        self.closed = True


class _ReconnectBridge(_FakeBridge):
    """模拟蓝牙重连后设备重新附着但过滤租约已停止。"""

    def __init__(self) -> None:
        super().__init__()
        self.driver_state = "stopped"
        self.attached_collections = 0x06
        self.lease_active = False
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self.driver_state = "running"
        self.lease_active = bool(self.policy and self.policy.lease_required)

    def status(self) -> BridgeStatus:
        return BridgeStatus(
            state=self.driver_state,
            last_error=0,
            dropped_reports=0,
            abi_version=2,
            attached_collections=self.attached_collections,
            lease_active=self.lease_active,
        )


class _EventBridge(_FakeBridge):
    """只返回一条驱动事件，用于触发会话运行时错误路径。"""

    def __init__(self, event: DriverInputEvent) -> None:
        super().__init__()
        self._event = event

    def read_event(self):
        event, self._event = self._event, None
        return event


class _FailingEmitter:
    """模拟 SendInput 失败，验证会话不会继续显示 running。"""

    def emit(self, _outputs) -> None:
        raise RuntimeError("SendInput 测试失败")


class _FakeRawListener:
    last_instance: "_FakeRawListener | None" = None

    def __init__(self, on_event, on_error, on_device_change=None, on_power_event=None) -> None:
        self.on_event = on_event
        self.on_error = on_error
        self.on_device_change = on_device_change
        self.on_power_event = on_power_event
        self.started = False
        self.stopped = False
        _FakeRawListener.last_instance = self

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class MappingSessionTests(unittest.TestCase):
    def test_heartbeat_restarts_filter_after_device_reconnect(self) -> None:
        bridge = _ReconnectBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=False,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            try:
                # 模拟设备重连：集合已附着，但原租约对应的过滤状态已经停止。
                bridge.driver_state = "stopped"
                bridge.lease_active = False
                deadline = time.monotonic() + 2
                while bridge.start_calls < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertGreaterEqual(bridge.start_calls, 2)
                self.assertEqual(session.status().driver_state, "running")
                self.assertTrue(session.status().lease_active)
            finally:
                session.stop()

    def test_output_failure_marks_session_as_error_and_stops_processing(self) -> None:
        bridge = _EventBridge(
            DriverInputEvent(
                sequence=1,
                usage_page=0x0C,
                usage=0x0E9,
                collection="COL02",
                report=bytes.fromhex("02 e9 00"),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            with patch(
                "t1remote.windows.mapping_session.WindowsInputEmitter",
                _FailingEmitter,
            ):
                session = T1MappingSession(
                    path,
                    dry_run=False,
                    bridge_factory=lambda: bridge,
                    raw_listener_factory=_FakeRawListener,
                    instance_name=f"T1RemoteTestSession-{id(bridge)}",
                )
                session.start()
                deadline = time.monotonic() + 2
                while session.status().state == "running" and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(session.status().state, "error")
                session.stop()

    def test_start_stop_and_status(self) -> None:
        bridge = _FakeBridge()
        logs: list[str] = []
        errors: list[Exception] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=True,
                on_log=logs.append,
                on_error=errors.append,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )

            session.start()
            running = session.status()
            self.assertEqual(running.state, "running")
            self.assertEqual(running.driver_state, "running")
            self.assertFalse(running.lease_active)
            assert bridge.policy is not None
            self.assertFalse(bridge.policy.enabled)
            self.assertFalse(bridge.policy.lease_required)
            session.stop()

        self.assertEqual(session.status().state, "stopped")
        self.assertTrue(bridge.closed)
        self.assertTrue(logs)
        self.assertEqual(errors, [])

    def test_raw_input_from_t1_keyboard_is_processed(self) -> None:
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=True,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            assert _FakeRawListener.last_instance is not None
            _FakeRawListener.last_instance.on_event(
                RawInputEvent(
                    r"\\?\hid#vid_620a&pid_0407&col01#x",
                    1,
                    bytes.fromhex("48 00 02 00 00 00 26 00 00 01 00 00"),
                )
            )
            self.assertEqual(session.status().diagnostics.mapping_events, 1)
            session.stop()

        self.assertEqual(session.status().diagnostics.mapping_events, 2)

    def test_device_removal_releases_active_mapping(self) -> None:
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=True,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            assert _FakeRawListener.last_instance is not None
            raw = _FakeRawListener.last_instance
            raw.on_event(
                RawInputEvent(
                    r"\\?\hid#vid_620a&pid_0407&col01#x",
                    1,
                    bytes.fromhex("48 00 02 00 00 00 26 00 00 01 00 00"),
                )
            )
            assert raw.on_device_change is not None
            raw.on_device_change(2)
            self.assertEqual(session.status().diagnostics.mapping_events, 2)

    def test_manual_reload_applies_latest_mapping_config(self) -> None:
        bridge = _FakeBridge()
        logs: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=True,
                on_log=logs.append,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()

            replacement = MappingConfig(
                mappings={
                    **MappingConfig.default().mappings,
                    "OK": KeyAction("key", "SPACE"),
                }
            )
            save_mapping_config(path, replacement)
            session.reload_config()

            assert session._runtime is not None
            self.assertEqual(session._runtime.config.mappings["OK"].key, "SPACE")
            self.assertTrue(any("映射配置已重新加载" in message for message in logs))
            session.stop()


if __name__ == "__main__":
    unittest.main()
