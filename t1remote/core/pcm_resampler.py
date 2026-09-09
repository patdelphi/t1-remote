"""程序说明：提供小型、无第三方依赖的 PCM16LE 线性重采样。"""

from __future__ import annotations


def resample_pcm16le(
    pcm: bytes,
    *,
    input_sample_rate: int,
    output_sample_rate: int,
    channels: int = 1,
) -> bytes:
    """把交错 PCM16LE 转为目标采样率，保持声道数不变。"""

    if not isinstance(pcm, bytes):
        raise TypeError("PCM 数据必须是 bytes")
    if input_sample_rate <= 0 or output_sample_rate <= 0:
        raise ValueError("采样率必须大于 0")
    if not 1 <= channels <= 8:
        raise ValueError("channels 必须在 1-8 范围内")
    frame_width = channels * 2
    if len(pcm) % frame_width:
        raise ValueError("PCM 数据不是完整 sample frame 的整数倍")
    if input_sample_rate == output_sample_rate or not pcm:
        return pcm

    input_frames = len(pcm) // frame_width
    output_frames = max(
        1,
        round(input_frames * output_sample_rate / input_sample_rate),
    )
    samples = [
        tuple(
            int.from_bytes(
                pcm[frame * frame_width + channel * 2 : frame * frame_width + channel * 2 + 2],
                "little",
                signed=True,
            )
            for channel in range(channels)
        )
        for frame in range(input_frames)
    ]
    output = bytearray()
    for output_frame in range(output_frames):
        source_position = output_frame * input_sample_rate / output_sample_rate
        left_index = min(int(source_position), input_frames - 1)
        right_index = min(left_index + 1, input_frames - 1)
        fraction = source_position - left_index
        for channel in range(channels):
            left = samples[left_index][channel]
            right = samples[right_index][channel]
            value = round(left + (right - left) * fraction)
            value = max(-32768, min(32767, value))
            output.extend(value.to_bytes(2, "little", signed=True))
    return bytes(output)


__all__ = ["resample_pcm16le"]
