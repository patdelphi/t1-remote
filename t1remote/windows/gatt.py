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


@dataclass(frozen=True)
class BleDeviceInfo:
    """供界面显示的 BLE 设备名称和地址。"""

    name: str
    address: str
    service_uuids: tuple[str, ...] = ()
    paired: bool = False

    @property
    def is_t1_candidate(self) -> bool:
        """返回设备是否通过 ATVV 服务或设备名称识别为 T1。"""

        return self.is_t1_service_candidate or self.is_probable_t1

    @property
    def is_t1_service_candidate(self) -> bool:
        """返回广播中是否包含 T1 ATVV 语音服务。"""

        return T1_AUDIO_SERVICE_UUID in self.service_uuids

    @property
    def is_probable_t1(self) -> bool:
        """返回设备名称是否包含 T1/Remote 标识。"""

        normalized_name = self.name.casefold()
        return "t1" in normalized_name or "remote" in normalized_name


def discover_ble_devices(timeout: float = 4.0) -> tuple[BleDeviceInfo, ...]:
    """扫描附近 BLE 设备，返回可交给 BleakClient 的名称和地址。"""

    if timeout <= 0:
        raise ValueError("BLE 扫描超时时间必须大于 0")
    try:
        from bleak import BleakScanner
    except ImportError as error:
        raise GattTransportError(
            "未安装可选依赖 bleak；安装项目的 ble extra 后才能扫描 BLE 设备"
        ) from error
    async def scan() -> object:
        """兼容支持和不支持 return_adv 的 Bleak 版本。"""

        try:
            return await BleakScanner.discover(timeout=timeout, return_adv=True)
        except TypeError:
            return await BleakScanner.discover(timeout=timeout)

    scan_error: Exception | None = None
    try:
        discovered = asyncio.run(scan())
    except Exception as error:
        discovered = ()
        scan_error = error
    result: list[BleDeviceInfo] = []
    seen: set[str] = set()
    if isinstance(discovered, dict):
        device_entries = tuple(discovered.items())
    else:
        device_entries = tuple((device, None) for device in discovered)
    for device, advertisement in device_entries:
        address = str(getattr(device, "address", "")).strip()
        if not address or address in seen:
            continue
        seen.add(address)
        local_name = getattr(advertisement, "local_name", "") if advertisement else ""
        name = str(
            getattr(device, "name", "") or local_name or "未知设备"
        ).strip() or "未知设备"
        service_uuids = tuple(
            sorted(
                {
                    normalize_uuid(str(uuid_value))
                    for uuid_value in getattr(advertisement, "service_uuids", ())
                }
            )
        ) if advertisement else ()
        result.append(BleDeviceInfo(name=name, address=address, service_uuids=service_uuids))
    if not any(device.is_t1_candidate for device in result):
        try:
            paired_devices = discover_paired_ble_devices()
        except Exception:
            paired_devices = ()
        known_addresses = {device.address.casefold() for device in result}
        for device in paired_devices:
            if device.address.casefold() not in known_addresses:
                result.append(device)
    if not result and scan_error is not None:
        raise GattTransportError(f"BLE 设备扫描失败：{scan_error}") from scan_error
    return tuple(result)


def discover_paired_ble_devices() -> tuple[BleDeviceInfo, ...]:
    """读取 Windows 已缓存的 BLE 设备，覆盖设备已连接但当前不广播的情况。"""

    try:
        from winrt.windows.devices.bluetooth import BluetoothLEDevice
        from winrt.windows.devices.enumeration import DeviceInformation
    except ImportError:
        return ()

    async def query() -> tuple[BleDeviceInfo, ...]:
        selector = BluetoothLEDevice.get_device_selector()
        device_infos = await DeviceInformation.find_all_async_aqs_filter(selector)
        result: list[BleDeviceInfo] = []
        seen: set[str] = set()
        for info in device_infos:
            name = str(getattr(info, "name", "") or "未知设备").strip() or "未知设备"
            # 只把名称明显属于 T1 的已缓存设备加入回退列表，避免下拉框塞满鼠标耳机。
            normalized_name = name.casefold()
            if "t1" not in normalized_name and "remote" not in normalized_name:
                continue
            device = await BluetoothLEDevice.from_id_async(info.id)
            if device is None:
                continue
            try:
                address = f"{int(device.bluetooth_address):012X}"
            finally:
                device.close()
            if address in seen:
                continue
            seen.add(address)
            result.append(BleDeviceInfo(name=name, address=address, paired=True))
        return tuple(result)

    try:
        return asyncio.run(query())
    except Exception as error:
        raise GattTransportError(f"读取已配对 BLE 设备失败：{error}") from error


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
            from bleak import BLEDevice, BleakClient
        except ImportError as error:
            raise GattTransportError(
                "未安装可选依赖 bleak；安装项目的 ble extra 后才能使用 GATT 探测"
            ) from error
        # Windows 可能已经缓存并配对了设备，但设备当前不广播。
        # 传入 BLEDevice 可以绕过 Bleak 的再次扫描，直接使用缓存地址建立 GATT 会话。
        compact_address = re.sub(r"[:-]", "", address_or_identifier.strip())
        if re.fullmatch(r"[0-9a-fA-F]{12}", compact_address):
            normalized_address = ":".join(
                compact_address[index : index + 2]
                for index in range(0, len(compact_address), 2)
            )
            target: Any = BLEDevice(normalized_address, "T1-Remote", None)
        else:
            target = address_or_identifier
        self._client = BleakClient(target)
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
    "BleDeviceInfo",
    "GattCharacteristicInfo",
    "GattServiceInfo",
    "GattTransportError",
    "T1_AUDIO_SERVICE_UUID",
    "discover_ble_devices",
    "discover_paired_ble_devices",
    "normalize_uuid",
    "summarize_bleak_services",
]
