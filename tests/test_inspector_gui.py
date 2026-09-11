"""程序说明：验证捕获页的驱动拦截会话生命周期。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tools.t1_inspector_gui import (
    CaptureBridgeSession,
    CaptureRawInputSession,
    should_ignore_passive_t1_raw_event,
)
from t1remote.windows.driver_bridge import InterceptionPolicy


class _FakeBridge:
    """只记录捕获页需要的最小桥接生命周期。"""

    def __init__(self) -> None:
        self.policy: InterceptionPolicy | None = None
        self.calls: list[str] = []
        self.is_open = False
        self.status_value = SimpleNamespace(
            state="running",
            lease_active=True,
            attached_collections=(1 << 2) | (1 << 3),
        )
        self.preparsed_collections: list[str] = []

    def open(self, policy: InterceptionPolicy) -> None:
        self.calls.append("open")
        self.policy = policy
        self.is_open = True

    def start(self) -> None:
        self.calls.append("start")

    def heartbeat(self) -> None:
        self.calls.append("heartbeat")

    def get_preparsed_data(self, collection: str) -> bytes:
        self.calls.append(f"preparsed:{collection}")
        self.preparsed_collections.append(collection)
        return b"preparsed-fixture"

    def status(self):
        self.calls.append("status")
        return self.status_value

    def read_event(self):
        return None

    def stop(self) -> None:
        self.calls.append("stop")

    def close(self) -> None:
        self.calls.append("close")
        self.is_open = False


class CaptureBridgeSessionTests(unittest.TestCase):
    """覆盖捕获页默认拦截和安全释放。"""

    def test_capture_session_starts_enabled_policy_by_default(self) -> None:
        bridge = _FakeBridge()
        session = CaptureBridgeSession(lambda: bridge)

        session.start(lambda _event: None)
        try:
            self.assertIsNotNone(bridge.policy)
            assert bridge.policy is not None
            self.assertTrue(bridge.policy.enabled)
            self.assertTrue(bridge.policy.lease_required)
            self.assertEqual(bridge.preparsed_collections, ["COL02", "COL03"])
            self.assertEqual(
                bridge.calls[:6],
                [
                    "open",
                    "preparsed:COL02",
                    "preparsed:COL03",
                    "start",
                    "heartbeat",
                    "status",
                ],
            )
        finally:
            session.stop()

        self.assertEqual(bridge.calls[-2:], ["stop", "close"])

    def test_capture_session_rejects_inactive_lease(self) -> None:
        bridge = _FakeBridge()
        bridge.status_value = SimpleNamespace(
            state="stopped",
            lease_active=False,
            attached_collections=0,
        )
        session = CaptureBridgeSession(lambda: bridge)

        with self.assertRaisesRegex(RuntimeError, "未进入有效拦截态"):
            session.start(lambda _event: None)

        self.assertEqual(bridge.calls[-2:], ["stop", "close"])

    def test_capture_session_stop_is_idempotent(self) -> None:
        bridge = _FakeBridge()
        session = CaptureBridgeSession(lambda: bridge)

        session.start(lambda _event: None)
        session.stop()
        session.stop()

        self.assertEqual(bridge.calls.count("stop"), 1)
        self.assertEqual(bridge.calls.count("close"), 1)

    def test_passive_raw_input_is_ignored_when_active_source_exists(self) -> None:
        self.assertTrue(
            should_ignore_passive_t1_raw_event(
                "COL02",
                direct_hid_active=True,
                bridge_active=False,
            )
        )
        self.assertTrue(
            should_ignore_passive_t1_raw_event(
                "COL03",
                direct_hid_active=False,
                bridge_active=True,
            )
        )
        self.assertFalse(
            should_ignore_passive_t1_raw_event(
                "COL02",
                direct_hid_active=False,
                bridge_active=False,
            )
        )
        self.assertFalse(
            should_ignore_passive_t1_raw_event(
                "COL01",
                direct_hid_active=True,
                bridge_active=True,
            )
        )

    def test_raw_input_session_can_re_register_after_mapping(self) -> None:
        class FakeListener:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def start(self) -> None:
                self.calls.append("start")

            def stop(self) -> None:
                self.calls.append("stop")

        listener = FakeListener()
        session = CaptureRawInputSession(listener)  # type: ignore[arg-type]

        session.start()
        session.stop()
        session.start()
        session.start()
        session.stop()
        session.stop()

        self.assertEqual(listener.calls, ["start", "stop", "start", "stop"])
        self.assertFalse(session.is_active)


if __name__ == "__main__":
    unittest.main()
