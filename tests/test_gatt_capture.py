"""程序说明：验证 GATT 通知夹具的脱敏序列化和特征校验。"""

from __future__ import annotations

import unittest

from t1remote.windows.gatt import GattCharacteristicInfo, GattServiceInfo, T1_AUDIO_SERVICE_UUID
from tools.t1_gatt_capture import (
    GattCaptureError,
    build_capture_document,
    validate_notification_characteristic,
)


class GattCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.services = (
            GattServiceInfo(
                T1_AUDIO_SERVICE_UUID,
                (
                    GattCharacteristicInfo(
                        "ab5e0002-5a21-4f05-bc7d-af01f617b664",
                        ("notify",),
                    ),
                ),
            ),
        )

    def test_validates_notify_characteristic_inside_target_service(self) -> None:
        uuid = validate_notification_characteristic(
            self.services,
            "AB5E0002-5A21-4F05-BC7D-AF01F617B664",
        )

        self.assertEqual(uuid, "ab5e0002-5a21-4f05-bc7d-af01f617b664")

    def test_rejects_characteristic_without_notify_property(self) -> None:
        services = (
            GattServiceInfo(
                T1_AUDIO_SERVICE_UUID,
                (GattCharacteristicInfo("ab5e0002-5a21-4f05-bc7d-af01f617b664", ("read",)),),
            ),
        )

        with self.assertRaises(GattCaptureError):
            validate_notification_characteristic(
                services,
                "ab5e0002-5a21-4f05-bc7d-af01f617b664",
            )

    def test_document_contains_only_redacted_notification_frames(self) -> None:
        document = build_capture_document(
            self.services,
            "ab5e0002-5a21-4f05-bc7d-af01f617b664",
            3.5,
            [
                ("2026-09-08T12:00:00.000+00:00", b"\x01\x02"),
            ],
        )

        self.assertEqual(document["capture"]["frame_count"], 1)
        self.assertEqual(document["capture"]["frames"][0]["payload_hex"], "01 02")
        self.assertNotIn("address", str(document).lower())
        self.assertEqual(document["duration_seconds"], 3.5)


if __name__ == "__main__":
    unittest.main()
