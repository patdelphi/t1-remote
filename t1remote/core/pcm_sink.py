"""程序说明：提供不依赖第三方库的 PCM16LE WAV 输出端点。"""

from __future__ import annotations

from pathlib import Path
import wave
from typing import BinaryIO

from t1remote.core.audio_buffer import PcmFormat


class PcmSinkError(RuntimeError):
    """PCM 输出端点打开、写入或关闭失败。"""


class WaveFilePcmSink:
    """把 PCM16LE chunk 写入新的 WAV 文件，禁止覆盖已有文件。"""

    def __init__(self, path: Path, pcm_format: PcmFormat) -> None:
        if pcm_format.sample_width_bytes != 2:
            raise ValueError("WaveFilePcmSink 当前只支持 PCM16LE")
        self._path = path
        self._format = pcm_format
        self._raw_file: BinaryIO | None = None
        self._wave: wave.Wave_write | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = self._path.open("xb")
            self._wave = wave.open(self._raw_file, "wb")
            self._wave.setnchannels(pcm_format.channels)
            self._wave.setsampwidth(pcm_format.sample_width_bytes)
            self._wave.setframerate(pcm_format.sample_rate)
        except (OSError, wave.Error) as error:
            self.close()
            raise PcmSinkError(f"无法创建 WAV PCM 输出：{self._path}") from error

    def write(self, chunk: bytes) -> None:
        """写入一个完整的交错 PCM16LE chunk。"""

        if not isinstance(chunk, bytes):
            raise TypeError("PCM chunk 必须是 bytes")
        if len(chunk) % self._format.bytes_per_sample_frame:
            raise ValueError("PCM chunk 不是完整 sample frame 的整数倍")
        if self._wave is None:
            raise PcmSinkError("WAV PCM 输出已经关闭")
        try:
            self._wave.writeframesraw(chunk)
        except (OSError, wave.Error) as error:
            raise PcmSinkError("写入 WAV PCM 失败") from error

    def close(self) -> None:
        """完成 WAV 头部并关闭文件；重复调用安全。"""

        if self._wave is None:
            return
        wave_file = self._wave
        raw_file = self._raw_file
        self._wave = None
        self._raw_file = None
        try:
            wave_file.close()
        except (OSError, wave.Error) as error:
            raise PcmSinkError("关闭 WAV PCM 输出失败") from error
        finally:
            if raw_file is not None:
                raw_file.close()

    def __enter__(self) -> "WaveFilePcmSink":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = ["PcmSinkError", "WaveFilePcmSink"]
