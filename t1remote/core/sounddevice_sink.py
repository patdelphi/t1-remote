"""程序说明：提供可选 sounddevice PCM 输出端点，用于音频链路离线验证。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

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
        extra_settings: Any | None = None,
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
                extra_settings=extra_settings,
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


class WasapiPcmSink(SoundDevicePcmSink):
    """通过 sounddevice 的 WASAPI host 设置输出 PCM16LE。

    该端点只负责播放到现有输出设备，不创建虚拟麦克风，也不宣称已经
    完成 T1 的真实语音协议接入。
    """

    def __init__(
        self,
        pcm_format: PcmFormat,
        *,
        device: int | str | None = None,
        blocksize: int = 0,
        exclusive: bool = False,
        stream_factory: Callable[..., Any] | None = None,
        settings_factory: Callable[..., Any] | None = None,
    ) -> None:
        if settings_factory is None:
            try:
                import sounddevice
                settings_factory = sounddevice.WasapiSettings
            except (AttributeError, ImportError) as error:
                raise PcmSinkError(
                    "未安装可选依赖 sounddevice，不能启动 WASAPI PCM 输出"
                ) from error
        try:
            extra_settings = settings_factory(exclusive=exclusive)
        except Exception as error:
            raise PcmSinkError("无法创建 WASAPI 音频设置") from error
        super().__init__(
            pcm_format,
            device=device,
            blocksize=blocksize,
            stream_factory=stream_factory,
            extra_settings=extra_settings,
        )


@dataclass(frozen=True)
class AudioOutputDevice:
    """一个可输出音频设备的脱敏摘要。"""

    index: int
    name: str
    max_output_channels: int
    default_samplerate: float


def summarize_output_devices(devices: Iterable[Any]) -> tuple[AudioOutputDevice, ...]:
    """从 sounddevice 设备记录中筛选可输出端点。"""

    summaries: list[AudioOutputDevice] = []
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        try:
            channels = int(device.get("max_output_channels", 0))
            if channels <= 0:
                continue
            summaries.append(
                AudioOutputDevice(
                    index=index,
                    name=str(device.get("name", f"output-{index}")),
                    max_output_channels=channels,
                    default_samplerate=float(device.get("default_samplerate", 0)),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(summaries)


def enumerate_output_devices(
    *,
    query_devices: Callable[[], Iterable[Any]] | None = None,
) -> tuple[AudioOutputDevice, ...]:
    """只读枚举 sounddevice 输出端点；依赖按需加载。"""

    if query_devices is None:
        try:
            import sounddevice
        except ImportError as error:
            raise PcmSinkError(
                "未安装可选依赖 sounddevice，不能枚举音频设备"
            ) from error
        query_devices = sounddevice.query_devices
    try:
        return summarize_output_devices(query_devices())
    except Exception as error:
        raise PcmSinkError("枚举 sounddevice 音频设备失败") from error


__all__ = [
    "AudioOutputDevice",
    "SoundDevicePcmSink",
    "WasapiPcmSink",
    "enumerate_output_devices",
    "summarize_output_devices",
]
