"""程序说明：提供可选 sounddevice PCM 输出端点，用于音频链路离线验证。"""

from __future__ import annotations

from typing import Any, Callable

from t1remote.core.audio_buffer import PcmFormat
from t1remote.core.pcm_sink import PcmSinkError


class SoundDevicePcmSink:
    """通过可选 sounddevice 播放 PCM16LE，不创建虚拟麦克风设备。"""

    def __init__(
        self,
        pcm_format: PcmFormat,
        *,
        device: int | str | None = None,
        blocksize: int = 0,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        if pcm_format.sample_width_bytes != 2:
            raise ValueError("SoundDevicePcmSink 当前只支持 PCM16LE")
        if blocksize < 0:
            raise ValueError("blocksize 不能为负数")
        if stream_factory is None:
            try:
                import sounddevice
            except ImportError as error:
                raise PcmSinkError(
                    "未安装可选依赖 sounddevice，不能启动 PCM 输出"
                ) from error
            stream_factory = sounddevice.RawOutputStream

        self._format = pcm_format
        self._stream: Any | None = None
        try:
            self._stream = stream_factory(
                samplerate=pcm_format.sample_rate,
                channels=pcm_format.channels,
                dtype="int16",
                device=device,
                blocksize=blocksize,
            )
            self._stream.start()
        except Exception as error:
            self.close()
            raise PcmSinkError("无法启动 sounddevice PCM 输出") from error

    def write(self, chunk: bytes) -> None:
        """写入一个完整的交错 PCM16LE chunk。"""

        if not isinstance(chunk, bytes):
            raise TypeError("PCM chunk 必须是 bytes")
        if len(chunk) % self._format.bytes_per_sample_frame:
            raise ValueError("PCM chunk 不是完整 sample frame 的整数倍")
        if self._stream is None:
            raise PcmSinkError("sounddevice PCM 输出已经关闭")
        try:
            self._stream.write(chunk)
        except Exception as error:
            raise PcmSinkError("写入 sounddevice PCM 失败") from error

    def close(self) -> None:
        """停止并关闭 sounddevice 流，重复调用安全。"""

        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as error:
            raise PcmSinkError("关闭 sounddevice PCM 输出失败") from error

    def __enter__(self) -> "SoundDevicePcmSink":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = ["SoundDevicePcmSink"]
