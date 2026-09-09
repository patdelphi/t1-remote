"""程序说明：验证虚拟音频线 PCM 路由端点的安全边界。"""

from __future__ import annotations

import unittest

from t1remote.core.audio_buffer import PcmFormat
from t1remote.core.pcm_sink import PcmSinkError
from t1remote.core.sounddevice_sink import (
    enumerate_input_devices,
    enumerate_output_devices,
)
from t1remote.core.virtual_mic import VirtualMicrophonePcmSink


class VirtualMicrophonePcmSinkTests(unittest.TestCase):
    def test_enumerates_only_input_endpoints(self) -> None:
        devices = enumerate_input_devices(
            query_devices=lambda: [
                {"name": "speaker", "max_input_channels": 0},
                {
                    "name": "CABLE Output",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
            ]
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].index, 1)
        self.assertEqual(devices[0].name, "CABLE Output")

    def test_audio_devices_keep_host_api_identity(self) -> None:
        devices = enumerate_output_devices(
            query_devices=lambda: [
                {
                    "name": "CABLE Input",
                    "max_output_channels": 2,
                    "default_samplerate": 48_000,
                    "hostapi": 2,
                }
            ],
            query_hostapis=lambda: [{"name": "MME"}, {"name": "DirectSound"}, {"name": "Windows WASAPI"}],
        )

        self.assertEqual(devices[0].hostapi_index, 2)
        self.assertEqual(devices[0].hostapi_name, "Windows WASAPI")

    def test_requires_an_explicit_virtual_cable_output_device(self) -> None:
        with self.assertRaises(PcmSinkError):
            VirtualMicrophonePcmSink(
                PcmFormat(16_000, 1),
                device=None,
                stream_factory=lambda **_kwargs: object(),
                settings_factory=lambda **_kwargs: object(),
            )

    def test_writes_pcm_to_selected_virtual_cable_device(self) -> None:
        created: list[dict[str, object]] = []

        class FakeStream:
            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)
                self.writes: list[bytes] = []

            def start(self) -> None:
                pass

            def write(self, chunk: bytes) -> None:
                self.writes.append(chunk)

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

        with VirtualMicrophonePcmSink(
            PcmFormat(16_000, 1),
            device="VB-CABLE Input",
            stream_factory=FakeStream,
            settings_factory=lambda **kwargs: ("wasapi", kwargs),
        ) as sink:
            sink.write(b"\x01\x00")

        self.assertEqual(created[0]["device"], "VB-CABLE Input")
        self.assertEqual(created[0]["extra_settings"], ("wasapi", {"exclusive": False}))


if __name__ == "__main__":
    unittest.main()
