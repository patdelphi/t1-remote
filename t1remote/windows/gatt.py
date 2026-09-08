"""程序说明：提供可选 Bleak GATT 传输适配器，不解释设备私有音频协议。"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import inspect
import re
from typing import Any, Awaitable, Callable


T1_AUDIO_SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
NotificationCallback = Callable[[bytes], Awaitable[None] | None]


class GattTransportError(RuntimeError):
    """GATT 传输依赖缺失、连接失败或调用顺序错误。"""


@dataclass(frozen=True)
class GattCharacteristicInfo:
    """GATT 特征的脱敏描述。"""

    uuid: str
    properties: tuple[str, ...]
    descriptor_uuids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GattServiceInfo:
    """GATT 服务和其特征清单。"""

    uuid: str
    characteristics: tuple[GattCharacteristicInfo, ...]


def normalize_uuid(value: str) -> str:
    """规范化 UUID，拒绝空值和非 UUID 字符串。"""

    import uuid

    text = str(value).strip()
    if re.fullmatch(r"[0-9a-fA-F]{4}", text):
        text = f"0000{text}-0000-1000-8000-00805f9b34fb"
    elif re.fullmatch(r"[0-9a-fA-F]{8}", text):
        text = f"{text}-0000-1000-8000-00805f9b34fb"
    try:
        return str(uuid.UUID(text)).lower()
    except (AttributeError, ValueError) as error:
        raise ValueError(f"无效 GATT UUID：{value!r}") from error


def _properties(characteristic: Any) -> tuple[str, ...]:
    """读取 Bleak 特征属性并固定排序，便于夹具比较。"""

    raw_properties = getattr(characteristic, "properties", ())
    return tuple(sorted(str(item) for item in raw_properties))


def _descriptor_uuids(characteristic: Any) -> tuple[str, ...]:
    """读取 Bleak 特征的描述符 UUID。"""

    descriptors = getattr(characteristic, "descriptors", ())
    values: list[str] = []
    for descriptor in descriptors:
        value = getattr(descriptor, "uuid", None)
        if value:
            values.append(normalize_uuid(str(value)))
    return tuple(sorted(values))


def summarize_bleak_services(services: Any) -> tuple[GattServiceInfo, ...]:
    """把 Bleak 服务对象转换为稳定、可序列化的服务摘要。"""

    summaries: list[GattServiceInfo] = []
    for service in services:
        service_uuid = normalize_uuid(str(getattr(service, "uuid")))
        characteristics: list[GattCharacteristicInfo] = []
        for characteristic in getattr(service, "characteristics", ()):
            characteristics.append(
                GattCharacteristicInfo(
                    uuid=normalize_uuid(str(getattr(characteristic, "uuid"))),
                    properties=_properties(characteristic),
                    descriptor_uuids=_descriptor_uuids(characteristic),
                )
            )
        summaries.append(
            GattServiceInfo(service_uuid, tuple(sorted(characteristics, key=lambda item: item.uuid)))
        )
    return tuple(sorted(summaries, key=lambda item: item.uuid))


class BleakGattAdapter:
    """对 BleakClient 的最小异步封装；Bleak 作为可选依赖延迟加载。"""

    def __init__(self, address_or_identifier: str) -> None:
        if not address_or_identifier.strip():
            raise ValueError("BLE 地址或设备标识不能为空")
        try:
            from bleak import BleakClient
        except ImportError as error:
            raise GattTransportError(
                "未安装可选依赖 bleak；安装项目的 ble extra 后才能使用 GATT 探测"
            ) from error
        self._client = BleakClient(address_or_identifier)
        self._notification_handlers: dict[str, Callable[..., Any]] = {}

    @property
    def is_connected(self) -> bool:
        """返回当前 Bleak 连接状态。"""

        return bool(self._client.is_connected)

    async def connect(self, timeout: float = 15.0) -> None:
        """连接 BLE 设备，不执行任何特征写入。"""

        try:
            await self._client.connect(timeout=timeout)
        except Exception as error:
            raise GattTransportError(f"BLE 连接失败：{error}") from error

    async def disconnect(self) -> None:
        """停止通知并断开 BLE 连接。"""

        try:
            for characteristic_uuid in tuple(self._notification_handlers):
                await self.unsubscribe(characteristic_uuid)
            if self.is_connected:
                await self._client.disconnect()
        except Exception as error:
            raise GattTransportError(f"BLE 断开失败：{error}") from error

    async def discover_services(self) -> tuple[GattServiceInfo, ...]:
        """读取当前连接的服务和特征摘要。"""

        if not self.is_connected:
            raise GattTransportError("BLE 尚未连接，不能发现 GATT 服务")
        try:
            return summarize_bleak_services(self._client.services)
        except Exception as error:
            raise GattTransportError(f"GATT 服务发现失败：{error}") from error

    async def subscribe(
        self,
        characteristic_uuid: str,
        callback: NotificationCallback,
    ) -> None:
        """订阅通知；不自动写入 CCCD 之外的设备私有命令。"""

        normalized_uuid = normalize_uuid(characteristic_uuid)
        if not self.is_connected:
            raise GattTransportError("BLE 尚未连接，不能订阅通知")

        def handler(_characteristic: Any, data: bytearray) -> None:
            result = callback(bytes(data))
            if inspect.isawaitable(result):
                asyncio.create_task(result)

        try:
            await self._client.start_notify(normalized_uuid, handler)
            self._notification_handlers[normalized_uuid] = handler
        except Exception as error:
            raise GattTransportError(f"GATT 通知订阅失败：{error}") from error

    async def unsubscribe(self, characteristic_uuid: str) -> None:
        """取消一个特征通知订阅。"""

        normalized_uuid = normalize_uuid(characteristic_uuid)
        if normalized_uuid not in self._notification_handlers:
            return
        try:
            await self._client.stop_notify(normalized_uuid)
        except Exception as error:
            raise GattTransportError(f"GATT 通知取消失败：{error}") from error
        finally:
            self._notification_handlers.pop(normalized_uuid, None)

    async def write(
        self,
        characteristic_uuid: str,
        data: bytes,
        *,
        response: bool = True,
    ) -> None:
        """向指定特征写入调用方提供的原始数据。"""

        if not isinstance(data, bytes):
            raise TypeError("GATT 写入数据必须是 bytes")
        normalized_uuid = normalize_uuid(characteristic_uuid)
        if not self.is_connected:
            raise GattTransportError("BLE 尚未连接，不能写入特征")
        try:
            await self._client.write_gatt_char(normalized_uuid, data, response=response)
        except Exception as error:
            raise GattTransportError(f"GATT 特征写入失败：{error}") from error


__all__ = [
    "BleakGattAdapter",
    "GattCharacteristicInfo",
    "GattServiceInfo",
    "GattTransportError",
    "T1_AUDIO_SERVICE_UUID",
    "normalize_uuid",
    "summarize_bleak_services",
]
