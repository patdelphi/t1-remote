"""程序说明：把 ATVV v0.4 音频帧接入现有 GATT 会话和 PCM 队列。"""

from __future__ import annotations

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.audio_pipeline import AudioPipelineStats
from t1remote.core.atvv_protocol import AtvvAudioFrameAssembler
from t1remote.core.gatt_audio_pipeline import GattAudioPipelineSnapshot
from t1remote.core.gatt_session import GattAudioSession


class AtvvV04AudioProcessor:
    """处理 v0.4 分片通知，并按音频帧边界重置 ADPCM 解码状态。"""

    def __init__(self, session: GattAudioSession, queue: PcmFrameQueue) -> None:
        self._session = session
        self._queue = queue
        self._assembler = AtvvAudioFrameAssembler()
        self._decoded_samples = 0
        self._emitted_chunks = 0

    @property
    def session(self) -> GattAudioSession:
        """返回 GATT 控制器使用的会话对象。"""

        return self._session

    @property
    def queue(self) -> PcmFrameQueue:
        """返回解码后的 PCM 队列。"""

        return self._queue

    @property
    def snapshot(self) -> GattAudioPipelineSnapshot:
        """返回与通用 GATT 管线一致的统计快照。"""

        return GattAudioPipelineSnapshot(
            session=self._session.snapshot,
            audio=AudioPipelineStats(
                decoded_samples=self._decoded_samples,
                emitted_chunks=self._emitted_chunks,
                dropped_chunks=self._queue.stats.dropped_chunks,
            ),
        )

    def handle_notification(self, generation: int, payload: bytes) -> bool:
        """只处理当前流式会话的通知，并把完整帧送入 PCM 队列。"""

        def process_payload(notification: bytes) -> None:
            for frame in self._assembler.feed(notification):
                pcm = frame.pcm16le
                self._decoded_samples += len(frame.samples)
                if pcm and self._queue.push(pcm):
                    self._emitted_chunks += 1

        return self._session.accept_notification(
            generation,
            payload,
            process_payload,
        )

    def reset(self) -> None:
        """清理未完成的分片和统计，供新会话重新开始。"""

        self._assembler.reset()
        self._decoded_samples = 0
        self._emitted_chunks = 0


__all__ = ["AtvvV04AudioProcessor"]
