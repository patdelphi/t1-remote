"""程序说明：提供可选 sounddevice PCM 输出端点，用于音频链路离线验证。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

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
    hostapi_index: int | None = None
    hostapi_name: str | None = None


@dataclass(frozen=True)
class AudioInputDevice:
    """一个可被目标应用选择为麦克风的脱敏输入端点摘要。"""

    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    hostapi_index: int | None = None
    hostapi_name: str | None = None


def _hostapi_fields(
    device: Mapping[str, Any],
    hostapi_names: Mapping[int, str] | None,
) -> tuple[int | None, str | None]:
    """读取 sounddevice 设备的 Host API 编号和名称。"""

    try:
        index = int(device["hostapi"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return index, (hostapi_names or {}).get(index)


def _summarize_hostapis(
    query_hostapis: Callable[[], Iterable[Any]] | None,
) -> dict[int, str]:
    """把 Host API 列表转换为稳定的编号到名称映射。"""

    if query_hostapis is None:
        return {}
    try:
        values: dict[int, str] = {}
        for index, hostapi in enumerate(query_hostapis()):
            if isinstance(hostapi, dict) and hostapi.get("name"):
                values[index] = str(hostapi["name"])
        return values
    except Exception:
        return {}


def summarize_output_devices(
    devices: Iterable[Any],
    *,
    hostapi_names: Mapping[int, str] | None = None,
) -> tuple[AudioOutputDevice, ...]:
    """从 sounddevice 设备记录中筛选可输出端点。"""

    summaries: list[AudioOutputDevice] = []
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        try:
            channels = int(device.get("max_output_channels", 0))
            if channels <= 0:
                continue
            hostapi_index, hostapi_name = _hostapi_fields(device, hostapi_names)
            summaries.append(
                AudioOutputDevice(
                    index=index,
                    name=str(device.get("name", f"output-{index}")),
                    max_output_channels=channels,
                    default_samplerate=float(device.get("default_samplerate", 0)),
                    hostapi_index=hostapi_index,
                    hostapi_name=hostapi_name,
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(summaries)


def enumerate_output_devices(
    *,
    query_devices: Callable[[], Iterable[Any]] | None = None,
    query_hostapis: Callable[[], Iterable[Any]] | None = None,
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
        if query_hostapis is None:
            query_hostapis = sounddevice.query_hostapis
    hostapi_names = _summarize_hostapis(query_hostapis)
    try:
        return summarize_output_devices(query_devices(), hostapi_names=hostapi_names)
    except Exception as error:
        raise PcmSinkError("枚举 sounddevice 音频设备失败") from error


def summarize_input_devices(
    devices: Iterable[Any],
    *,
    hostapi_names: Mapping[int, str] | None = None,
) -> tuple[AudioInputDevice, ...]:
    """从 sounddevice 设备记录中筛选可输入端点。"""

    summaries: list[AudioInputDevice] = []
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        try:
            channels = int(device.get("max_input_channels", 0))
            if channels <= 0:
                continue
            hostapi_index, hostapi_name = _hostapi_fields(device, hostapi_names)
            summaries.append(
                AudioInputDevice(
                    index=index,
                    name=str(device.get("name", f"input-{index}")),
                    max_input_channels=channels,
                    default_samplerate=float(device.get("default_samplerate", 0)),
                    hostapi_index=hostapi_index,
                    hostapi_name=hostapi_name,
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(summaries)


def enumerate_input_devices(
    *,
    query_devices: Callable[[], Iterable[Any]] | None = None,
    query_hostapis: Callable[[], Iterable[Any]] | None = None,
) -> tuple[AudioInputDevice, ...]:
    """只读枚举 sounddevice 输入端点；依赖按需加载。"""

    if query_devices is None:
        try:
            import sounddevice
        except ImportError as error:
            raise PcmSinkError(
                "未安装可选依赖 sounddevice，不能枚举音频设备"
            ) from error
        query_devices = sounddevice.query_devices
        if query_hostapis is None:
            query_hostapis = sounddevice.query_hostapis
    hostapi_names = _summarize_hostapis(query_hostapis)
    try:
        return summarize_input_devices(query_devices(), hostapi_names=hostapi_names)
    except Exception as error:
        raise PcmSinkError("枚举 sounddevice 音频设备失败") from error


__all__ = [
    "AudioInputDevice",
    "AudioOutputDevice",
    "SoundDevicePcmSink",
    "WasapiPcmSink",
    "enumerate_input_devices",
    "enumerate_output_devices",
    "summarize_input_devices",
    "summarize_output_devices",
]
