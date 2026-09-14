"""程序说明：连接真实 T1，接收 ATVV v0.4 语音并写入 VB-CABLE。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import signal
import threading
from typing import Any, Callable, Iterable

from t1remote.core.audio_buffer import PcmFrameQueue, PcmFormat
from t1remote.core.atvv_protocol import ATVV_V04_SAMPLE_RATE, AtvvCapabilityResponse
from t1remote.core.pcm_output import PcmSink, PcmSinkWorker, PcmSinkWorkerSnapshot
from t1remote.core.pcm_sink import PcmSinkError, PcmWaveformSink, RecordingPcmSink
from t1remote.core.sounddevice_sink import (
    AudioOutputDevice,
    enumerate_output_devices,
)
from t1remote.core.virtual_mic import VirtualMicrophonePcmSink
from t1remote.windows.atvv_audio import (
    AtvvV04GattAudioController,
    AtvvV04GattAudioControllerError,
)
from t1remote.windows.gatt import BleakGattAdapter, GattTransportError


class VoiceTestError(RuntimeError):
    """真实语音测试参数或虚拟音频端点错误。"""


ControllerFactory = Callable[[Any, PcmFrameQueue], Any]
WorkerFactory = Callable[[PcmFrameQueue, PcmSink], Any]
SinkFactory = Callable[..., PcmSink]
TransportFactory = Callable[[str], Any]
WaveformCallback = Callable[[tuple[float, ...]], None]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDING_DIR = PROJECT_ROOT / "captures" / "voice-replay"


@dataclass(frozen=True)
class VoiceTestResult:
    """一次真实语音测试的脱敏结果。"""

    output_device: AudioOutputDevice
    capabilities: AtvvCapabilityResponse | None
    worker: PcmSinkWorkerSnapshot | Any
    recording_path: Path | None = None


def select_virtual_cable_output(
    devices: Iterable[AudioOutputDevice],
) -> AudioOutputDevice:
    """选择 WASAPI 下的 VB-CABLE 单声道兼容播放端点。"""

    candidates = [
        device
        for device in devices
        if device.name.strip().casefold() == "cable input (vb-audio virtual cable)"
        and (
            (device.hostapi_name or "").strip().casefold() == "windows wasapi"
            or device.hostapi_index == 2
        )
        and device.max_output_channels >= 1
    ]
    if not candidates:
        raise VoiceTestError(
            "未找到 Windows WASAPI 的 CABLE Input (VB-Audio Virtual Cable) 输出端点"
        )
    return candidates[0]


class VoiceTestRunner:
    """把 ATVV 控制器、PCM 队列和虚拟麦克风输出串成一次测试。"""

    def __init__(
        self,
        address: str,
        *,
        duration_seconds: float = 10.0,
        device_index: int | None = None,
        negotiation_timeout: float = 8.0,
        queue_chunks: int = 64,
        blocksize: int = 0,
        stop_event: threading.Event | None = None,
        keepalive_interval: float = 10.0,
        controller_factory: ControllerFactory = AtvvV04GattAudioController,
        worker_factory: WorkerFactory = PcmSinkWorker,
        sink_factory: SinkFactory = VirtualMicrophonePcmSink,
        transport_factory: TransportFactory = BleakGattAdapter,
        output_devices_factory: Callable[[], tuple[AudioOutputDevice, ...]] = enumerate_output_devices,
        recording_dir: Path | None = DEFAULT_RECORDING_DIR,
        waveform_callback: WaveformCallback | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if not address.strip():
            raise ValueError("BLE 地址或设备标识不能为空")
        if duration_seconds < 0:
            raise ValueError("duration_seconds 不能为负数")
        if negotiation_timeout <= 0:
            raise ValueError("negotiation_timeout 必须大于 0")
        if queue_chunks < 1:
            raise ValueError("queue_chunks 必须大于 0")
        if blocksize < 0:
            raise ValueError("blocksize 不能为负数")
        self.address = address
        self.duration_seconds = duration_seconds
        self.device_index = device_index
        self.negotiation_timeout = negotiation_timeout
        self.queue_chunks = queue_chunks
        self.blocksize = blocksize
        self.stop_event = stop_event
        self.keepalive_interval = keepalive_interval
        self._controller_factory = controller_factory
        self._worker_factory = worker_factory
        self._sink_factory = sink_factory
        self._transport_factory = transport_factory
        self._output_devices_factory = output_devices_factory
        self._recording_dir = Path(recording_dir) if recording_dir is not None else None
        self._waveform_callback = waveform_callback
        self._progress = progress
        self.controller: Any | None = None
        self.worker: Any | None = None

    async def _keep_alive(self, controller: Any) -> None:
        """定期重发 MIC_OPEN 防止 T1 VAD 超时静音。

        T1 在无人声约 15 秒后自动关闭麦克风传输（VAD 省电）。理论上
        MIC_OPEN 只需要一次，但实测重发可以重置 T1 的内部计时器。
        """

        if self.keepalive_interval <= 0:
            return
        try:
            while True:
                await asyncio.sleep(self.keepalive_interval)
                if not hasattr(controller, "open_microphone"):
                    break
                await controller.open_microphone()
                self._report("MIC_OPEN keep-alive")
        except asyncio.CancelledError:
            pass

    async def run(self) -> VoiceTestResult:
        """连接、协商、开麦、输出指定时长，并按安全顺序清理。"""

        queue = PcmFrameQueue(max_chunks=self.queue_chunks)
        transport = self._transport_factory(self.address)
        controller = self._controller_factory(transport, queue)
        self.controller = controller
        self._keep_alive_task: asyncio.Task[None] | None = None
        worker: Any | None = None
        sink: PcmSink | None = None
        recording_path: Path | None = None
        output_device: AudioOutputDevice | None = None
        capabilities: AtvvCapabilityResponse | None = None
        primary_error: BaseException | None = None
        try:
            self._report("连接 T1 GATT")
            await controller.connect()
            self._report("完成 ATVV 能力协商")
            capabilities = await controller.negotiate(timeout=self.negotiation_timeout)
            # 先完成 BLE 协商，再初始化 PortAudio；Windows 上反向启动会互相阻塞。
            output_device = self._resolve_output_device()
            base_sink = self._sink_factory(
                PcmFormat(ATVV_V04_SAMPLE_RATE, 1),
                device=output_device.index,
                output_sample_rate=48_000,
                blocksize=self.blocksize,
                exclusive=False,
            )
            if self._recording_dir is not None:
                try:
                    recording_path = self._next_recording_path()
                    sink = RecordingPcmSink(
                        base_sink,
                        recording_path,
                        PcmFormat(ATVV_V04_SAMPLE_RATE, 1),
                        overwrite=True,
                    )
                except BaseException:
                    try:
                        base_sink.close()
                    except Exception:
                        pass
                    raise
            else:
                sink = base_sink
            if self._waveform_callback is not None:
                sink = PcmWaveformSink(sink, self._waveform_callback)
            try:
                worker = self._worker_factory(queue, sink)
            except BaseException:
                try:
                    sink.close()
                finally:
                    sink = None
                raise
            self.worker = worker
            worker.start()
            self._report("发送 MIC_OPEN，开始接收语音")
            await controller.open_microphone()
            if self.keepalive_interval > 0:
                self._keep_alive_task = asyncio.create_task(self._keep_alive(controller))
            await self._wait_for_completion()
            self._report("测试时长结束")
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            if controller is not None:
                if self._keep_alive_task is not None:
                    self._keep_alive_task.cancel()
                    try:
                        await self._keep_alive_task
                    except asyncio.CancelledError:
                        pass
                try:
                    if controller.snapshot.microphone_open:
                        await controller.close_microphone()
                except BaseException as error:
                    cleanup_errors.append(error)
                try:
                    await controller.disconnect()
                except BaseException as error:
                    cleanup_errors.append(error)
            if worker is not None:
                try:
                    worker.stop(discard=False)
                except BaseException as error:
                    cleanup_errors.append(error)
            elif sink is not None:
                try:
                    sink.close()
                except BaseException as error:
                    cleanup_errors.append(error)
            if primary_error is None and cleanup_errors:
                raise VoiceTestError(f"语音测试清理失败：{cleanup_errors[0]}") from cleanup_errors[0]

        return VoiceTestResult(
            output_device=output_device,
            capabilities=capabilities,
            worker=worker.snapshot,
            recording_path=recording_path,
        )

    def _next_recording_path(self) -> Path:
        """返回固定的最近一次录音路径，新的测试会覆盖旧文件。"""

        if self._recording_dir is None:
            raise VoiceTestError("语音录音目录未启用")
        return self._recording_dir / "t1-voice-latest.wav"

    async def _wait_for_completion(self) -> None:
        """按时长等待，或响应 GUI 的停止事件。

        传入 ``stop_event`` 时 ``duration_seconds == 0`` 表示持续收音（无限等待），
        直到外部设置停止事件；没有停止事件时 0 秒仍按“立即结束”处理。
        """

        if self.stop_event is None:
            if self.duration_seconds:
                await asyncio.sleep(self.duration_seconds)
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.duration_seconds if self.duration_seconds else None
        while not self.stop_event.is_set():
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                await asyncio.sleep(min(0.1, remaining))
            else:
                await asyncio.sleep(0.1)

    def _report(self, message: str) -> None:
        """发送可选的运行进度，不影响库调用方。"""

        if self._progress is not None:
            self._progress(message)

    def _resolve_output_device(self) -> AudioOutputDevice:
        """解析显式设备编号，或自动选择 WASAPI CABLE Input。"""

        devices = self._output_devices_factory()
        if self.device_index is not None:
            for device in devices:
                if device.index == self.device_index:
                    if device.max_output_channels < 1:
                        raise VoiceTestError(f"设备 {self.device_index} 不支持音频输出")
                    return device
            raise VoiceTestError(f"未找到输出设备编号：{self.device_index}")
        return select_virtual_cable_output(devices)


def _format_capabilities(capabilities: AtvvCapabilityResponse | None) -> str:
    """把能力响应转换成适合终端显示的摘要。"""

    if capabilities is None:
        return "未知"
    return (
        f"v{capabilities.version[0]}.{capabilities.version[1]}, "
        f"codec_flags=0x{capabilities.codec_flags:04X}, "
        f"frame_size={capabilities.frame_size}, "
        f"payload_size={capabilities.characteristic_payload_size}"
    )


def _install_interrupt_stop_event() -> threading.Event:
    """把 Ctrl+C 转成停止事件，让麦克风和 GATT 按顺序收尾。

    ``--duration 0`` 的持续收音只能靠 Ctrl+C 结束；如果直接抛出
    ``KeyboardInterrupt``，会跳过 close_microphone 和 disconnect。
    非主线程或不支持信号的平台退回默认行为。
    """

    stop_event = threading.Event()
    try:
        signal.signal(signal.SIGINT, lambda *_args: stop_event.set())
    except (ValueError, OSError):
        pass
    return stop_event


def main() -> int:
    """执行真实 T1 语音录入测试。"""

    parser = argparse.ArgumentParser(
        description="连接 T1，接收 ATVV v0.4 语音并写入 VB-CABLE"
    )
    parser.add_argument("address", help="Bleak 支持的 BLE 地址或设备标识")
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="开麦时长（秒）；0 表示持续收音直到 Ctrl+C，默认 10 秒",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=None,
        help="显式指定 sounddevice 输出编号；默认自动选择 WASAPI CABLE Input",
    )
    parser.add_argument(
        "--negotiation-timeout",
        type=float,
        default=8.0,
        help="ATVV 能力协商超时秒数，默认 8 秒",
    )
    parser.add_argument("--queue-chunks", type=int, default=64)
    parser.add_argument("--blocksize", type=int, default=0)
    args = parser.parse_args()
    stop_event = _install_interrupt_stop_event()
    if args.duration == 0:
        print("持续收音：按 Ctrl+C 结束并正常收尾", flush=True)
    try:
        runner = VoiceTestRunner(
            args.address,
            duration_seconds=args.duration,
            stop_event=stop_event,
            device_index=args.device_index,
            negotiation_timeout=args.negotiation_timeout,
            queue_chunks=args.queue_chunks,
            blocksize=args.blocksize,
            progress=lambda message: print(message, flush=True),
        )
        result = asyncio.run(runner.run())
        worker = result.worker
        print(f"语音测试完成：输出端点={result.output_device.name} [{result.output_device.index}]")
        print(f"ATVV 能力：{_format_capabilities(result.capabilities)}")
        print(
            "PCM 输出："
            f" running={worker.running}"
            f" chunks={worker.chunks_written}"
            f" bytes={worker.bytes_written}"
            f" error={worker.error or 'none'}"
        )
        if result.recording_path is not None:
            print(f"语音录音：{result.recording_path}")
        return 0
    except KeyboardInterrupt:
        print("语音测试已中断")
        return 130
    except (
        AtvvV04GattAudioControllerError,
        GattTransportError,
        PcmSinkError,
        VoiceTestError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"语音测试失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
