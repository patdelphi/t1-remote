"""程序说明：把 GATT 会话通知、安全帧适配和 PCM 管线组合起来。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from t1remote.core.audio_pipeline import AudioPipelineStats, ImaPcmPipeline
from t1remote.core.gatt_session import GattAudioSession, GattSessionSnapshot


PayloadAdapter = Callable[[bytes], bytes]


@dataclass(frozen=True)
class GattAudioPipelineSnapshot:
    """GATT 会话和 PCM 管线的组合快照。"""

    session: GattSessionSnapshot
    audio: AudioPipelineStats


class GattAudioProcessor:
    """将当前 GATT generation 的通知交给可注入的音频帧适配器。"""

    def __init__(
        self,
        session: GattAudioSession,
        pipeline: ImaPcmPipeline,
        *,
        payload_adapter: PayloadAdapter | None = None,
    ) -> None:
        self._session = session
        self._pipeline = pipeline
        self._payload_adapter = payload_adapter or _identity_payload

    @property
    def session(self) -> GattAudioSession:
        """返回控制器需要协调的 GATT 会话对象。"""

        return self._session

    @property
    def snapshot(self) -> GattAudioPipelineSnapshot:
        """返回当前会话和音频管线统计。"""

        return GattAudioPipelineSnapshot(
            session=self._session.snapshot,
            audio=self._pipeline.stats,
        )

    def handle_notification(self, generation: int, payload: bytes) -> bool:
        """处理一条 GATT 通知；旧 generation 不会进入解码器。"""

        def decode_payload(notification: bytes) -> None:
            adpcm_payload = self._payload_adapter(notification)
            if not isinstance(adpcm_payload, bytes):
                raise TypeError("payload_adapter 必须返回 bytes")
            self._pipeline.feed(adpcm_payload)

        return self._session.accept_notification(
            generation,
            payload,
            decode_payload,
        )


def _identity_payload(payload: bytes) -> bytes:
    """默认适配器：明确要求调用方传入已剥离私有帧头的载荷。"""

    return payload


__all__ = ["GattAudioPipelineSnapshot", "GattAudioProcessor", "PayloadAdapter"]
