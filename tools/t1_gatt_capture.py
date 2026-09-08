"""程序说明：只读采集指定 GATT 通知特征的脱敏音频帧夹具。

工具只执行服务发现和通知订阅，不发送 T1 私有能力协商命令；输出不包含
BLE 地址或完整设备标识。默认禁止覆盖已有输出文件。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from t1remote.windows.gatt import (
    BleakGattAdapter,
    GattServiceInfo,
    GattTransportError,
    T1_AUDIO_SERVICE_UUID,
    normalize_uuid,
)


class GattCaptureError(RuntimeError):
    """GATT 夹具采集参数或特征选择错误。"""


def validate_notification_characteristic(
    services: Iterable[GattServiceInfo],
    characteristic_uuid: str,
) -> str:
    """确认特征属于目标音频服务且支持 notify。"""

    normalized_characteristic = normalize_uuid(characteristic_uuid)
    normalized_service = normalize_uuid(T1_AUDIO_SERVICE_UUID)
    for service in services:
        if service.uuid != normalized_service:
            continue
        for characteristic in service.characteristics:
            if characteristic.uuid != normalized_characteristic:
                continue
            if "notify" not in {value.lower() for value in characteristic.properties}:
                raise GattCaptureError(
                    f"特征不支持 notify：{normalized_characteristic}"
                )
            return normalized_characteristic
    raise GattCaptureError(
        f"目标音频服务中未找到通知特征：{normalized_characteristic}"
    )


def build_capture_document(
    services: tuple[GattServiceInfo, ...],
    characteristic_uuid: str,
    duration_seconds: float,
    frames: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """构造不含设备地址的 GATT 通知夹具文档。"""

    normalized_characteristic = normalize_uuid(characteristic_uuid)
    return {
        "format_version": 1,
        "device": "T1-Remote",
        "target_audio_service": T1_AUDIO_SERVICE_UUID,
        "characteristic": normalized_characteristic,
        "duration_seconds": duration_seconds,
        "services": [
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
        ],
        "capture": {
            "frame_count": len(frames),
            "frames": [
                {
                    "timestamp_utc": timestamp,
                    "payload_length": len(payload),
                    "payload_hex": payload.hex(" "),
                }
                for timestamp, payload in frames
            ],
        },
    }


def _utc_now() -> str:
    """返回可排序的 UTC 时间戳。"""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def capture_notifications(
    address: str,
    characteristic_uuid: str,
    *,
    duration_seconds: float = 5.0,
    max_frames: int = 200,
) -> dict[str, Any]:
    """连接、发现、订阅并在限定时间内收集通知帧。"""

    if duration_seconds <= 0:
        raise ValueError("duration_seconds 必须大于 0")
    if max_frames < 1:
        raise ValueError("max_frames 必须大于 0")

    adapter = BleakGattAdapter(address)
    await adapter.connect()
    subscribed_uuid: str | None = None
    frames: list[tuple[str, bytes]] = []
    try:
        services = await adapter.discover_services()
        normalized_characteristic = validate_notification_characteristic(
            services,
            characteristic_uuid,
        )
        finished = asyncio.Event()

        def on_notification(payload: bytes) -> None:
            if len(frames) >= max_frames:
                finished.set()
                return
            frames.append((_utc_now(), bytes(payload)))
            if len(frames) >= max_frames:
                finished.set()

        await adapter.subscribe(normalized_characteristic, on_notification)
        subscribed_uuid = normalized_characteristic
        try:
            await asyncio.wait_for(finished.wait(), timeout=duration_seconds)
        except asyncio.TimeoutError:
            pass
        return build_capture_document(
            services,
            normalized_characteristic,
            duration_seconds,
            frames,
        )
    finally:
        try:
            if subscribed_uuid is not None:
                await adapter.unsubscribe(subscribed_uuid)
        finally:
            await adapter.disconnect()


def _write_document(path: Path, document: dict[str, Any]) -> None:
    """以 UTF-8 BOM 和 CRLF 创建新夹具文件，不覆盖已有文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(document, ensure_ascii=False, indent=2)
    with path.open("x", encoding="utf-8-sig", newline="\r\n") as file:
        file.write(content)
        file.write("\r\n")


def main() -> int:
    """执行只读 GATT 通知采集。"""

    parser = argparse.ArgumentParser(description="采集 T1 GATT 通知帧夹具")
    parser.add_argument("address", help="Bleak 支持的 BLE 地址或设备标识")
    parser.add_argument("--characteristic", required=True, help="notify 特征 UUID")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--max-frames", type=int, default=200)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    try:
        document = asyncio.run(
            capture_notifications(
                args.address,
                args.characteristic,
                duration_seconds=args.seconds,
                max_frames=args.max_frames,
            )
        )
        if args.output is None:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            _write_document(args.output, document)
            print(f"已保存 GATT 夹具：{args.output}")
        return 0
    except (GattCaptureError, GattTransportError, OSError, RuntimeError, ValueError) as error:
        print(f"GATT 夹具采集失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
