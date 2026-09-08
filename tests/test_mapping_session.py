"""程序说明：验证 T1 Mapping 会话的启动、停止和 Raw Input 路由。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from t1remote.core.key_mapping import MappingConfig, save_mapping_config
from t1remote.windows.driver_bridge import BridgeStatus
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
            session.stop()


if __name__ == "__main__":
    unittest.main()
