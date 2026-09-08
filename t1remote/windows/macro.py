"""程序说明：按顺序执行只包含键位和间隔的 T1 键盘宏。

宏步骤只发送虚拟键的按下/抬起事件，不解析脚本，也不启动外部命令。
执行期间保留取消入口，避免配置热加载、设备断开或应用退出后留下按键状态。
"""

from __future__ import annotations

from collections.abc import Callable
import threading

from t1remote.core.key_mapping import MacroStep
from t1remote.windows.send_input import KeyboardOutput, build_macro_step_events


class MacroExecutorError(RuntimeError):
    """键盘宏执行失败。"""


class KeyboardMacroExecutor:
    """在后台串行执行宏，支持新宏替换和安全取消。"""

    def __init__(
        self,
        emit: Callable[[tuple[KeyboardOutput, ...]], None],
        *,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._emit = emit
        self._on_error = on_error
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def run(self, steps: tuple[MacroStep, ...]) -> None:
        """替换当前宏并在后台开始执行。"""

        self.stop()
        self._cancel.clear()
        thread = threading.Thread(
            target=self._run,
            args=(steps, self._cancel),
            name="t1-mapping-macro",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        """取消当前宏并等待其完成当前键击的抬起。"""

        self._cancel.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(
        self,
        steps: tuple[MacroStep, ...],
        cancel: threading.Event,
    ) -> None:
        try:
            for step in steps:
                if cancel.is_set():
                    return
                down, up = build_macro_step_events(step)
                self._emit(down)
                # 即使取消发生在按下之后，也必须释放本步骤，避免粘键。
                self._emit(up)
                if step.delay_ms and cancel.wait(step.delay_ms / 1000):
                    return
        except Exception as error:
            if self._on_error is not None:
                wrapped = MacroExecutorError("键盘宏执行失败")
                wrapped.__cause__ = error
                self._on_error(wrapped)


__all__ = ["KeyboardMacroExecutor", "MacroExecutorError"]
