"""程序说明：验证 GATT 服务摘要和 UUID 脱敏边界。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
import sys
import types
from unittest.mock import patch

from t1remote.windows.gatt import (
    GattServiceInfo,
    T1_AUDIO_SERVICE_UUID,
    discover_ble_devices,
    normalize_uuid,
    summarize_bleak_services,
)


class _Descriptor:
    def __init__(self, uuid: str) -> None:
        self.uuid = uuid


class _Characteristic:
    def __init__(self, uuid: str, properties: list[str], descriptors: list[_Descriptor]) -> None:
        self.uuid = uuid
        self.properties = properties
        self.descriptors = descriptors


class _Service:
    def __init__(self, uuid: str, characteristics: list[_Characteristic]) -> None:
        self.uuid = uuid
        self.characteristics = characteristics


class GattSummaryTests(unittest.TestCase):
    """验证 GATT 发现摘要的稳定排序和 UUID 规范化。"""

    def test_normalizes_uuid(self) -> None:
        self.assertEqual(
            normalize_uuid("AB5E0001-5A21-4F05-BC7D-AF01F617B664"),
            "ab5e0001-5a21-4f05-bc7d-af01f617b664",
        )
        self.assertEqual(
            normalize_uuid("180F"),
            "0000180f-0000-1000-8000-00805f9b34fb",
        )

    def test_summarizes_services_without_device_address(self) -> None:
        services = summarize_bleak_services(
            [
                _Service(
                    "180F",
                    [
                        _Characteristic(
                            "2A19",
                            ["read", "notify"],
                            [_Descriptor("2902")],
                        )
                    ],
                )
            ]
        )

        self.assertEqual(services[0], GattServiceInfo(
            "0000180f-0000-1000-8000-00805f9b34fb",
            (
                services[0].characteristics[0],
            ),
        ))
        self.assertNotIn("address", str(services))

    def test_rejects_invalid_uuid(self) -> None:
        with self.assertRaises(ValueError):
            normalize_uuid("not-a-uuid")

    def test_discovers_unique_ble_device_addresses(self) -> None:
        class FakeDevice:
            def __init__(self, name: str | None, address: str) -> None:
                self.name = name
                self.address = address

        first = FakeDevice("", "AA:BB:CC:DD:EE:FF")
        second = FakeDevice(None, "11:22:33:44:55:66")
        discovered = {
            first: SimpleNamespace(local_name="", service_uuids=[T1_AUDIO_SERVICE_UUID]),
            second: SimpleNamespace(local_name="", service_uuids=[]),
        }
        fake_bleak = types.ModuleType("bleak")
        fake_bleak.BleakScanner = SimpleNamespace(discover=lambda **_kwargs: object())

        def fake_asyncio_run(coroutine):
            coroutine.close()
            return discovered

        with patch.dict(sys.modules, {"bleak": fake_bleak}), patch(
            "t1remote.windows.gatt.asyncio.run", side_effect=fake_asyncio_run
        ):
            devices = discover_ble_devices()
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[0].is_t1_candidate)
        self.assertEqual(devices[0].name, "未知设备")
        self.assertEqual(devices[1].name, "未知设备")


if __name__ == "__main__":
    unittest.main()
