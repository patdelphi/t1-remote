"""程序说明：提供与具体 T1 报文格式无关的 IMA-DVI ADPCM 解码器。"""

from __future__ import annotations

from dataclasses import dataclass


_STEP_TABLE: tuple[int, ...] = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878,
    2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894,
    6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818,
    18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
_INDEX_TABLE: tuple[int, ...] = (
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
)
MIN_SAMPLE = -32768
MAX_SAMPLE = 32767


@dataclass(frozen=True)
class ImaAdpcmState:
    """一个 IMA ADPCM 解码器在块之间保留的状态。"""

    predictor: int = 0
    step_index: int = 0

    def __post_init__(self) -> None:
        if not MIN_SAMPLE <= self.predictor <= MAX_SAMPLE:
            raise ValueError("predictor 必须在 16 位 PCM 范围内")
        if not 0 <= self.step_index < len(_STEP_TABLE):
            raise ValueError("step_index 超出 IMA ADPCM 范围")


def _clamp_sample(value: int) -> int:
    """把预测样本限制在有符号 16 位 PCM 范围。"""

    return max(MIN_SAMPLE, min(MAX_SAMPLE, value))


class ImaAdpcmDecoder:
    """增量解码 IMA-DVI ADPCM nibble 流。"""

    def __init__(
        self,
        *,
        predictor: int = 0,
        step_index: int = 0,
    ) -> None:
        self._state = ImaAdpcmState(predictor, step_index)

    @property
    def state(self) -> ImaAdpcmState:
        """返回当前解码状态。"""

        return self._state

    def reset(self, *, predictor: int = 0, step_index: int = 0) -> None:
        """重置块或新会话的预测状态。"""

        self._state = ImaAdpcmState(predictor, step_index)

    def decode(self, data: bytes, *, low_nibble_first: bool = True) -> tuple[int, ...]:
        """解码字节流并返回有符号 16 位 PCM 样本。"""

        if not isinstance(data, bytes):
            raise TypeError("ADPCM 数据必须是 bytes")
        samples: list[int] = []
        for value in data:
            nibbles = (
                (value & 0x0F, value >> 4)
                if low_nibble_first
                else (value >> 4, value & 0x0F)
            )
            samples.extend(self._decode_nibble(nibble) for nibble in nibbles)
        return tuple(samples)

    def decode_wav_ima_block(self, block: bytes) -> tuple[int, ...]:
        """解码标准 WAV IMA block；T1 是否采用该头部需另行验证。"""

        if len(block) < 4:
            raise ValueError("WAV IMA block 至少需要 4 字节头部")
        predictor = int.from_bytes(block[0:2], "little", signed=True)
        step_index = block[2]
        self.reset(predictor=predictor, step_index=step_index)
        return (predictor, *self.decode(block[4:]))

    def _decode_nibble(self, nibble: int) -> int:
        """解码一个 4 位 ADPCM code，并推进内部状态。"""

        if not 0 <= nibble <= 0x0F:
            raise ValueError("ADPCM nibble 必须在 0x0-0xF 范围内")
        state = self._state
        step = _STEP_TABLE[state.step_index]
        difference = step >> 3
        if nibble & 0x01:
            difference += step >> 2
        if nibble & 0x02:
            difference += step >> 1
        if nibble & 0x04:
            difference += step
        predictor = state.predictor - difference if nibble & 0x08 else state.predictor + difference
        predictor = _clamp_sample(predictor)
        step_index = max(0, min(len(_STEP_TABLE) - 1, state.step_index + _INDEX_TABLE[nibble]))
        self._state = ImaAdpcmState(predictor, step_index)
        return predictor


def decode_ima_adpcm(
    data: bytes,
    *,
    predictor: int = 0,
    step_index: int = 0,
    low_nibble_first: bool = True,
) -> tuple[int, ...]:
    """以一次性调用解码 IMA-DVI ADPCM 数据。"""

    decoder = ImaAdpcmDecoder(predictor=predictor, step_index=step_index)
    return decoder.decode(data, low_nibble_first=low_nibble_first)


__all__ = [
    "ImaAdpcmDecoder",
    "ImaAdpcmState",
    "decode_ima_adpcm",
]
