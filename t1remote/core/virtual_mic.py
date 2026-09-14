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


def _to_stereo_pcm16le(pcm: bytes, channels: int) -> bytes:
    """把交错 PCM16LE 单声道扩成双声道；已是双声道则原样返回。"""

    if channels == 2:
        return pcm
    if channels != 1:
        raise ValueError("VirtualMicrophonePcmSink 只支持单声道或双声道源")
    if len(pcm) % 2:
        raise ValueError("单声道 PCM 数据不是完整 sample 的整数倍")
    output = bytearray()
    for index in range(0, len(pcm), 2):
        output.extend(pcm[index : index + 2])
        output.extend(pcm[index : index + 2])
    return bytes(output)


class VirtualMicrophonePcmSink:
    """将 PCM 写入指定的虚拟音频线播放端点。

    输出固定为立体声：VB-CABLE 这类虚拟音频线的播放端和录音端都是
    双声道设备，目标应用按立体声读取 CABLE Output。若只写单声道，
    左右声道会被错位解释成互相串扰的噪音。
    """

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
        self._output_format = PcmFormat(output_sample_rate, 2)
        self._sink = WasapiPcmSink(
            self._output_format,
            device=device,
            blocksize=blocksize,
            exclusive=exclusive,
            stream_factory=stream_factory,
            settings_factory=settings_factory,
        )

    def write(self, chunk: bytes) -> None:
        """重采样到立体声后写入虚拟音频线。"""

        converted = resample_pcm16le(
            chunk,
            input_sample_rate=self._source_format.sample_rate,
            output_sample_rate=self._output_format.sample_rate,
            channels=self._source_format.channels,
        )
        self._sink.write(_to_stereo_pcm16le(converted, self._source_format.channels))

    def close(self) -> None:
        """关闭虚拟音频线端点。"""

        self._sink.close()

    def __enter__(self) -> "VirtualMicrophonePcmSink":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = ["VirtualMicrophonePcmSink"]
