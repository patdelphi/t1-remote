"""程序说明：读取语音测试生成的 WAV，并在默认真实音频输出端回放。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import wave

from t1remote.core.audio_buffer import PcmFormat
from t1remote.core.pcm_sink import PcmSinkError
from t1remote.core.sounddevice_sink import SoundDevicePcmSink


class VoiceReplayError(RuntimeError):
    """语音回放文件或音频输出端点错误。"""


ReplaySinkFactory = Callable[[PcmFormat], Any]


def play_wav_file(
    path: Path,
    *,
    sink_factory: ReplaySinkFactory = SoundDevicePcmSink,
    chunk_frames: int = 2_048,
) -> None:
    """在默认真实音频输出端播放一个 PCM16 WAV 文件。"""

    if chunk_frames < 1:
        raise ValueError("chunk_frames 必须大于 0")
    if not path.is_file():
        raise VoiceReplayError(f"录音文件不存在：{path}")

    try:
        audio = wave.open(str(path), "rb")
    except (OSError, wave.Error) as error:
        raise VoiceReplayError(f"无法打开录音文件：{path}") from error

    with audio:
        if audio.getsampwidth() != 2:
            raise VoiceReplayError("录音文件不是 PCM16 WAV")
        try:
            pcm_format = PcmFormat(audio.getframerate(), audio.getnchannels())
        except ValueError as error:
            raise VoiceReplayError("录音文件的采样率或声道数无效") from error
        try:
            sink = sink_factory(pcm_format)
        except Exception as error:
            raise VoiceReplayError("无法启动默认音频输出端点") from error

        playback_error: BaseException | None = None
        try:
            while True:
                chunk = audio.readframes(chunk_frames)
                if not chunk:
                    break
                sink.write(chunk)
        except Exception as error:
            playback_error = error
        finally:
            try:
                sink.close()
            except Exception as error:
                if playback_error is None:
                    playback_error = error
        if playback_error is not None:
            if isinstance(playback_error, PcmSinkError):
                raise VoiceReplayError("语音回放输出失败") from playback_error
            raise VoiceReplayError("语音回放失败") from playback_error


__all__ = ["VoiceReplayError", "play_wav_file"]
