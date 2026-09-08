"""程序说明：提供 GATT 音频通知到 PCM 输出之间的有界线程安全缓冲。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class PcmFormat:
    """PCM 输出格式；不绑定具体 Windows 音频后端。"""

    sample_rate: int
    channels: int
    sample_width_bytes: int = 2

    def __post_init__(self) -> None:
        if not 8000 <= self.sample_rate <= 192000:
            raise ValueError("sample_rate 必须在 8000-192000 Hz 范围内")
        if not 1 <= self.channels <= 8:
            raise ValueError("channels 必须在 1-8 范围内")
        if self.sample_width_bytes not in (1, 2, 3, 4):
            raise ValueError("sample_width_bytes 必须是 1、2、3 或 4")

    @property
    def bytes_per_sample_frame(self) -> int:
        """返回所有声道一个 PCM frame 所占的字节数。"""

        return self.channels * self.sample_width_bytes


@dataclass(frozen=True)
class PcmBufferStats:
    """缓冲区运行统计。"""

    pushed_chunks: int
    popped_chunks: int
    dropped_chunks: int
    queued_chunks: int
    closed: bool


class PcmFrameQueue:
    """固定容量 PCM chunk 队列，满时丢弃最旧数据避免无限增长。"""

    def __init__(self, max_chunks: int = 32) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks 必须大于 0")
        self._max_chunks = max_chunks
        self._chunks: deque[bytes] = deque()
        self._condition = threading.Condition()
        self._pushed = 0
        self._popped = 0
        self._dropped = 0
        self._closed = False

    def push(self, chunk: bytes) -> bool:
        """加入 PCM chunk；返回是否成功保留该 chunk。"""

        if not isinstance(chunk, bytes):
            raise TypeError("PCM chunk 必须是 bytes")
        if not chunk:
            return True
        with self._condition:
            if self._closed:
                return False
            self._pushed += 1
            if len(self._chunks) >= self._max_chunks:
                self._chunks.popleft()
                self._dropped += 1
            self._chunks.append(chunk)
            self._condition.notify()
            return True

    def pop(self, timeout: float | None = None) -> bytes | None:
        """取出最早的 PCM chunk；关闭且排空后返回 None。"""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout 不能为负数")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._chunks and not self._closed:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if not self._chunks:
                return None
            self._popped += 1
            return self._chunks.popleft()

    def close(self, *, discard: bool = False) -> None:
        """关闭队列；可选地丢弃尚未消费的 PCM。"""

        with self._condition:
            self._closed = True
            if discard:
                self._chunks.clear()
            self._condition.notify_all()

    @property
    def stats(self) -> PcmBufferStats:
        """返回线程安全的统计快照。"""

        with self._condition:
            return PcmBufferStats(
                pushed_chunks=self._pushed,
                popped_chunks=self._popped,
                dropped_chunks=self._dropped,
                queued_chunks=len(self._chunks),
                closed=self._closed,
            )


__all__ = ["PcmBufferStats", "PcmFormat", "PcmFrameQueue"]
