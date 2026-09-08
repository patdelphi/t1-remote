"""程序说明：验证 PCM 队列到音频端点的后台输出泵。"""

from __future__ import annotations

import threading
import unittest

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.pcm_output import PcmSinkWorker
from t1remote.core.pcm_sink import PcmSinkError


class _FakeSink:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.written = threading.Event()

    def write(self, chunk: bytes) -> None:
        self.writes.append(chunk)
        self.written.set()

    def close(self) -> None:
        self.closed = True


class _FailingSink(_FakeSink):
    def write(self, _chunk: bytes) -> None:
        self.written.set()
        raise OSError("output failed")


class PcmOutputTests(unittest.TestCase):
    def test_worker_drains_queue_and_closes_sink(self) -> None:
        queue = PcmFrameQueue()
        sink = _FakeSink()
        worker = PcmSinkWorker(queue, sink)

        worker.start()
        self.assertTrue(queue.push(b"\x01\x00"))
        self.assertTrue(sink.written.wait(1.0))
        worker.stop()

        self.assertEqual(sink.writes, [b"\x01\x00"])
        self.assertTrue(sink.closed)
        self.assertEqual(worker.snapshot.chunks_written, 1)
        self.assertEqual(worker.snapshot.bytes_written, 2)
        self.assertFalse(worker.snapshot.running)

    def test_sink_error_is_reported_and_queue_is_closed(self) -> None:
        queue = PcmFrameQueue()
        sink = _FailingSink()
        worker = PcmSinkWorker(queue, sink)

        worker.start()
        self.assertTrue(queue.push(b"\x00\x00"))
        self.assertTrue(sink.written.wait(1.0))
        with self.assertRaises(PcmSinkError):
            worker.stop()

        self.assertIsNotNone(worker.snapshot.error)
        self.assertTrue(queue.stats.closed)
        self.assertTrue(sink.closed)


if __name__ == "__main__":
    unittest.main()
