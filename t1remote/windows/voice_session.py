"""程序说明：在独立线程中管理 T1 真实语音测试的启动、停止和状态回调。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from tools.t1_voice_test import VoiceTestResult, VoiceTestRunner


class VoiceSessionError(RuntimeError):
    """语音会话状态或线程生命周期错误。"""


@dataclass(frozen=True)
class VoiceSessionStatus:
    """供 Tk 主线程显示的脱敏语音会话状态。"""

    state: str
    message: str
    result: VoiceTestResult | Any | None = None
    error: BaseException | None = None
    waveform_points: tuple[float, ...] = ()


RunnerFactory = Callable[..., VoiceTestRunner]
StatusCallback = Callable[[VoiceSessionStatus], None]


class VoiceSessionController:
    """把异步 VoiceTestRunner 包装为可由 GUI 控制的后台会话。"""

    _ACTIVE_STATES = frozenset({"starting", "running", "stopping"})

    def __init__(
        self,
        *,
        runner_factory: RunnerFactory = VoiceTestRunner,
        on_status: StatusCallback | None = None,
    ) -> None:
        self._runner_factory = runner_factory
        self._on_status = on_status
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._waveform_points: tuple[float, ...] = ()
        self._last_waveform_notification = 0.0
        self._status = VoiceSessionStatus("stopped", "语音会话未启动")

    def status(self) -> VoiceSessionStatus:
        """返回线程安全的当前状态。"""

        with self._lock:
            return self._status

    def start(
        self,
        address: str,
        *,
        duration_seconds: float = 10.0,
        device_index: int | None = None,
        negotiation_timeout: float = 8.0,
        queue_chunks: int = 64,
        blocksize: int = 0,
        keepalive_interval: float = 10.0,
    ) -> None:
        """启动新的后台语音会话。

        ``duration_seconds=0`` 表示持续收音：麦克风保持打开，直到 `stop()`
        被调用；正数则按时长自动结束。

        ``keepalive_interval`` 定期重发 MIC_OPEN 防止 T1 VAD 超时（秒），
        0 表示关闭；T1 在无人声约 15 秒后停止音频传输。
        """

        if not address.strip():
            raise ValueError("BLE 地址或设备标识不能为空")
        with self._lock:
            if self._status.state in self._ACTIVE_STATES:
                raise VoiceSessionError("已有一个语音会话正在运行")
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._keepalive_interval = keepalive_interval
            self._waveform_points = ()
            self._last_waveform_notification = 0.0
            self._status = VoiceSessionStatus("starting", "正在启动语音会话")
            thread = threading.Thread(
                target=self._run,
                args=(
                    address,
                    duration_seconds,
                    device_index,
                    negotiation_timeout,
                    queue_chunks,
                    blocksize,
                    stop_event,
                    keepalive_interval,
                ),
                name="t1-voice-session",
                daemon=True,
            )
            self._thread = thread
        self._notify()
        thread.start()

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        """请求关闭会话，并按需等待 BLE 与音频端点完成清理。"""

        if timeout < 0:
            raise ValueError("timeout 不能为负数")
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            if self._status.state in self._ACTIVE_STATES:
                self._status = VoiceSessionStatus("stopping", "正在停止语音会话")
        if stop_event is not None:
            stop_event.set()
        self._notify()
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise VoiceSessionError("语音会话未能在限定时间内停止")

    def _run(
        self,
        address: str,
        duration_seconds: float,
        device_index: int | None,
        negotiation_timeout: float,
        queue_chunks: int,
        blocksize: int,
        stop_event: threading.Event,
        keepalive_interval: float = 10.0,
    ) -> None:
        """在线程内创建事件循环，避免阻塞 Tk 主循环。

        `duration_seconds=0` 时 VoiceTestRunner 会一直收音，只由
        `stop_event` 结束本次会话。

        ``keepalive_interval`` 给 VoiceTestRunner 用以定期重发
        MIC_OPEN 防 VAD 超时。
        """

        self._set_status("running", "正在连接 T1 GATT")
        try:
            runner = self._runner_factory(
                address,
                duration_seconds=duration_seconds,
                device_index=device_index,
                negotiation_timeout=negotiation_timeout,
                queue_chunks=queue_chunks,
                blocksize=blocksize,
                stop_event=stop_event,
                keepalive_interval=keepalive_interval,
                waveform_callback=self._set_waveform,
                progress=lambda message: self._set_status("running", message),
            )
            result = asyncio.run(runner.run())
        except BaseException as error:
            self._set_status("error", f"语音会话失败：{error}", error=error)
        else:
            self._set_status("stopped", "语音会话已停止", result=result)
        finally:
            with self._lock:
                self._thread = None
                self._stop_event = None

    def _set_status(
        self,
        state: str,
        message: str,
        *,
        result: VoiceTestResult | Any | None = None,
        error: BaseException | None = None,
    ) -> None:
        """更新状态并安全通知调用方。"""

        with self._lock:
            self._status = VoiceSessionStatus(
                state,
                message,
                result,
                error,
                self._waveform_points,
            )
        self._notify()

    def _set_waveform(self, points: tuple[float, ...]) -> None:
        """限频推送最近 PCM 波形，避免高频刷新 Tk。"""

        now = time.monotonic()
        with self._lock:
            self._waveform_points = points
            if now - self._last_waveform_notification < 0.05:
                return
            self._last_waveform_notification = now
            current = self._status
            self._status = VoiceSessionStatus(
                current.state,
                current.message,
                current.result,
                current.error,
                points,
            )
        self._notify()

    def _notify(self) -> None:
        """回调异常不能反向杀掉语音工作线程。"""

        if self._on_status is None:
            return
        try:
            self._on_status(self.status())
        except Exception:
            return


__all__ = ["VoiceSessionController", "VoiceSessionError", "VoiceSessionStatus"]
