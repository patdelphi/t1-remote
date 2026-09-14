"""程序说明：验证主窗口使用的语音会话后台线程和停止生命周期。"""

from __future__ import annotations

import threading
import time
import unittest

from t1remote.windows.voice_session import (
    VoiceSessionController,
    VoiceSessionError,
)


class _FakeRunner:
    """用可控事件模拟真实 VoiceTestRunner，避免测试连接蓝牙设备。"""

    instances: list["_FakeRunner"] = []

    def __init__(self, _address: str, *, stop_event: threading.Event, **_kwargs: object) -> None:
        self.stop_event = stop_event
        self.started = threading.Event()
        self.finished = threading.Event()
        self.progress = _kwargs.get("progress")
        self.duration_seconds = _kwargs.get("duration_seconds")
        self.__class__.instances.append(self)

    async def run(self) -> object:
        self.started.set()
        if callable(self.progress):
            self.progress("模拟语音会话已启动")
        while not self.stop_event.is_set():
            await _sleep_briefly()
        self.finished.set()
        return object()


async def _sleep_briefly() -> None:
    """让出事件循环，避免伪造 Runner 忙等。"""

    import asyncio

    await asyncio.sleep(0.01)


class VoiceSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeRunner.instances.clear()

    def test_start_and_stop_runs_runner_in_background(self) -> None:
        statuses: list[str] = []
        controller = VoiceSessionController(
            runner_factory=_FakeRunner,
            on_status=lambda status: statuses.append(status.state),
        )

        controller.start("test-address", duration_seconds=10)
        self.assertTrue(_wait_until(lambda: bool(_FakeRunner.instances)))
        runner = _FakeRunner.instances[0]
        self.assertTrue(runner.started.wait(1.0))
        self.assertIn(controller.status().state, {"running", "stopping"})

        controller.stop(wait=True)

        self.assertTrue(runner.finished.is_set())
        self.assertEqual(controller.status().state, "stopped")
        self.assertIn("starting", statuses)
        self.assertIn("stopped", statuses)

    def test_start_rejects_duplicate_active_session(self) -> None:
        controller = VoiceSessionController(runner_factory=_FakeRunner)
        controller.start("test-address", duration_seconds=10)
        self.assertTrue(_wait_until(lambda: bool(_FakeRunner.instances)))

        with self.assertRaises(VoiceSessionError):
            controller.start("another-address", duration_seconds=10)

        controller.stop(wait=True)

    def test_zero_duration_keeps_session_active_until_stop(self) -> None:
        """时长 0 表示持续收音：会话保持运行，直到外部调用停止。"""

        controller = VoiceSessionController(runner_factory=_FakeRunner)
        controller.start("test-address", duration_seconds=0)
        self.assertTrue(_wait_until(lambda: bool(_FakeRunner.instances)))
        runner = _FakeRunner.instances[0]
        self.assertTrue(runner.started.wait(1.0))
        self.assertEqual(runner.duration_seconds, 0)

        # 持续收音不会自行结束。
        self.assertFalse(
            _wait_until(lambda: controller.status().state == "stopped", timeout=0.3)
        )
        self.assertIn(controller.status().state, {"running", "stopping"})

        controller.stop(wait=True)

        self.assertTrue(runner.finished.is_set())
        self.assertEqual(controller.status().state, "stopped")

    def test_waveform_callback_is_exposed_in_status(self) -> None:
        statuses = []

        class _WaveformRunner(_FakeRunner):
            async def run(self) -> object:
                callback = self._kwargs.get("waveform_callback")
                if callable(callback):
                    callback((-0.5, 0.5))
                self.stop_event.set()
                return object()

            def __init__(self, address: str, *, stop_event: threading.Event, **kwargs: object) -> None:
                super().__init__(address, stop_event=stop_event, **kwargs)
                self._kwargs = kwargs

        controller = VoiceSessionController(
            runner_factory=_WaveformRunner,
            on_status=lambda status: statuses.append(status),
        )
        controller.start("test-address", duration_seconds=1)
        self.assertTrue(_wait_until(lambda: controller.status().state == "stopped"))
        self.assertEqual(controller.status().waveform_points, (-0.5, 0.5))


def _wait_until(predicate: object, timeout: float = 1.0) -> bool:
    """轮询后台线程状态，给 Windows 调度留出时间。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        time.sleep(0.01)
    return False


if __name__ == "__main__":
    unittest.main()
