"""程序说明：把公开 ATVV v0.4 协议接入 Bleak 风格的 GATT 传输。

控制器只在显式选择 v0.4 配置后发送能力查询和开麦命令；同 UUID 的其他
协议版本不会被静默当作 v0.4 处理。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.atvv_audio import AtvvV04AudioProcessor
from t1remote.core.atvv_protocol import (
    ATVV_AUDIO_CHARACTERISTIC_UUID,
    ATVV_AUDIO_SERVICE_UUID,
    ATVV_CODEC_ADPCM_8KHZ,
    ATVV_CONTROL_CHARACTERISTIC_UUID,
    ATVV_TX_CHARACTERISTIC_UUID,
    AtvvCapabilityResponse,
    AtvvControlSignal,
    build_get_caps_v04,
    build_mic_close,
    build_mic_open,
    parse_capability_response,
)
from t1remote.core.gatt_session import GattAudioSession, GattSessionSnapshot, GattSessionState
from t1remote.windows.gatt import GattServiceInfo, normalize_uuid


class AtvvV04GattAudioControllerError(RuntimeError):
    """ATVV v0.4 GATT 控制器错误。"""


NotificationHandler = Callable[[bytes], Awaitable[None] | None]


class AtvvV04GattTransport(Protocol):
    """ATVV 控制器需要的最小 GATT 传输接口。"""

    async def connect(self, timeout: float = 15.0) -> None: ...

    async def disconnect(self) -> None: ...

    async def discover_services(self) -> tuple[GattServiceInfo, ...]: ...

    async def subscribe(self, characteristic_uuid: str, callback: NotificationHandler) -> None: ...

    async def unsubscribe(self, characteristic_uuid: str) -> None: ...

    async def write(self, characteristic_uuid: str, data: bytes, *, response: bool) -> None: ...


@dataclass(frozen=True)
class AtvvV04GattAudioControllerSnapshot:
    """ATVV 控制器状态快照。"""

    session: GattSessionSnapshot
    services: tuple[GattServiceInfo, ...]
    capabilities: AtvvCapabilityResponse | None
    microphone_open: bool


class AtvvV04GattAudioController:
    """连接、协商并接收 ATVV v0.4 音频。"""

    def __init__(self, transport: AtvvV04GattTransport, queue: PcmFrameQueue) -> None:
        self._transport = transport
        self._session = GattAudioSession()
        self._processor = AtvvV04AudioProcessor(self._session, queue)
        self._generation: int | None = None
        self._services: tuple[GattServiceInfo, ...] = ()
        self._capabilities: AtvvCapabilityResponse | None = None
        self._microphone_open = False
        self._caps_future: asyncio.Future[AtvvCapabilityResponse] | None = None

    @property
    def snapshot(self) -> AtvvV04GattAudioControllerSnapshot:
        """返回当前控制器快照。"""

        return AtvvV04GattAudioControllerSnapshot(
            session=self._session.snapshot,
            services=self._services,
            capabilities=self._capabilities,
            microphone_open=self._microphone_open,
        )

    async def connect(self, *, timeout: float = 15.0) -> None:
        """连接设备、发现 ATVV 特征并订阅通知。"""

        try:
            generation = self._session.begin_connect()
        except Exception as error:
            raise AtvvV04GattAudioControllerError(f"无法开始 ATVV 连接：{error}") from error
        self._generation = generation
        try:
            await self._transport.connect(timeout=timeout)
            self._session.on_connected(generation)
            self._services = tuple(await self._transport.discover_services())
            service = self._find_service(ATVV_AUDIO_SERVICE_UUID)
            self._require_characteristic(service, ATVV_TX_CHARACTERISTIC_UUID, "write")
            self._require_characteristic(service, ATVV_AUDIO_CHARACTERISTIC_UUID, "notify")
            self._require_characteristic(service, ATVV_CONTROL_CHARACTERISTIC_UUID, "notify")
            await self._transport.subscribe(
                ATVV_AUDIO_CHARACTERISTIC_UUID,
                self._handle_audio_notification,
            )
            await self._transport.subscribe(
                ATVV_CONTROL_CHARACTERISTIC_UUID,
                self._handle_control_notification,
            )
            self._session.on_services_discovered(generation)
        except Exception as error:
            self._session.fail(generation, error)
            await self._disconnect_transport_safely()
            if isinstance(error, AtvvV04GattAudioControllerError):
                raise
            raise AtvvV04GattAudioControllerError(f"ATVV 连接失败：{error}") from error

    async def negotiate(self, *, timeout: float = 8.0) -> AtvvCapabilityResponse:
        """发送 v0.4 能力查询，等待控制特征返回能力响应。"""

        generation = self._require_state(GattSessionState.NEGOTIATING)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AtvvCapabilityResponse] = loop.create_future()
        self._caps_future = future
        try:
            await self._transport.write(
                ATVV_TX_CHARACTERISTIC_UUID,
                build_get_caps_v04(),
                response=False,
            )
            capabilities = await asyncio.wait_for(future, timeout=timeout)
            if not capabilities.codec_flags & ATVV_CODEC_ADPCM_8KHZ:
                raise AtvvV04GattAudioControllerError(
                    "T1 未声明 ATVV v0.4 ADPCM 8 kHz 能力，拒绝按 v0.4 解码"
                )
            self._capabilities = capabilities
            self._session.on_negotiated(generation)
            return capabilities
        except asyncio.TimeoutError as error:
            self._session.fail(generation, "ATVV 能力响应超时")
            raise AtvvV04GattAudioControllerError("ATVV 能力响应超时") from error
        except Exception as error:
            self._session.fail(generation, error)
            if isinstance(error, AtvvV04GattAudioControllerError):
                raise
            raise AtvvV04GattAudioControllerError(f"ATVV 能力协商失败：{error}") from error
        finally:
            self._caps_future = None

    async def open_microphone(self) -> None:
        """发送 v0.4 MIC_OPEN，允许设备开始发送音频。"""

        self._require_state(GattSessionState.STREAMING)
        if self._capabilities is None:
            raise AtvvV04GattAudioControllerError("尚未完成 ATVV 能力协商")
        try:
            await self._transport.write(
                ATVV_TX_CHARACTERISTIC_UUID,
                build_mic_open(ATVV_CODEC_ADPCM_8KHZ),
                response=False,
            )
            self._microphone_open = True
        except Exception as error:
            raise AtvvV04GattAudioControllerError(f"ATVV MIC_OPEN 失败：{error}") from error

    async def close_microphone(self) -> None:
        """发送 v0.4 MIC_CLOSE。"""

        self._require_state(GattSessionState.STREAMING)
        if not self._microphone_open:
            return
        try:
            await self._transport.write(
                ATVV_TX_CHARACTERISTIC_UUID,
                build_mic_close(),
                response=False,
            )
            self._microphone_open = False
        except Exception as error:
            raise AtvvV04GattAudioControllerError(f"ATVV MIC_CLOSE 失败：{error}") from error

    async def disconnect(self) -> None:
        """取消通知订阅并使旧音频回调失效。"""

        errors: list[Exception] = []
        try:
            await self._transport.unsubscribe(ATVV_AUDIO_CHARACTERISTIC_UUID)
            await self._transport.unsubscribe(ATVV_CONTROL_CHARACTERISTIC_UUID)
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
            self._microphone_open = False
            self._caps_future = None
            self._processor.reset()
        if errors:
            raise AtvvV04GattAudioControllerError(f"ATVV 断开失败：{errors[0]}") from errors[0]

    async def _handle_audio_notification(self, payload: bytes) -> None:
        generation = self._generation
        if generation is None:
            return
        try:
            self._processor.handle_notification(generation, payload)
        except Exception as error:
            self._session.fail(generation, error)

    async def _handle_control_notification(self, payload: bytes) -> None:
        if not payload:
            return
        if payload[0] == AtvvControlSignal.GET_CAPS_RESPONSE:
            try:
                capabilities = parse_capability_response(payload)
            except Exception as error:
                if self._caps_future is not None and not self._caps_future.done():
                    self._caps_future.set_exception(error)
                return
            self._capabilities = capabilities
            if self._caps_future is not None and not self._caps_future.done():
                self._caps_future.set_result(capabilities)
        elif payload[0] == AtvvControlSignal.AUDIO_STOP:
            self._microphone_open = False

    def _find_service(self, service_uuid: str) -> GattServiceInfo:
        normalized = normalize_uuid(service_uuid)
        for service in self._services:
            if normalize_uuid(service.uuid) == normalized:
                return service
        raise AtvvV04GattAudioControllerError(f"未发现 ATVV 服务：{normalized}")

    @staticmethod
    def _require_characteristic(
        service: GattServiceInfo,
        characteristic_uuid: str,
        required_property: str,
    ) -> None:
        normalized = normalize_uuid(characteristic_uuid)
        for characteristic in service.characteristics:
            if normalize_uuid(characteristic.uuid) != normalized:
                continue
            properties = {value.lower() for value in characteristic.properties}
            if required_property == "write":
                writable = {"write", "write-without-response"} & properties
                if writable:
                    return
            elif required_property.lower() in properties:
                return
            break
        raise AtvvV04GattAudioControllerError(
            f"ATVV 特征不存在或不支持 {required_property}：{normalized}"
        )

    def _require_state(self, expected: GattSessionState) -> int:
        generation = self._generation
        actual = self._session.snapshot.state
        if generation is None or actual != expected:
            raise AtvvV04GattAudioControllerError(
                f"ATVV 状态不允许此操作：需要 {expected.value}，当前 {actual.value}"
            )
        return generation

    async def _disconnect_transport_safely(self) -> None:
        """错误路径尽力断开传输。"""

        try:
            await self._transport.disconnect()
        except Exception:
            pass


__all__ = [
    "AtvvV04GattAudioController",
    "AtvvV04GattAudioControllerError",
    "AtvvV04GattAudioControllerSnapshot",
    "AtvvV04GattTransport",
]
