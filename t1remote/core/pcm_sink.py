"""程序说明：提供不依赖第三方库的 PCM16LE WAV 输出端点。"""

from __future__ import annotations

from pathlib import Path
import struct
import wave
from typing import Any, BinaryIO

from t1remote.core.audio_buffer import PcmFormat


class PcmSinkError(RuntimeError):
    """PCM 输出端点打开、写入或关闭失败。"""


def pcm16le_waveform_points(chunk: bytes, *, point_count: int = 96) -> tuple[float, ...]:
    """把 PCM16LE chunk 压缩成适合 Canvas 绘制的归一化波形点。"""

    if point_count < 1:
        raise ValueError("point_count 必须大于 0")
    if not isinstance(chunk, bytes):
        raise TypeError("PCM chunk 必须是 bytes")
    if len(chunk) % 2:
        raise ValueError("PCM16LE chunk 长度必须是偶数")
    sample_count = len(chunk) // 2
    if sample_count == 0:
        return ()
    samples = struct.unpack(f"<{sample_count}h", chunk)
    if sample_count <= point_count:
        return tuple(max(-1.0, min(1.0, sample / 32768.0)) for sample in samples)
    points: list[float] = []
    for index in range(point_count):
        start = index * sample_count // point_count
        end = max(start + 1, (index + 1) * sample_count // point_count)
        points.append(
            max(
                samples[start:end],
                key=lambda sample: abs(sample),
            )
            / 32768.0
        )
    return tuple(max(-1.0, min(1.0, point)) for point in points)


class WaveFilePcmSink:
    """把 PCM16LE chunk 写入 WAV；默认禁止覆盖已有文件。"""

    def __init__(self, path: Path, pcm_format: PcmFormat, *, overwrite: bool = False) -> None:
        if pcm_format.sample_width_bytes != 2:
            raise ValueError("WaveFilePcmSink 当前只支持 PCM16LE")
        self._path = path
        self._format = pcm_format
        self._raw_file: BinaryIO | None = None
        self._wave: wave.Wave_write | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = self._path.open("wb" if overwrite else "xb")
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


class RecordingPcmSink:
    """把原始 PCM 同时转发到输出端点并保存为 WAV。"""

    def __init__(
        self,
        sink: Any,
        path: Path,
        pcm_format: PcmFormat,
        *,
        overwrite: bool = False,
    ) -> None:
        self.path = path
        self._sink = sink
        try:
            self._recording = WaveFilePcmSink(path, pcm_format, overwrite=overwrite)
        except Exception:
            # 录音文件创建失败时立即释放已经打开的音频端点，避免设备句柄泄漏。
            try:
                sink.close()
            except Exception:
                pass
            raise

    def write(self, chunk: bytes) -> None:
        """先保存输入 PCM，再转发到虚拟音频线。"""

        self._recording.write(chunk)
        self._sink.write(chunk)

    def close(self) -> None:
        """关闭两个端点，即使其中一个关闭失败也继续释放另一个。"""

        errors: list[Exception] = []
        for sink in (self._recording, self._sink):
            try:
                sink.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise PcmSinkError("关闭录音或 PCM 输出端点失败") from errors[0]

    def __enter__(self) -> "RecordingPcmSink":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


class PcmWaveformSink:
    """转发 PCM 并以低频回调归一化波形点，回调失败不影响音频输出。"""

    def __init__(
        self,
        sink: Any,
        on_waveform: Any,
        *,
        point_count: int = 96,
    ) -> None:
        if not callable(on_waveform):
            raise TypeError("on_waveform 必须是可调用对象")
        if point_count < 1:
            raise ValueError("point_count 必须大于 0")
        self._sink = sink
        self._on_waveform = on_waveform
        self._point_count = point_count

    def write(self, chunk: bytes) -> None:
        """先通知波形，再转发原始 PCM。"""

        try:
            points = pcm16le_waveform_points(chunk, point_count=self._point_count)
            if points:
                self._on_waveform(points)
        except Exception:
            # UI 波形异常不能导致真实语音输出中断。
            pass
        self._sink.write(chunk)

    def close(self) -> None:
        """关闭被包装的 PCM 输出端点。"""

        self._sink.close()


__all__ = [
    "PcmSinkError",
    "PcmWaveformSink",
    "RecordingPcmSink",
    "WaveFilePcmSink",
    "pcm16le_waveform_points",
]
