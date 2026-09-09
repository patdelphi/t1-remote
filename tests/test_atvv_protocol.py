"""程序说明：验证 ATVV GATT 音频协议的纯 Python 编解码边界。"""

from __future__ import annotations

import unittest

from t1remote.core.atvv_protocol import (
    ATVV_AUDIO_CHARACTERISTIC_UUID,
    ATVV_AUDIO_SERVICE_UUID,
    ATVV_CONTROL_CHARACTERISTIC_UUID,
    ATVV_TX_CHARACTERISTIC_UUID,
    AtvvAudioFrame,
    AtvvAudioFrameAssembler,
    AtvvControlSignal,
    build_get_caps_v04,
    build_get_caps_v1,
    build_mic_close,
    build_mic_extend,
    build_mic_open,
    parse_capability_response,
)


class AtvvUuidAndCommandTests(unittest.TestCase):
    def test_standard_service_and_characteristics(self) -> None:
        self.assertEqual(
            ATVV_AUDIO_SERVICE_UUID,
            "ab5e0001-5a21-4f05-bc7d-af01f617b664",
        )
        self.assertEqual(
            ATVV_TX_CHARACTERISTIC_UUID,
            "ab5e0002-5a21-4f05-bc7d-af01f617b664",
        )
        self.assertEqual(
            ATVV_AUDIO_CHARACTERISTIC_UUID,
            "ab5e0003-5a21-4f05-bc7d-af01f617b664",
        )
        self.assertEqual(
            ATVV_CONTROL_CHARACTERISTIC_UUID,
            "ab5e0004-5a21-4f05-bc7d-af01f617b664",
        )

    def test_commands_use_big_endian_fields(self) -> None:
        self.assertEqual(build_get_caps_v04(), bytes.fromhex("0a00010001"))
        self.assertEqual(build_get_caps_v1(), bytes.fromhex("0a0100000303"))
        self.assertEqual(build_mic_open(0x0001), bytes.fromhex("0c0001"))
        self.assertEqual(build_mic_close(), bytes.fromhex("0d"))
        self.assertEqual(build_mic_extend(), bytes.fromhex("0e00"))

    def test_control_signal_values_are_stable(self) -> None:
        self.assertEqual(AtvvControlSignal.AUDIO_STOP, 0x00)
        self.assertEqual(AtvvControlSignal.AUDIO_START, 0x04)
        self.assertEqual(AtvvControlSignal.START_SEARCH, 0x08)
        self.assertEqual(AtvvControlSignal.GET_CAPS_RESPONSE, 0x0B)


class AtvvCapabilityTests(unittest.TestCase):
    def test_parse_v04_capability_response(self) -> None:
        caps = parse_capability_response(bytes.fromhex("0b0004000100860014"))
        self.assertEqual(caps.version, (0, 4))
        self.assertEqual(caps.codec_flags, 0x0001)
        self.assertEqual(caps.frame_size, 134)
        self.assertEqual(caps.characteristic_payload_size, 20)

    def test_parse_v1_capability_response_without_interpreting_private_flags(self) -> None:
        caps = parse_capability_response(bytes.fromhex("0b0100020300780000"))
        self.assertEqual(caps.version, (1, 0))
        self.assertEqual(caps.codec_flags, 0x0203)
        self.assertEqual(caps.frame_size, 120)
        self.assertEqual(caps.characteristic_payload_size, 0)


class AtvvFrameTests(unittest.TestCase):
    def test_fragmented_v04_frame_is_reassembled_and_decoded(self) -> None:
        frame_bytes = bytes.fromhex("000100123400") + bytes(128)
        assembler = AtvvAudioFrameAssembler(frame_size=134)
        frames: list[AtvvAudioFrame] = []
        for offset in range(0, len(frame_bytes), 20):
            frames.extend(assembler.feed(frame_bytes[offset : offset + 20]))

        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.sequence, 1)
        self.assertEqual(frame.predictor, 0x1234)
        self.assertEqual(frame.step_index, 0)
        self.assertEqual(len(frame.samples), 257)
        self.assertTrue(all(sample == 0x1234 for sample in frame.samples))
        self.assertEqual(len(frame.pcm16le), 257 * 2)

    def test_invalid_v04_frame_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AtvvAudioFrame.from_bytes(bytes(133))
        with self.assertRaises(ValueError):
            AtvvAudioFrame.from_bytes(bytes.fromhex("00010001123400") + bytes(128))


if __name__ == "__main__":
    unittest.main()
