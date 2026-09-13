"""程序说明：验证 T1 Mapping 会话的启动、停止和 Raw Input 路由。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from t1remote.core.key_mapping import (
    KeyAction,
    MappingConfig,
    TriggerConfig,
    save_mapping_config,
)
from t1remote.windows.command_runner import CommandExecutionError
from t1remote.windows.driver_bridge import BridgeStatus, DriverInputEvent
from t1remote.windows.mapping_session import MappingSessionError, T1MappingSession
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

    def get_preparsed_data(self, collection: str) -> bytes:
        return collection.encode("ascii")

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


class _CountingBridge(_FakeBridge):
    """记录并发清理是否重复关闭桥接资源。"""

    def __init__(self) -> None:
        super().__init__()
        self.stop_calls = 0
        self.close_calls = 0
        self._count_lock = threading.Lock()

    def stop(self) -> None:
        with self._count_lock:
            self.stop_calls += 1
        super().stop()

    def close(self) -> None:
        with self._count_lock:
            self.close_calls += 1
        super().close()


class _BlockingStartBridge(_FakeBridge):
    """在启动阶段暂停，验证停止不会抢先清理资源。"""

    def __init__(self) -> None:
        super().__init__()
        self.start_entered = threading.Event()
        self.allow_start = threading.Event()

    def start(self) -> None:
        self.start_entered.set()
        self.allow_start.wait(timeout=2)


class _FlushingBridge(_FakeBridge):
    """记录启动前是否清理过旧的驱动队列。"""

    def __init__(self) -> None:
        super().__init__()
        self.flush_calls = 0
        self.lifecycle: list[str] = []

    def flush_events(self) -> None:
        self.flush_calls += 1
        self.lifecycle.append("flush")

    def start(self) -> None:
        self.lifecycle.append("start")


class _ReconnectBridge(_FakeBridge):
    """模拟蓝牙重连后设备重新附着但过滤租约已停止。"""

    def __init__(self) -> None:
        super().__init__()
        self.driver_state = "stopped"
        self.attached_collections = 0x06
        self.lease_active = False
        self.start_calls = 0
        self.parser_calls = []

    def get_preparsed_data(self, collection: str) -> bytes:
        self.parser_calls.append(collection)
        return super().get_preparsed_data(collection)

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


class _SequenceEventBridge(_FakeBridge):
    """按顺序返回多条驱动事件，验证命令失败后仍继续读队列。"""

    def __init__(self, events: list[DriverInputEvent]) -> None:
        super().__init__()
        self._events = list(events)

    def read_event(self):
        if not self._events:
            return None
        return self._events.pop(0)


class _FailingEmitter:
    """模拟 SendInput 失败，验证会话不会继续显示 running。"""

    def emit(self, _outputs) -> None:
        raise RuntimeError("SendInput 测试失败")


class _NoopEmitter:
    """模拟成功的 SendInput，避免会话测试修改系统键盘状态。"""

    def emit(self, _outputs) -> None:
        pass


class _FailingCommandExecutor:
    """模拟命令启动失败，验证会话仍保持运行。"""

    def run(self, _argv) -> None:
        raise CommandExecutionError("测试命令启动失败")


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


class _FakeHidListener:
    """模拟 Mapping 为 COL02/COL03 建立的 HID 读请求泵。"""

    last_instance: "_FakeHidListener | None" = None

    def __init__(self, on_event, on_error, target_collections) -> None:
        self.on_event = on_event
        self.on_error = on_error
        self.target_collections = tuple(target_collections)
        self.started = False
        self.stopped = False
        _FakeHidListener.last_instance = self

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class MappingSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        # 模拟官方解析边界，生命周期测试不依赖真实 HID 设备。
        mock = patch("t1remote.windows.mapping_session.inspect_preparsed_data", return_value=SimpleNamespace(input_button_capabilities=(object(),)))
        mock.start()
        self.addCleanup(mock.stop)
        # 假桥接返回的不是合法 preparsed data；不能把它交给真实原生解析器，
        # 否则解析器按伪造结构读越界内存，测试会随内存布局偶发长时间阻塞。
        parse_mock = patch(
            "t1remote.windows.mapping_session.parse_input_data",
            return_value=(),
        )
        parse_mock.start()
        self.addCleanup(parse_mock.stop)

    def test_parser_failure_prevents_filter_start(self) -> None:
        """缺接口或任一集合查询失败均不得启用过滤。"""
        for missing in (True, False):
            bridge = _FlushingBridge()
            if missing:
                bridge.get_preparsed_data = None
            else:
                def fail(_collection):
                    raise OSError("parser unavailable")
                bridge.get_preparsed_data = fail
            with tempfile.TemporaryDirectory() as directory:
                session = T1MappingSession(str(Path(directory) / "mapping.json"), bridge_factory=lambda: bridge, raw_listener_factory=_FakeRawListener, hid_listener_factory=_FakeHidListener, instance_name=f"T1ParserFailure-{id(bridge)}")
                with self.assertRaises(MappingSessionError):
                    session.start(MappingConfig.default())
                self.assertNotIn("start", bridge.lifecycle)
                self.assertTrue(bridge.closed)
                self.assertEqual(session.status().state, "error")

    def test_parser_is_ready_before_start(self) -> None:
        """两个集合准备完毕后才允许 Start。"""
        bridge = _FlushingBridge()
        def get_data(collection):
            bridge.lifecycle.append(collection)
            return b"parser"
        bridge.get_preparsed_data = get_data
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(str(path), bridge_factory=lambda: bridge, raw_listener_factory=_FakeRawListener, hid_listener_factory=_FakeHidListener, instance_name=f"T1ParserOrder-{id(bridge)}")
            try:
                session.start()
                self.assertLess(bridge.lifecycle.index("COL02"), bridge.lifecycle.index("start"))
                self.assertLess(bridge.lifecycle.index("COL03"), bridge.lifecycle.index("start"))
            finally:
                session.stop()

    def test_reconnect_parser_failure_does_not_restart_filter(self) -> None:
        """重连重新查询失败时保持停止并报告错误。"""
        bridge = _ReconnectBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(str(path), bridge_factory=lambda: bridge, raw_listener_factory=_FakeRawListener, hid_listener_factory=_FakeHidListener, instance_name=f"T1ParserReconnect-{id(bridge)}")
            try:
                session.start()
                def fail(_collection):
                    raise OSError("parser reconnect failed")
                bridge.get_preparsed_data = fail
                bridge.driver_state = "stopped"
                deadline = time.monotonic() + 3
                while session.status().state != "error" and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(session.status().state, "error")
                self.assertEqual(bridge.start_calls, 1)
            finally:
                session.stop()

    def test_stop_waits_until_start_finishes(self) -> None:
        """启动与停止并发时，停止必须等待启动完成再清理。"""

        bridge = _BlockingStartBridge()
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
            starter = threading.Thread(
                target=lambda: session.start(),
                daemon=True,
            )
            starter.start()
            self.assertTrue(bridge.start_entered.wait(timeout=1))
            stopper = threading.Thread(target=session.stop, daemon=True)
            stopper.start()
            time.sleep(0.05)
            self.assertTrue(stopper.is_alive())
            bridge.allow_start.set()
            starter.join(timeout=2)
            stopper.join(timeout=2)

        self.assertFalse(starter.is_alive())
        self.assertFalse(stopper.is_alive())

    def test_concurrent_cleanup_closes_bridge_once(self) -> None:
        """停止按钮和故障线程同时清理时不能重复释放桥接句柄。"""

        bridge = _CountingBridge()
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
            workers = [threading.Thread(target=session._cleanup) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=2)

        self.assertEqual(bridge.stop_calls, 1)
        self.assertEqual(bridge.close_calls, 1)

    def test_start_flushes_stale_driver_events_before_new_mapping(self) -> None:
        bridge = _FlushingBridge()
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
            session.stop()

        self.assertEqual(bridge.flush_calls, 1)
        self.assertEqual(bridge.lifecycle, ["flush", "start"])

    def test_command_failure_does_not_stop_session_or_later_capture(self) -> None:
        bridge = _SequenceEventBridge(
            [
                DriverInputEvent(
                    sequence=1,
                    usage_page=0x0C,
                    usage=0x223,
                    collection="COL02",
                    report=bytes.fromhex("02 23 02"),
                ),
                DriverInputEvent(
                    sequence=2,
                    usage_page=0x0C,
                    usage=0,
                    collection="COL02",
                    report=bytes.fromhex("02 00 00"),
                ),
                DriverInputEvent(
                    sequence=3,
                    usage_page=0x0C,
                    usage=0xE9,
                    collection="COL02",
                    report=bytes.fromhex("02 E9 00"),
                ),
                DriverInputEvent(
                    sequence=4,
                    usage_page=0x0C,
                    usage=0,
                    collection="COL02",
                    report=bytes.fromhex("02 00 00"),
                ),
            ]
        )
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Home": KeyAction("command", argv=("missing.exe",)),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, config)
            with patch(
                "t1remote.windows.mapping_session.WindowsInputEmitter",
                _NoopEmitter,
            ), patch(
                "t1remote.windows.mapping_runtime.WindowsCommandExecutor",
                _FailingCommandExecutor,
            ):
                session = T1MappingSession(
                    path,
                    dry_run=False,
                    bridge_factory=lambda: bridge,
                    raw_listener_factory=_FakeRawListener,
                    hid_listener_factory=_FakeHidListener,
                    instance_name=f"T1RemoteTestSession-{id(bridge)}",
                )
                session.start()
                try:
                    deadline = time.monotonic() + 5
                    while (
                        session.status().diagnostics.input_events < 4
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)
                    status = session.status()
                    self.assertEqual(status.state, "running")
                    self.assertEqual(status.diagnostics.input_events, 4)
                    self.assertEqual(status.diagnostics.errors, 1)
                    self.assertEqual(status.diagnostics.output_events, 2)
                finally:
                    session.stop()

    def test_real_session_starts_hid_read_pump_for_consumer_collections(self) -> None:
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, MappingConfig.default())
            session = T1MappingSession(
                path,
                dry_run=False,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                hid_listener_factory=_FakeHidListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            try:
                assert _FakeHidListener.last_instance is not None
                self.assertTrue(_FakeHidListener.last_instance.started)
                self.assertEqual(
                    _FakeHidListener.last_instance.target_collections,
                    ("COL02", "COL03"),
                )
            finally:
                session.stop()
            self.assertTrue(_FakeHidListener.last_instance.stopped)

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
                hid_listener_factory=_FakeHidListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            try:
                # 模拟设备重连：集合已附着，但原租约对应的过滤状态已经停止。
                bridge.driver_state = "stopped"
                bridge.lease_active = False
                deadline = time.monotonic() + 5
                while bridge.start_calls < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertGreaterEqual(bridge.start_calls, 2)
                self.assertEqual(bridge.parser_calls[:4], ["COL02", "COL03", "COL02", "COL03"])
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
                    hid_listener_factory=_FakeHidListener,
                    instance_name=f"T1RemoteTestSession-{id(bridge)}",
                )
                session.start()
                deadline = time.monotonic() + 5
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

    def test_start_accepts_preloaded_config_without_reading_file_again(self) -> None:
        """验证前台启动前加载的配置可以直接交给会话。"""

        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            config = MappingConfig.default()
            save_mapping_config(path, config)
            session = T1MappingSession(
                path,
                dry_run=True,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            with patch(
                "t1remote.windows.mapping_session.load_mapping_config",
                side_effect=AssertionError("启动时不应重复读取配置"),
            ):
                session.start(config)
            session.stop()

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

    def test_long_press_is_emitted_by_session_polling(self) -> None:
        """桥接队列空闲时，长按计时器仍应按轮询及时触发。"""

        bridge = _EventBridge(
            DriverInputEvent(
                sequence=1,
                usage_page=0x0C,
                usage=0x221,
                collection="COL02",
                report=bytes.fromhex("02 21 02"),
            )
        )
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction(
                    "combo",
                    "D",
                    ("CTRL", "SHIFT"),
                    trigger=TriggerConfig("long_press", threshold_ms=100),
                ),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, config)
            session = T1MappingSession(
                path,
                dry_run=True,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            try:
                deadline = time.monotonic() + 5
                while (
                    session.status().diagnostics.mapping_events < 1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                snapshot = session.status().diagnostics
                self.assertGreaterEqual(snapshot.mapping_events, 1)
                self.assertTrue(
                    any(
                        record.button == "Voice" and record.state == "long_press"
                        for record in snapshot.recent_events
                    )
                )
            finally:
                session.stop()

    def test_queued_driver_reports_use_kernel_timestamps_for_long_press(self) -> None:
        """按下/抬起已排队时，会话仍使用驱动时间戳触发 Voice 长按。"""

        bridge = _SequenceEventBridge(
            [
                DriverInputEvent(
                    sequence=1,
                    usage_page=0x0C,
                    usage=0x221,
                    collection="COL02",
                    report=bytes.fromhex("02 21 02"),
                    timestamp_100ns=1_000_000_000,
                ),
                DriverInputEvent(
                    sequence=2,
                    usage_page=0x0C,
                    usage=0,
                    collection="COL02",
                    report=bytes.fromhex("02 00 00"),
                    timestamp_100ns=1_001_000_000,
                ),
            ]
        )
        config = MappingConfig(
            mappings={
                **MappingConfig.default().mappings,
                "Voice": KeyAction(
                    "combo",
                    "D",
                    ("CTRL", "SHIFT"),
                    trigger=TriggerConfig("long_press", threshold_ms=100),
                ),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_mapping_config(path, config)
            session = T1MappingSession(
                path,
                dry_run=True,
                bridge_factory=lambda: bridge,
                raw_listener_factory=_FakeRawListener,
                instance_name=f"T1RemoteTestSession-{id(bridge)}",
            )
            session.start()
            try:
                deadline = time.monotonic() + 5
                while (
                    session.status().diagnostics.mapping_events < 2
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                snapshot = session.status().diagnostics
                self.assertEqual(snapshot.mapping_events, 2)
                self.assertEqual(snapshot.errors, 0)
            finally:
                session.stop()

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
