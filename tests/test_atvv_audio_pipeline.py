"""程序说明：验证 ATVV v0.4 帧处理器接入 GATT 会话和 PCM 队列。"""

from __future__ import annotations

import unittest

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.atvv_audio import AtvvV04AudioProcessor
from t1remote.core.gatt_session import GattAudioSession, GattSessionState


def _frame(sequence: int) -> bytes:
    """生成预测值固定、便于断言的 134 字节测试帧。"""

    return sequence.to_bytes(2, "big") + bytes.fromhex("00123400") + bytes(128)


class AtvvV04AudioProcessorTests(unittest.TestCase):
    def test_fragmented_frame_is_pushed_as_pcm(self) -> None:
        session = GattAudioSession()
        queue = PcmFrameQueue()
        processor = AtvvV04AudioProcessor(session, queue)
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)

        payload = _frame(7)
        self.assertTrue(processor.handle_notification(generation, payload[:20]))
        self.assertIsNone(queue.pop(timeout=0))
        self.assertTrue(processor.handle_notification(generation, payload[20:]))
        pcm = queue.pop()
        self.assertIsNotNone(pcm)
        assert pcm is not None
        self.assertEqual(len(pcm), 257 * 2)
        self.assertEqual(pcm[:2], b"4\x12")
        self.assertEqual(processor.snapshot.audio.decoded_samples, 257)
        self.assertEqual(processor.snapshot.audio.emitted_chunks, 1)

    def test_notifications_are_rejected_when_session_is_not_streaming(self) -> None:
        session = GattAudioSession()
        processor = AtvvV04AudioProcessor(session, PcmFrameQueue())

        self.assertFalse(processor.handle_notification(1, _frame(1)))
        self.assertEqual(session.snapshot.state, GattSessionState.DISCONNECTED)
        self.assertEqual(session.snapshot.ignored_notifications, 1)


if __name__ == "__main__":
    unittest.main()
