"""程序说明：轮询映射 JSON 文件并在有效配置变化后通知运行时。

监视器只负责检测和加载文件，不决定如何应用配置。非法文件通过错误回调报告，
旧配置继续由调用方保持；同一份非法文件只报告一次，直到文件再次发生变化。
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import threading
from typing import Callable

from t1remote.core.key_mapping import MappingConfig, load_mapping_config


ConfigReloadCallback = Callable[[MappingConfig], None]
ConfigErrorCallback = Callable[[Exception], None]
FileSignature = tuple[int, bytes] | None


class MappingConfigWatcher:
    """以轻量轮询方式监视 Key Mapping 配置文件。"""

    def __init__(
        self,
        path: str | Path,
        on_reload: ConfigReloadCallback,
        on_error: ConfigErrorCallback,
        *,
        interval_seconds: float = 0.5,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("配置文件监视间隔必须大于 0")
        self.path = Path(path)
        self._on_reload = on_reload
        self._on_error = on_error
        self._interval_seconds = interval_seconds
        self._last_signature = self._signature()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def check_once(self) -> bool:
        """检查一次文件变化；返回是否发现了新文件状态。"""

        signature = self._signature()
        with self._lock:
            if signature == self._last_signature:
                return False
            self._last_signature = signature
        try:
            if signature is None:
                raise FileNotFoundError(f"映射配置文件不存在：{self.path}")
            config = load_mapping_config(self.path)
            self._on_reload(config)
        except Exception as error:
            self._on_error(error)
        return True

    def start(self) -> None:
        """启动后台监视线程；重复调用不会创建多个线程。"""

        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="t1-mapping-config-watch",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止后台监视线程。"""

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 3))
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self.check_once()

    def _signature(self) -> FileSignature:
        """返回文件的内容签名。

        Windows 上最后写入时间的更新可能被延迟，快速重写同一长度的配置会
        让 stat 签名保持不变，因此签名必须包含内容摘要而不是只依赖 stat。
        """

        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        return len(data), sha256(data).digest()


__all__ = ["MappingConfigWatcher"]
