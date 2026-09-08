"""程序说明：把 GATT 传输、会话状态机和 PCM 管线连接成可注入控制器。

控制器只负责连接、发现和通知订阅，不猜测 T1 私有协商命令或音频帧头。
调用方必须显式确认协议协商后才能开始流式接收。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from t1remote.core.gatt_audio_pipeline import GattAudioPipelineSnapshot, GattAudioProcessor
from t1remote.core.gatt_session import GattSessionState
from t1remote.windows.gatt import (
    GattServiceInfo,
    T1_AUDIO_SERVICE_UUID,
    normalize_uuid,
)


class GattAudioControllerError(RuntimeError):
    """GATT 音频控制器的调用顺序、发现或传输错误。"""


NotificationHandler = Callable[[bytes], Awaitable[None] | None]


class GattAudioTransport(Protocol):
    """控制器需要的最小异步 GATT 传输接口。"""

    async def connect(self, timeout: float = 15.0) -> None: ...

    async def disconnect(self) -> None: ...

    async def discover_services(self) -> tuple[GattServiceInfo, ...]: ...

    async def subscribe(
        self,
        characteristic_uuid: str,
        callback: NotificationHandler,
    ) -> None: ...

    async def unsubscribe(self, characteristic_uuid: str) -> None: ...


@dataclass(frozen=True)
class GattAudioControllerSnapshot:
    """控制器状态、服务摘要和当前通知特征。"""

    pipeline: GattAudioPipelineSnapshot
    services: tuple[GattServiceInfo, ...]
    notification_characteristic: str | None


class GattAudioController:
    """编排 GATT 服务发现和已确认通知特征的 PCM 接收。"""

    def __init__(
        self,
        transport: GattAudioTransport,
        processor: GattAudioProcessor,
        *,
        audio_service_uuid: str = T1_AUDIO_SERVICE_UUID,
    ) -> None:
        self._transport = transport
        self._processor = processor
        self._session = processor.session
        self._audio_service_uuid = normalize_uuid(audio_service_uuid)
        self._generation: int | None = None
        self._services: tuple[GattServiceInfo, ...] = ()
        self._notification_characteristic: str | None = None

    @property
    def snapshot(self) -> GattAudioControllerSnapshot:
        """返回当前控制器快照。"""

        return GattAudioControllerSnapshot(
            pipeline=self._processor.snapshot,
            services=self._services,
            notification_characteristic=self._notification_characteristic,
        )

    async def connect(self, *, timeout: float = 15.0) -> None:
        """连接设备、发现服务并进入待协商状态。"""

        try:
            generation = self._session.begin_connect()
        except Exception as error:
            raise GattAudioControllerError(f"无法开始 GATT 连接：{error}") from error
        self._generation = generation
        self._services = ()
        self._notification_characteristic = None
        try:
            await self._transport.connect(timeout=timeout)
            self._session.on_connected(generation)
            services = await self._transport.discover_services()
            self._services = tuple(services)
            if not any(service.uuid == self._audio_service_uuid for service in self._services):
                raise GattAudioControllerError(
                    f"未发现目标音频服务：{self._audio_service_uuid}"
                )
            self._session.on_services_discovered(generation)
        except Exception as error:
            self._session.fail(generation, error)
            await self._disconnect_transport_safely()
            if isinstance(error, GattAudioControllerError):
                raise
            raise GattAudioControllerError(f"GATT 音频连接失败：{error}") from error

    async def start_stream(
        self,
        characteristic_uuid: str,
        *,
        negotiation_confirmed: bool = False,
    ) -> None:
        """订阅已确认的通知特征，并进入流式接收状态。

        私有能力协商尚未实现，因此默认拒绝启动；调用方必须使用真实协议
        证据显式传入 ``negotiation_confirmed=True``。
        """

        generation = self._require_generation(GattSessionState.NEGOTIATING)
        if not negotiation_confirmed:
            raise GattAudioControllerError(
                "T1 私有 GATT 能力协商尚未验证，不能启动音频流"
            )
        normalized_characteristic = normalize_uuid(characteristic_uuid)
        service = next(
            service
            for service in self._services
            if service.uuid == self._audio_service_uuid
        )
        characteristic = next(
            (
                item
                for item in service.characteristics
                if item.uuid == normalized_characteristic
            ),
            None,
        )
        if characteristic is None or "notify" not in {
            value.lower() for value in characteristic.properties
        }:
            raise GattAudioControllerError(
                f"目标特征不存在或不支持 notify：{normalized_characteristic}"
            )
        self._session.on_negotiated(generation)
        try:
            await self._transport.subscribe(
                normalized_characteristic,
                self._handle_notification,
            )
        except Exception as error:
            self._session.fail(generation, error)
            raise GattAudioControllerError(f"GATT 音频订阅失败：{error}") from error
        self._notification_characteristic = normalized_characteristic

    async def disconnect(self) -> None:
        """停止通知并使当前 generation 失效。"""

        characteristic = self._notification_characteristic
        self._notification_characteristic = None
        errors: list[Exception] = []
        try:
            if characteristic is not None:
                await self._transport.unsubscribe(characteristic)
        except Exception as error:
            errors.append(error)
        try:
            await self._transport.disconnect()
        except Exception as error:
            errors.append(error)
        finally:
            if self._generation is not None:
                self._session.disconnect(self._generation)
            self._generation = None
        if errors:
            raise GattAudioControllerError(f"GATT 音频断开失败：{errors[0]}") from errors[0]

    async def _handle_notification(self, payload: bytes) -> None:
        """把当前特征通知交给严格 generation 检查的处理器。"""

        generation = self._generation
        if generation is None:
            return
        try:
            self._processor.handle_notification(generation, payload)
        except Exception as error:
            self._session.fail(generation, error)

    def _require_generation(self, state: GattSessionState) -> int:
        generation = self._generation
        if generation is None or self._session.snapshot.state != state:
            actual = self._session.snapshot.state.value
            raise GattAudioControllerError(
                f"GATT 音频状态不允许此操作：需要 {state.value}，当前 {actual}"
            )
        return generation

    async def _disconnect_transport_safely(self) -> None:
        """错误路径尽力断开传输，保留原始连接错误。"""

        try:
            await self._transport.disconnect()
        except Exception:
            pass


__all__ = [
    "GattAudioController",
    "GattAudioControllerError",
    "GattAudioControllerSnapshot",
    "GattAudioTransport",
]
