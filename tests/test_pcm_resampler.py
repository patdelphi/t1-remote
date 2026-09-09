"""程序说明：验证 PCM16LE 采样率转换和虚拟麦克风路由。"""

from __future__ import annotations

import unittest

from t1remote.core.audio_buffer import PcmFormat
from t1remote.core.virtual_mic import VirtualMicrophonePcmSink


class PcmResamplerTests(unittest.TestCase):
    def test_virtual_mic_resamples_8khz_source_to_48khz_output(self) -> None:
        created: list[dict[str, object]] = []
        streams: list[object] = []

        class FakeStream:
            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)
                self.writes: list[bytes] = []
                streams.append(self)

            def start(self) -> None:
                pass

            def write(self, chunk: bytes) -> None:
                self.writes.append(chunk)

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

        with VirtualMicrophonePcmSink(
            PcmFormat(8_000, 1),
            device="CABLE Input",
            output_sample_rate=48_000,
            stream_factory=FakeStream,
            settings_factory=lambda **kwargs: kwargs,
        ) as sink:
            sink.write((0).to_bytes(2, "little", signed=True))
            sink.write((1000).to_bytes(2, "little", signed=True))

        self.assertEqual(created[0]["samplerate"], 48_000)
        self.assertEqual(len(streams[0].writes[0]), 12)
        self.assertEqual(len(streams[0].writes[1]), 12)


if __name__ == "__main__":
    unittest.main()
