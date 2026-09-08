"""程序说明：验证 IMA-DVI 解码器和有界 PCM 队列。"""

from __future__ import annotations

import threading
import time
import unittest
import wave
from pathlib import Path
import tempfile

from t1remote.core.audio_buffer import PcmFormat, PcmFrameQueue
from t1remote.core.audio_pipeline import ImaPcmPipeline, samples_to_pcm16le
from t1remote.core.ima_adpcm import ImaAdpcmDecoder, decode_ima_adpcm
from t1remote.core.pcm_sink import PcmSinkError, WaveFilePcmSink
from t1remote.core.sounddevice_sink import (
    SoundDevicePcmSink,
    enumerate_output_devices,
)


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

    def test_pipeline_decodes_and_queues_pcm16le(self) -> None:
        queue = PcmFrameQueue()
        pipeline = ImaPcmPipeline(queue)

        pcm = pipeline.feed(b"\x77")

        self.assertEqual(pcm, samples_to_pcm16le((11, 41)))
        self.assertEqual(queue.pop(), pcm)
        self.assertEqual(pipeline.stats.decoded_samples, 2)

    def test_wave_sink_writes_pcm16le_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            with WaveFilePcmSink(path, PcmFormat(16000, 1)) as sink:
                sink.write(b"\x0b\x00\x29\x00")

            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getframerate(), 16000)
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.readframes(2), b"\x0b\x00\x29\x00")
            with self.assertRaises(PcmSinkError):
                WaveFilePcmSink(path, PcmFormat(16000, 1))

    def test_sounddevice_sink_uses_injected_stream_factory(self) -> None:
        created: list[dict[str, object]] = []
        instances: list[object] = []

        class FakeStream:
            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)
                instances.append(self)
                self.writes: list[bytes] = []
                self.started = False
                self.closed = False

            def start(self) -> None:
                self.started = True

            def write(self, chunk: bytes) -> None:
                self.writes.append(chunk)

            def stop(self) -> None:
                self.started = False

            def close(self) -> None:
                self.closed = True

        with SoundDevicePcmSink(
            PcmFormat(16000, 1),
            device="test-output",
            stream_factory=FakeStream,
        ) as sink:
            sink.write(b"\x00\x00")

        self.assertEqual(created[0]["samplerate"], 16000)
        self.assertEqual(instances[0].writes, [b"\x00\x00"])
        self.assertTrue(instances[0].closed)

    def test_enumerates_only_output_devices(self) -> None:
        devices = enumerate_output_devices(
            query_devices=lambda: [
                {"name": "input", "max_output_channels": 0},
                {
                    "name": "speaker",
                    "max_output_channels": 2,
                    "default_samplerate": 48000,
                },
            ]
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].index, 1)
        self.assertEqual(devices[0].name, "speaker")


if __name__ == "__main__":
    unittest.main()
