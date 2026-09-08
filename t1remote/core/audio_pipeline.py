"""程序说明：连接 ADPCM 解码器与有界 PCM 队列的纯 Python 音频管线。"""

from __future__ import annotations

from dataclasses import dataclass

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.ima_adpcm import ImaAdpcmDecoder


def samples_to_pcm16le(samples: tuple[int, ...]) -> bytes:
    """把有符号样本编码为交给音频端点的 PCM16LE 字节。"""

    output = bytearray()
    for sample in samples:
        if not -32768 <= sample <= 32767:
            raise ValueError("PCM 样本超出 16 位范围")
        output.extend(int(sample).to_bytes(2, "little", signed=True))
    return bytes(output)


@dataclass(frozen=True)
class AudioPipelineStats:
    """音频管线运行统计。"""

    decoded_samples: int
    emitted_chunks: int
    dropped_chunks: int


class ImaPcmPipeline:
    """将已剥离私有帧头的 IMA ADPCM 载荷转换为 PCM16LE。"""

    def __init__(
        self,
        queue: PcmFrameQueue,
        *,
        decoder: ImaAdpcmDecoder | None = None,
        low_nibble_first: bool = True,
    ) -> None:
        self._queue = queue
        self._decoder = decoder or ImaAdpcmDecoder()
        self._low_nibble_first = low_nibble_first
        self._decoded_samples = 0
        self._emitted_chunks = 0

    @property
    def decoder(self) -> ImaAdpcmDecoder:
        """返回管线使用的解码器，供帧边界重置。"""

        return self._decoder

    @property
    def stats(self) -> AudioPipelineStats:
        """返回解码和队列统计。"""

        return AudioPipelineStats(
            decoded_samples=self._decoded_samples,
            emitted_chunks=self._emitted_chunks,
            dropped_chunks=self._queue.stats.dropped_chunks,
        )

    def feed(self, adpcm_payload: bytes) -> bytes:
        """解码一个载荷并推入 PCM 队列，返回本次 PCM 字节。"""

        samples = self._decoder.decode(
            adpcm_payload,
            low_nibble_first=self._low_nibble_first,
        )
        pcm = samples_to_pcm16le(samples)
        self._decoded_samples += len(samples)
        if pcm and self._queue.push(pcm):
            self._emitted_chunks += 1
        return pcm

    def reset(self, *, predictor: int = 0, step_index: int = 0) -> None:
        """在新的音频帧或新会话开始时重置 ADPCM 状态。"""

        self._decoder.reset(predictor=predictor, step_index=step_index)


__all__ = ["AudioPipelineStats", "ImaPcmPipeline", "samples_to_pcm16le"]
