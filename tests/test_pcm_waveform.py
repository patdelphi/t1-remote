"""程序说明：验证 PCM 实时波形压缩和输出包装端点。"""

from __future__ import annotations

import struct
import unittest

from t1remote.core.pcm_sink import PcmWaveformSink, pcm16le_waveform_points


class PcmWaveformTests(unittest.TestCase):
    def test_waveform_points_preserve_short_pcm_samples(self) -> None:
        chunk = struct.pack("<hhhh", -32768, -16384, 0, 32767)

        self.assertEqual(
            pcm16le_waveform_points(chunk, point_count=8),
            (-1.0, -0.5, 0.0, 32767 / 32768),
        )

    def test_waveform_sink_forwards_pcm_when_callback_fails(self) -> None:
        class FakeSink:
            def __init__(self) -> None:
                self.writes: list[bytes] = []
                self.closed = False

            def write(self, chunk: bytes) -> None:
                self.writes.append(chunk)

            def close(self) -> None:
                self.closed = True

        delegate = FakeSink()
        sink = PcmWaveformSink(delegate, lambda _points: 1 / 0)
        sink.write(b"\x00\x00")
        sink.close()

        self.assertEqual(delegate.writes, [b"\x00\x00"])
        self.assertTrue(delegate.closed)


if __name__ == "__main__":
    unittest.main()
