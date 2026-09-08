"""程序说明：验证 IMA-DVI 解码器和有界 PCM 队列。"""

from __future__ import annotations

import threading
import time
import unittest

from t1remote.core.audio_buffer import PcmFormat, PcmFrameQueue
from t1remote.core.ima_adpcm import ImaAdpcmDecoder, decode_ima_adpcm


class ImaAdpcmTests(unittest.TestCase):
    """覆盖标准 nibble、块头和边界状态。"""

    def test_decodes_low_nibble_first_and_updates_state(self) -> None:
        decoder = ImaAdpcmDecoder()

        samples = decoder.decode(bytes([0x77]))

        self.assertEqual(samples, (11, 41))
        self.assertEqual(decoder.state.step_index, 16)

    def test_decodes_high_nibble_first(self) -> None:
        samples = decode_ima_adpcm(bytes([0x70]), low_nibble_first=False)

        self.assertEqual(samples, (11, 13))

    def test_wav_block_uses_header_predictor_and_index(self) -> None:
        decoder = ImaAdpcmDecoder()

        samples = decoder.decode_wav_ima_block((100).to_bytes(2, "little", signed=True) + b"\x00\x00\x00")

        self.assertEqual(samples, (100, 100, 100))
        self.assertEqual(decoder.state.predictor, 100)

    def test_rejects_invalid_state(self) -> None:
        with self.assertRaises(ValueError):
            ImaAdpcmDecoder(step_index=89)


class PcmFrameQueueTests(unittest.TestCase):
    """覆盖容量、关闭和消费者等待语义。"""

    def test_drops_oldest_chunk_when_full(self) -> None:
        queue = PcmFrameQueue(max_chunks=2)

        queue.push(b"a")
        queue.push(b"b")
        queue.push(b"c")

        self.assertEqual(queue.pop(), b"b")
        self.assertEqual(queue.pop(), b"c")
        self.assertEqual(queue.stats.dropped_chunks, 1)

    def test_close_drains_existing_data_then_returns_none(self) -> None:
        queue = PcmFrameQueue()
        queue.push(b"pcm")
        queue.close()

        self.assertEqual(queue.pop(), b"pcm")
        self.assertIsNone(queue.pop(timeout=0))
        self.assertFalse(queue.push(b"late"))

    def test_waiting_consumer_is_released_by_push(self) -> None:
        queue = PcmFrameQueue()
        result: list[bytes | None] = []

        thread = threading.Thread(target=lambda: result.append(queue.pop(timeout=1)))
        thread.start()
        time.sleep(0.01)
        queue.push(b"pcm")
        thread.join(timeout=1)

        self.assertEqual(result, [b"pcm"])

    def test_pcm_format_reports_interleaved_frame_size(self) -> None:
        self.assertEqual(PcmFormat(16000, 2, 2).bytes_per_sample_frame, 4)


if __name__ == "__main__":
    unittest.main()
