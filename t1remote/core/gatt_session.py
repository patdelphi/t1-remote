"""程序说明：定义与具体 BLE 库无关的 GATT 音频会话状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class GattSessionError(RuntimeError):
    """GATT 会话调用顺序或状态不合法。"""


class GattSessionState(str, Enum):
    """音频 GATT 会话的稳定状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    NEGOTIATING = "negotiating"
    STREAMING = "streaming"
    DRAINING = "draining"
    ERROR = "error"


@dataclass(frozen=True)
class GattSessionSnapshot:
    """供 UI 或日志使用的不可变状态快照。"""

    state: GattSessionState
    generation: int
    accepted_notifications: int
    ignored_notifications: int
    last_error: str | None


class GattAudioSession:
    """管理 GATT 会话生命周期，不解释 T1 私有音频报文。"""

    def __init__(self) -> None:
        self._state = GattSessionState.DISCONNECTED
        self._generation = 0
        self._accepted_notifications = 0
        self._ignored_notifications = 0
        self._last_error: str | None = None

    @property
    def snapshot(self) -> GattSessionSnapshot:
        """返回当前会话快照。"""

        return GattSessionSnapshot(
            state=self._state,
            generation=self._generation,
            accepted_notifications=self._accepted_notifications,
            ignored_notifications=self._ignored_notifications,
            last_error=self._last_error,
        )

    def begin_connect(self) -> int:
        """开始一次新连接并返回本次会话 generation。"""

        if self._state not in (
            GattSessionState.DISCONNECTED,
            GattSessionState.ERROR,
        ):
            raise GattSessionError(f"无法从 {self._state.value} 开始连接")
        self._generation += 1
        self._state = GattSessionState.CONNECTING
        self._last_error = None
        self._accepted_notifications = 0
        self._ignored_notifications = 0
        return self._generation

    def on_connected(self, generation: int) -> None:
        """确认 BLE 连接成功，进入服务发现。"""

        self._advance(generation, GattSessionState.CONNECTING, GattSessionState.DISCOVERING)

    def on_services_discovered(self, generation: int) -> None:
        """确认目标服务和特征已发现，进入能力协商。"""

        self._advance(generation, GattSessionState.DISCOVERING, GattSessionState.NEGOTIATING)

    def on_negotiated(self, generation: int) -> None:
        """确认能力协商完成，进入通知接收状态。"""

        self._advance(generation, GattSessionState.NEGOTIATING, GattSessionState.STREAMING)

    def begin_drain(self, generation: int) -> None:
        """停止接收新音频并等待 PCM 队列排空。"""

        self._advance(generation, GattSessionState.STREAMING, GattSessionState.DRAINING)

    def finish_drain(self, generation: int) -> None:
        """确认 PCM 队列已排空，结束当前连接。"""

        self._advance(generation, GattSessionState.DRAINING, GattSessionState.DISCONNECTED)

    def disconnect(self, generation: int | None = None) -> None:
        """处理主动或被动断开；旧回调随后全部失效。"""

        if generation is not None and generation != self._generation:
            self._ignored_notifications += 1
            return
        self._state = GattSessionState.DISCONNECTED
        self._generation += 1

    def fail(self, generation: int, error: Exception | str) -> None:
        """记录当前会话错误并进入安全的 ERROR 状态。"""

        if generation != self._generation:
            self._ignored_notifications += 1
            return
        self._state = GattSessionState.ERROR
        self._last_error = str(error)

    def accept_notification(
        self,
        generation: int,
        payload: bytes,
        on_payload: Callable[[bytes], None],
    ) -> bool:
        """只接受当前 STREAMING 会话的通知，旧回调或错误状态直接丢弃。"""

        if (
            self._state != GattSessionState.STREAMING
            or generation != self._generation
            or not isinstance(payload, bytes)
        ):
            self._ignored_notifications += 1
            return False
        try:
            on_payload(payload)
        except Exception as error:
            self.fail(generation, error)
            raise
        self._accepted_notifications += 1
        return True

    def _advance(
        self,
        generation: int,
        expected: GattSessionState,
        target: GattSessionState,
    ) -> None:
        """校验 generation 和状态后推进会话。"""

        if generation != self._generation:
            raise GattSessionError("收到旧 GATT 会话的回调")
        if self._state != expected:
            raise GattSessionError(
                f"无法从 {self._state.value} 进入 {target.value}"
            )
        self._state = target


__all__ = [
    "GattAudioSession",
    "GattSessionError",
    "GattSessionSnapshot",
    "GattSessionState",
]
