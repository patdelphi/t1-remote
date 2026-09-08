"""程序说明：验证 GATT 音频控制器的连接、订阅和旧回调隔离。"""

from __future__ import annotations

import asyncio
import unittest

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.audio_pipeline import ImaPcmPipeline
from t1remote.core.gatt_audio_pipeline import GattAudioProcessor
from t1remote.core.gatt_session import GattAudioSession, GattSessionState
from t1remote.windows.gatt import (
    GattCharacteristicInfo,
    GattServiceInfo,
    T1_AUDIO_SERVICE_UUID,
)
from t1remote.windows.gatt_audio import GattAudioController, GattAudioControllerError


class _FakeTransport:
    def __init__(self, services: tuple[GattServiceInfo, ...]) -> None:
        self.services = services
        self.connected = False
        self.callback = None
        self.subscribed_uuid: str | None = None

    async def connect(self, timeout: float = 15.0) -> None:
        self.connected = timeout > 0

    async def disconnect(self) -> None:
        self.connected = False
        self.callback = None

    async def discover_services(self) -> tuple[GattServiceInfo, ...]:
        return self.services

    async def subscribe(self, characteristic_uuid, callback) -> None:
        self.subscribed_uuid = characteristic_uuid
        self.callback = callback

    async def unsubscribe(self, characteristic_uuid: str) -> None:
        if characteristic_uuid == self.subscribed_uuid:
            self.callback = None

    async def emit(self, payload: bytes) -> None:
        if self.callback is not None:
            result = self.callback(payload)
            if asyncio.iscoroutine(result):
                await result


def _processor() -> tuple[GattAudioSession, GattAudioProcessor, PcmFrameQueue]:
    session = GattAudioSession()
    queue = PcmFrameQueue()
    processor = GattAudioProcessor(session, ImaPcmPipeline(queue))
    return session, processor, queue


def _services() -> tuple[GattServiceInfo, ...]:
    return (
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


class GattAudioControllerTests(unittest.TestCase):
    def test_connect_requires_explicit_negotiation_before_streaming(self) -> None:
        async def scenario() -> None:
            session, processor, _queue = _processor()
            transport = _FakeTransport(_services())
            controller = GattAudioController(transport, processor)

            await controller.connect()
            self.assertEqual(session.snapshot.state, GattSessionState.NEGOTIATING)
            with self.assertRaises(GattAudioControllerError):
                await controller.start_stream(
                    "ab5e0002-5a21-4f05-bc7d-af01f617b664"
                )
            await controller.disconnect()

        asyncio.run(scenario())

    def test_stream_notification_flows_to_pcm_and_disconnect_invalidates_callback(self) -> None:
        async def scenario() -> None:
            session, processor, queue = _processor()
            transport = _FakeTransport(_services())
            controller = GattAudioController(transport, processor)

            await controller.connect()
            await controller.start_stream(
                "AB5E0002-5A21-4F05-BC7D-AF01F617B664",
                negotiation_confirmed=True,
            )
            await transport.emit(b"\x77")
            self.assertEqual(queue.pop(), b"\x0b\x00\x29\x00")
            self.assertEqual(session.snapshot.accepted_notifications, 1)

            await controller.disconnect()
            self.assertEqual(session.snapshot.state, GattSessionState.DISCONNECTED)
            self.assertIsNone(transport.callback)

        asyncio.run(scenario())

    def test_connect_rejects_missing_target_service(self) -> None:
        async def scenario() -> None:
            session, processor, _queue = _processor()
            transport = _FakeTransport(
                (
                    GattServiceInfo(
                        "180F",
                        (),
                    ),
                )
            )
            controller = GattAudioController(transport, processor)

            with self.assertRaises(GattAudioControllerError):
                await controller.connect()
            self.assertEqual(session.snapshot.state, GattSessionState.ERROR)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
