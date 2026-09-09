"""程序说明：验证 ATVV v0.4 GATT 控制器的协商和音频流转发。"""

from __future__ import annotations

import asyncio
import unittest

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.atvv_protocol import (
    ATVV_AUDIO_CHARACTERISTIC_UUID,
    ATVV_AUDIO_SERVICE_UUID,
    ATVV_CONTROL_CHARACTERISTIC_UUID,
    ATVV_TX_CHARACTERISTIC_UUID,
)
from t1remote.core.gatt_session import GattSessionState
from t1remote.windows.gatt import GattCharacteristicInfo, GattServiceInfo
from t1remote.windows.atvv_audio import (
    AtvvV04GattAudioController,
    AtvvV04GattAudioControllerError,
)


class _FakeAtvvTransport:
    def __init__(self, services: tuple[GattServiceInfo, ...]) -> None:
        self.services = services
        self.callbacks: dict[str, object] = {}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.connected = False

    async def connect(self, timeout: float = 15.0) -> None:
        self.connected = timeout > 0

    async def disconnect(self) -> None:
        self.connected = False
        self.callbacks.clear()

    async def discover_services(self) -> tuple[GattServiceInfo, ...]:
        return self.services

    async def subscribe(self, characteristic_uuid: str, callback) -> None:
        self.callbacks[characteristic_uuid] = callback

    async def unsubscribe(self, characteristic_uuid: str) -> None:
        self.callbacks.pop(characteristic_uuid, None)

    async def write(self, characteristic_uuid: str, data: bytes, *, response: bool) -> None:
        self.writes.append((characteristic_uuid, data, response))

    async def emit(self, characteristic_uuid: str, payload: bytes) -> None:
        callback = self.callbacks.get(characteristic_uuid)
        if callback is None:
            return
        result = callback(payload)
        if asyncio.iscoroutine(result):
            await result


def _services() -> tuple[GattServiceInfo, ...]:
    return (
        GattServiceInfo(
            ATVV_AUDIO_SERVICE_UUID,
            (
                GattCharacteristicInfo(ATVV_TX_CHARACTERISTIC_UUID, ("write-without-response",)),
                GattCharacteristicInfo(ATVV_AUDIO_CHARACTERISTIC_UUID, ("notify",)),
                GattCharacteristicInfo(ATVV_CONTROL_CHARACTERISTIC_UUID, ("notify",)),
            ),
        ),
    )


def _frame() -> bytes:
    return bytes.fromhex("000100123400") + bytes(128)


class AtvvV04GattAudioControllerTests(unittest.TestCase):
    def test_negotiates_opens_and_delivers_pcm(self) -> None:
        async def scenario() -> None:
            transport = _FakeAtvvTransport(_services())
            queue = PcmFrameQueue()
            controller = AtvvV04GattAudioController(transport, queue)

            await controller.connect()
            negotiation = asyncio.create_task(controller.negotiate(timeout=1))
            await asyncio.sleep(0)
            await transport.emit(
                ATVV_CONTROL_CHARACTERISTIC_UUID,
                bytes.fromhex("0b0004000100860014"),
            )
            caps = await negotiation

            self.assertEqual(caps.version, (0, 4))
            self.assertEqual(controller.snapshot.session.state, GattSessionState.STREAMING)
            await controller.open_microphone()
            await transport.emit(ATVV_AUDIO_CHARACTERISTIC_UUID, _frame())
            pcm = queue.pop(timeout=0)
            self.assertIsNotNone(pcm)
            self.assertEqual(transport.writes[0][1], bytes.fromhex("0a00010001"))
            self.assertEqual(transport.writes[1][1], bytes.fromhex("0c0001"))

            await controller.close_microphone()
            self.assertEqual(transport.writes[2][1], bytes.fromhex("0d"))
            await controller.disconnect()

        asyncio.run(scenario())

    def test_connect_rejects_missing_control_characteristic(self) -> None:
        async def scenario() -> None:
            services = (
                GattServiceInfo(
                    ATVV_AUDIO_SERVICE_UUID,
                    (
                        GattCharacteristicInfo(ATVV_TX_CHARACTERISTIC_UUID, ("write",)),
                        GattCharacteristicInfo(ATVV_AUDIO_CHARACTERISTIC_UUID, ("notify",)),
                    ),
                ),
            )
            controller = AtvvV04GattAudioController(_FakeAtvvTransport(services), PcmFrameQueue())

            with self.assertRaises(AtvvV04GattAudioControllerError):
                await controller.connect()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
