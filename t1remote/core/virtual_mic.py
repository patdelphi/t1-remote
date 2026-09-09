"""程序说明：把 PCM 写入预先安装的 Windows 虚拟音频线输入端点。

该模块不创建或安装音频驱动。它把本程序作为播放端连接到 VB-CABLE、
VoiceMeeter 或其他兼容 WASAPI 的虚拟音频线；目标应用再选择该音频线的
录音端作为麦克风。
"""

from __future__ import annotations

from typing import Any, Callable

from t1remote.core.audio_buffer import PcmFormat
from t1remote.core.pcm_resampler import resample_pcm16le
from t1remote.core.pcm_sink import PcmSinkError
from t1remote.core.sounddevice_sink import WasapiPcmSink


class VirtualMicrophonePcmSink:
    """将 PCM 写入指定的虚拟音频线播放端点。"""

    def __init__(
        self,
        pcm_format: PcmFormat,
        *,
        device: int | str | None,
        output_sample_rate: int = 48_000,
        blocksize: int = 0,
        exclusive: bool = False,
        stream_factory: Callable[..., Any] | None = None,
        settings_factory: Callable[..., Any] | None = None,
    ) -> None:
        if device is None or (isinstance(device, str) and not device.strip()):
            raise PcmSinkError(
                "必须明确指定已安装的虚拟音频线输入端点，不能默认写入扬声器"
            )
        if pcm_format.sample_width_bytes != 2:
            raise ValueError("VirtualMicrophonePcmSink 当前只支持 PCM16LE")
        self._source_format = pcm_format
        self._output_format = PcmFormat(output_sample_rate, pcm_format.channels)
        self._sink = WasapiPcmSink(
            self._output_format,
            device=device,
            blocksize=blocksize,
            exclusive=exclusive,
            stream_factory=stream_factory,
            settings_factory=settings_factory,
        )

    def write(self, chunk: bytes) -> None:
        """重采样后写入虚拟音频线。"""

        converted = resample_pcm16le(
            chunk,
            input_sample_rate=self._source_format.sample_rate,
            output_sample_rate=self._output_format.sample_rate,
            channels=self._source_format.channels,
        )
        self._sink.write(converted)

    def close(self) -> None:
        """关闭虚拟音频线端点。"""

        self._sink.close()

    def __enter__(self) -> "VirtualMicrophonePcmSink":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = ["VirtualMicrophonePcmSink"]
