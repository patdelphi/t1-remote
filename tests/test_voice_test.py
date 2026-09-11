"""程序说明：验证真实语音测试入口的虚拟声卡选择和生命周期编排。"""

from __future__ import annotations

import asyncio
import threading
import unittest
import tempfile
from pathlib import Path
import wave

from t1remote.core.sounddevice_sink import AudioOutputDevice
from tools.t1_voice_test import (
    VoiceTestError,
    VoiceTestRunner,
    select_virtual_cable_output,
)


class _FakeSnapshot:
    def __init__(self, microphone_open: bool = False) -> None:
        self.microphone_open = microphone_open


class _FakeController:
    def __init__(self, _transport, _queue) -> None:
        self.snapshot = _FakeSnapshot()
        self.calls: list[str] = []

    async def connect(self) -> None:
        self.calls.append("connect")

    async def negotiate(self, *, timeout: float) -> object:
        self.calls.append(f"negotiate:{timeout}")
        return object()

    async def open_microphone(self) -> None:
        self.calls.append("open_microphone")
        self.snapshot.microphone_open = True

    async def close_microphone(self) -> None:
        self.calls.append("close_microphone")
        self.snapshot.microphone_open = False

    async def disconnect(self) -> None:
        self.calls.append("disconnect")


class _FakeWorker:
    def __init__(self, _queue, _sink) -> None:
        self.calls: list[str] = []

    @property
    def snapshot(self) -> object:
        return object()

    def start(self) -> None:
        self.calls.append("start")

    def stop(self, *, discard: bool = False) -> None:
        self.calls.append(f"stop:{discard}")


class VoiceTestTests(unittest.TestCase):
    def test_selects_wasapi_cable_input_and_ignores_cable_16ch(self) -> None:
        devices = (
            AudioOutputDevice(5, "CABLE Input (VB-Audio Virtual Cable)", 16, 44100, 0, "MME"),
            AudioOutputDevice(6, "CABLE In 16ch (VB-Audio Virtual Cable)", 16, 44100, 0, "MME"),
            AudioOutputDevice(23, "CABLE Input (VB-Audio Virtual Cable)", 2, 48000, 2, "Windows WASAPI"),
        )

        selected = select_virtual_cable_output(devices)

        self.assertEqual(selected.index, 23)
        self.assertEqual(selected.hostapi_name, "Windows WASAPI")

    def test_select_virtual_cable_output_requires_exact_endpoint(self) -> None:
        with self.assertRaises(VoiceTestError):
            select_virtual_cable_output(
                (AudioOutputDevice(6, "CABLE In 16ch (VB-Audio Virtual Cable)", 16, 44100, 0, "MME"),)
            )

    def test_runner_closes_microphone_before_controller_disconnect(self) -> None:
        async def scenario() -> None:
            runner = VoiceTestRunner(
                "test-address",
                duration_seconds=0,
                device_index=23,
                controller_factory=_FakeController,
                worker_factory=_FakeWorker,
                sink_factory=lambda *_args, **_kwargs: object(),
                transport_factory=lambda _address: object(),
                recording_dir=None,
                output_devices_factory=lambda: (
                    AudioOutputDevice(23, "CABLE Input (VB-Audio Virtual Cable)", 2, 48000, 2, "Windows WASAPI"),
                ),
            )

            await runner.run()

            self.assertEqual(
                runner.controller.calls,
                ["connect", "negotiate:8.0", "open_microphone", "close_microphone", "disconnect"],
            )
            self.assertEqual(runner.worker.calls, ["start", "stop:False"])

        asyncio.run(scenario())

    def test_runner_stop_event_interrupts_duration_wait(self) -> None:
        async def scenario() -> None:
            stop_event = threading.Event()

            class _StoppingController(_FakeController):
                async def open_microphone(self) -> None:
                    await super().open_microphone()
                    stop_event.set()

            runner = VoiceTestRunner(
                "test-address",
                duration_seconds=60,
                stop_event=stop_event,
                device_index=23,
                controller_factory=_StoppingController,
                worker_factory=_FakeWorker,
                sink_factory=lambda *_args, **_kwargs: object(),
                transport_factory=lambda _address: object(),
                recording_dir=None,
                output_devices_factory=lambda: (
                    AudioOutputDevice(
                        23,
                        "CABLE Input (VB-Audio Virtual Cable)",
                        2,
                        48000,
                        2,
                        "Windows WASAPI",
                    ),
                ),
            )

            await asyncio.wait_for(runner.run(), timeout=1.0)
            self.assertEqual(runner.controller.calls[-2:], ["close_microphone", "disconnect"])

        asyncio.run(scenario())

    def test_runner_keeps_one_latest_voice_recording(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runner = VoiceTestRunner(
                    "test-address",
                    duration_seconds=0,
                    device_index=23,
                    controller_factory=_FakeController,
                    transport_factory=lambda _address: object(),
                    recording_dir=Path(directory),
                    output_devices_factory=lambda: (
                        AudioOutputDevice(
                            23,
                            "CABLE Input (VB-Audio Virtual Cable)",
                            2,
                            48000,
                            2,
                            "Windows WASAPI",
                        ),
                    ),
                    sink_factory=lambda *_args, **_kwargs: _NullSink(),
                )

                result = await runner.run()
                second_result = await VoiceTestRunner(
                    "test-address",
                    duration_seconds=0,
                    device_index=23,
                    controller_factory=_FakeController,
                    transport_factory=lambda _address: object(),
                    recording_dir=Path(directory),
                    output_devices_factory=lambda: (
                        AudioOutputDevice(
                            23,
                            "CABLE Input (VB-Audio Virtual Cable)",
                            2,
                            48000,
                            2,
                            "Windows WASAPI",
                        ),
                    ),
                    sink_factory=lambda *_args, **_kwargs: _NullSink(),
                ).run()

                self.assertIsNotNone(result.recording_path)
                self.assertEqual(result.recording_path, second_result.recording_path)
                assert result.recording_path is not None
                self.assertTrue(result.recording_path.is_file())
                with wave.open(str(result.recording_path), "rb") as audio:
                    self.assertEqual(audio.getframerate(), 16000)
                    self.assertEqual(audio.getnchannels(), 1)
                    self.assertEqual(audio.getsampwidth(), 2)

        asyncio.run(scenario())


class _NullSink:
    """不连接声卡的 PCM 端点，用于验证录音文件生命周期。"""

    def write(self, _chunk: bytes) -> None:
        pass

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
