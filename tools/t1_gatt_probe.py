"""程序说明：只读发现指定 BLE 设备的 GATT 服务和特征。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from t1remote.windows.gatt import (
    BleakGattAdapter,
    GattTransportError,
    T1_AUDIO_SERVICE_UUID,
)


def _serialize_services(services: tuple[Any, ...]) -> list[dict[str, Any]]:
    """生成不包含 BLE 地址的服务发现 JSON。"""

    return [
        {
            "uuid": service.uuid,
            "characteristics": [
                {
                    "uuid": characteristic.uuid,
                    "properties": list(characteristic.properties),
                    "descriptor_uuids": list(characteristic.descriptor_uuids),
                }
                for characteristic in service.characteristics
            ],
        }
        for service in services
    ]


async def _probe(address: str) -> tuple[Any, ...]:
    """连接、发现服务并安全断开；不写入任何特征。"""

    adapter = BleakGattAdapter(address)
    await adapter.connect()
    try:
        return await adapter.discover_services()
    finally:
        await adapter.disconnect()


def main() -> int:
    """执行只读 GATT 服务探测。"""

    parser = argparse.ArgumentParser(description="只读发现 T1 GATT 服务和特征")
    parser.add_argument("address", help="Bleak 支持的 BLE 地址或设备标识")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="可选 JSON 输出路径；默认打印到标准输出",
    )
    args = parser.parse_args()
    try:
        services = asyncio.run(_probe(args.address))
        document = {
            "device": "T1-Remote",
            "target_audio_service": T1_AUDIO_SERVICE_UUID,
            "services": _serialize_services(services),
        }
        content = json.dumps(document, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8-sig", newline="\r\n") as file:
                file.write(content)
                file.write("\r\n")
        else:
            print(content)
        return 0
    except (GattTransportError, OSError, RuntimeError, ValueError) as error:
        print(f"GATT 探测失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
