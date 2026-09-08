"""程序说明：验证 GATT 音频会话状态机和旧回调隔离。"""

from __future__ import annotations

import unittest

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.audio_pipeline import ImaPcmPipeline
from t1remote.core.gatt_audio_pipeline import GattAudioProcessor
from t1remote.core.gatt_session import (
    GattAudioSession,
    GattSessionError,
    GattSessionState,
)


class GattAudioSessionTests(unittest.TestCase):
    """覆盖正常生命周期、旧 generation 和异常回调。"""

    def test_lifecycle_reaches_streaming_and_drains(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)
        session.begin_drain(generation)
        session.finish_drain(generation)

        self.assertEqual(session.snapshot.state, GattSessionState.DISCONNECTED)

    def test_old_notification_is_ignored_after_disconnect(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)
        session.disconnect(generation)
        received: list[bytes] = []

        accepted = session.accept_notification(generation, b"old", received.append)

        self.assertFalse(accepted)
        self.assertEqual(received, [])
        self.assertEqual(session.snapshot.ignored_notifications, 1)

    def test_callback_failure_enters_error(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)

        with self.assertRaises(ValueError):
            session.accept_notification(
                generation,
                b"frame",
                lambda _payload: (_ for _ in ()).throw(ValueError("bad frame")),
            )

        self.assertEqual(session.snapshot.state, GattSessionState.ERROR)
        self.assertEqual(session.snapshot.last_error, "bad frame")

    def test_invalid_transition_is_rejected(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()

        with self.assertRaises(GattSessionError):
            session.on_negotiated(generation)

    def test_processor_decodes_current_generation_into_pcm_queue(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)
        queue = PcmFrameQueue()
        processor = GattAudioProcessor(session, ImaPcmPipeline(queue))

        self.assertTrue(processor.handle_notification(generation, b"\x77"))
        self.assertEqual(queue.pop(), b"\x0b\x00\x29\x00")
        self.assertEqual(processor.snapshot.session.accepted_notifications, 1)

    def test_processor_can_inject_frame_header_adapter(self) -> None:
        session = GattAudioSession()
        generation = session.begin_connect()
        session.on_connected(generation)
        session.on_services_discovered(generation)
        session.on_negotiated(generation)
        queue = PcmFrameQueue()
        processor = GattAudioProcessor(
            session,
            ImaPcmPipeline(queue),
            payload_adapter=lambda payload: payload[1:],
        )

        processor.handle_notification(generation, b"\x99\x77")

        self.assertEqual(queue.pop(), b"\x0b\x00\x29\x00")


if __name__ == "__main__":
    unittest.main()
