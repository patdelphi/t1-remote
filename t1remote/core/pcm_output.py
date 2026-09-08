"""程序说明：把有界 PCM 队列安全地泵送到可注入音频端点。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Protocol

from t1remote.core.audio_buffer import PcmFrameQueue
from t1remote.core.pcm_sink import PcmSinkError


class PcmSink(Protocol):
    """PCM 输出端点的最小接口。"""

    def write(self, chunk: bytes) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class PcmSinkWorkerSnapshot:
    """PCM 输出泵的运行统计。"""

    running: bool
    chunks_written: int
    bytes_written: int
    error: str | None


class PcmSinkWorker:
    """在后台线程中消费 PCM 队列并写入端点。"""

    def __init__(
        self,
        queue: PcmFrameQueue,
        sink: PcmSink,
        *,
        pop_timeout: float = 0.1,
    ) -> None:
        if pop_timeout <= 0:
            raise ValueError("pop_timeout 必须大于 0")
        self._queue = queue
        self._sink = sink
        self._pop_timeout = pop_timeout
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._chunks_written = 0
        self._bytes_written = 0
        self._error: Exception | None = None
        self._sink_closed = False

    @property
    def snapshot(self) -> PcmSinkWorkerSnapshot:
        """返回线程安全的输出统计。"""

        with self._state_lock:
            thread = self._thread
            error = self._error
            return PcmSinkWorkerSnapshot(
                running=bool(thread and thread.is_alive()),
                chunks_written=self._chunks_written,
                bytes_written=self._bytes_written,
                error=str(error) if error is not None else None,
            )

    def start(self) -> None:
        """启动输出线程；同一实例只能启动一次。"""

        with self._state_lock:
            if self._thread is not None:
                raise PcmSinkError("PCM 输出泵不能重复启动")
            self._thread = threading.Thread(
                target=self._run,
                name="t1-pcm-sink",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, discard: bool = False, timeout: float = 5.0) -> None:
        """关闭队列、等待输出线程并关闭端点。"""

        if timeout < 0:
            raise ValueError("timeout 不能为负数")
        self._queue.close(discard=discard)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise PcmSinkError("PCM 输出线程未能在限定时间内停止")
        self._close_sink()
        if self._error is not None:
            raise PcmSinkError("PCM 输出线程失败") from self._error

    def _run(self) -> None:
        """消费队列；关闭且排空后自然退出。"""

        while True:
            chunk = self._queue.pop(timeout=self._pop_timeout)
            if chunk is None:
                if self._queue.stats.closed:
                    return
                continue
            try:
                self._sink.write(chunk)
            except Exception as error:
                with self._state_lock:
                    self._error = error
                self._queue.close(discard=True)
                return
            with self._state_lock:
                self._chunks_written += 1
                self._bytes_written += len(chunk)

    def _close_sink(self) -> None:
        """关闭端点并保证重复 stop 不重复关闭。"""

        with self._state_lock:
            if self._sink_closed:
                return
            self._sink_closed = True
        try:
            self._sink.close()
        except Exception as error:
            raise PcmSinkError("关闭 PCM 输出端点失败") from error


__all__ = ["PcmSink", "PcmSinkWorker", "PcmSinkWorkerSnapshot"]
