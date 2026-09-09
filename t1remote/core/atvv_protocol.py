"""程序说明：实现 ATVV GATT 音频协议的 UUID、命令和 v0.4 音频帧解析。

协议版本和音频帧布局由设备能力响应决定。这里提供公开 ATVV v0.4
帧格式的确定性实现；不会把同一 UUID 自动视为 T1 已兼容的证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from t1remote.core.audio_pipeline import samples_to_pcm16le
from t1remote.core.ima_adpcm import ImaAdpcmDecoder


ATVV_AUDIO_SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
ATVV_TX_CHARACTERISTIC_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
ATVV_AUDIO_CHARACTERISTIC_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
ATVV_CONTROL_CHARACTERISTIC_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

ATVV_CODEC_ADPCM_8KHZ = 0x0001
ATVV_V04_FRAME_SIZE = 134
ATVV_V04_SAMPLE_RATE = 8_000
ATVV_FRAME_HEADER_SIZE = 6


class AtvvControlSignal(IntEnum):
    """ATVV 控制特征的已知信号。"""

    AUDIO_STOP = 0x00
    AUDIO_START = 0x04
    START_SEARCH = 0x08
    GET_CAPS_RESPONSE = 0x0B


@dataclass(frozen=True)
class AtvvCapabilityResponse:
    """能力响应中的通用字段；未知位保留为 codec_flags。"""

    version: tuple[int, int]
    codec_flags: int
    frame_size: int
    characteristic_payload_size: int
    raw: bytes


def _u16(value: int, *, name: str) -> bytes:
    """编码一个大端无符号 16 位字段。"""

    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{name} 必须在 0 到 65535 范围内")
    return value.to_bytes(2, "big")


def build_get_caps_v04() -> bytes:
    """构造公开 ATVV v0.4 设备使用的能力查询命令。"""

    return bytes.fromhex("0A 00 01 00 01")


def build_get_caps_v1() -> bytes:
    """构造公开 ATVV v1.0 设备使用的能力查询命令。"""

    return bytes.fromhex("0A 01 00 00 03 03")


def build_mic_open(codec: int) -> bytes:
    """构造 MIC_OPEN；codec 字段按协议使用大端序。"""

    return b"\x0C" + _u16(codec, name="codec")


def build_mic_close() -> bytes:
    """构造 v0.4 ATVV MIC_CLOSE 命令。"""

    return b"\x0D"


def build_mic_extend(stream_id: int = 0) -> bytes:
    """构造 v0.4 ATVV MIC_EXTEND 命令。"""

    if not 0 <= stream_id <= 0xFF:
        raise ValueError("stream_id 必须在 0 到 255 范围内")
    return bytes((0x0E, stream_id))


def parse_capability_response(payload: bytes) -> AtvvCapabilityResponse:
    """解析 0x0B 开头的 ATVV 能力响应，保留未知字段。"""

    if not isinstance(payload, bytes):
        raise TypeError("ATVV 能力响应必须是 bytes")
    if len(payload) < 9:
        raise ValueError("ATVV 能力响应至少需要 9 字节")
    if payload[0] != AtvvControlSignal.GET_CAPS_RESPONSE:
        raise ValueError("不是 ATVV GET_CAPS_RESPONSE")
    return AtvvCapabilityResponse(
        version=(payload[1], payload[2]),
        codec_flags=int.from_bytes(payload[3:5], "big"),
        frame_size=int.from_bytes(payload[5:7], "big"),
        characteristic_payload_size=int.from_bytes(payload[7:9], "big"),
        raw=bytes(payload),
    )


@dataclass(frozen=True)
class AtvvAudioFrame:
    """公开 ATVV v0.4 的 134 字节音频帧及其 PCM 结果。"""

    sequence: int
    predictor: int
    step_index: int
    adpcm_payload: bytes
    samples: tuple[int, ...]

    @classmethod
    def from_bytes(cls, payload: bytes) -> "AtvvAudioFrame":
        """按 v0.4 大端头部和高 nibble 优先规则解析一帧。"""

        if not isinstance(payload, bytes):
            raise TypeError("ATVV 音频帧必须是 bytes")
        if len(payload) != ATVV_V04_FRAME_SIZE:
            raise ValueError(
                f"ATVV v0.4 音频帧必须是 {ATVV_V04_FRAME_SIZE} 字节，实际为 {len(payload)}"
            )
        if payload[2] != 0:
            raise ValueError("ATVV v0.4 音频帧的保留字节必须为 0")
        predictor = int.from_bytes(payload[3:5], "big", signed=True)
        step_index = payload[5]
        if step_index > 88:
            raise ValueError("ATVV DVI step_index 超出范围")
        adpcm_payload = payload[ATVV_FRAME_HEADER_SIZE:]
        decoder = ImaAdpcmDecoder(predictor=predictor, step_index=step_index)
        samples = (predictor, *decoder.decode(adpcm_payload, low_nibble_first=False))
        return cls(
            sequence=int.from_bytes(payload[0:2], "big"),
            predictor=predictor,
            step_index=step_index,
            adpcm_payload=bytes(adpcm_payload),
            samples=tuple(samples),
        )

    @property
    def pcm16le(self) -> bytes:
        """返回 16 位单声道 PCM little-endian 数据。"""

        return samples_to_pcm16le(self.samples)


class AtvvAudioFrameAssembler:
    """把 BLE MTU 分片重新组装为完整 ATVV 音频帧。"""

    def __init__(self, *, frame_size: int = ATVV_V04_FRAME_SIZE) -> None:
        if frame_size <= 0:
            raise ValueError("frame_size 必须大于 0")
        self._frame_size = frame_size
        self._buffer = bytearray()

    @property
    def buffered_size(self) -> int:
        """返回尚未组成完整帧的字节数。"""

        return len(self._buffer)

    def feed(self, payload: bytes) -> tuple[AtvvAudioFrame, ...]:
        """加入一段通知数据并返回已经完成的 v0.4 音频帧。"""

        if not isinstance(payload, bytes):
            raise TypeError("ATVV 通知数据必须是 bytes")
        self._buffer.extend(payload)
        frames: list[AtvvAudioFrame] = []
        while len(self._buffer) >= self._frame_size:
            candidate = bytes(self._buffer[: self._frame_size])
            frame = AtvvAudioFrame.from_bytes(candidate)
            del self._buffer[: self._frame_size]
            frames.append(frame)
        return tuple(frames)

    def reset(self) -> None:
        """清空当前未完成帧，供断连或协议切换使用。"""

        self._buffer.clear()


__all__ = [
    "ATVV_AUDIO_CHARACTERISTIC_UUID",
    "ATVV_AUDIO_SERVICE_UUID",
    "ATVV_CODEC_ADPCM_8KHZ",
    "ATVV_CONTROL_CHARACTERISTIC_UUID",
    "ATVV_FRAME_HEADER_SIZE",
    "ATVV_TX_CHARACTERISTIC_UUID",
    "ATVV_V04_FRAME_SIZE",
    "ATVV_V04_SAMPLE_RATE",
    "AtvvAudioFrame",
    "AtvvAudioFrameAssembler",
    "AtvvCapabilityResponse",
    "AtvvControlSignal",
    "build_get_caps_v04",
    "build_get_caps_v1",
    "build_mic_close",
    "build_mic_extend",
    "build_mic_open",
    "parse_capability_response",
]
