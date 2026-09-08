"""程序说明：收集 Key Mapping 输入、输出和错误诊断信息。

诊断对象只保存有限数量的最近记录，不保存完整 HID 报文，避免日志中出现
设备路径或无关原始数据。运行时可读取不可变快照，前台线程不会直接访问内部状态。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import threading

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import MappingEvent


@dataclass(frozen=True)
class DiagnosticRecord:
    """一条脱敏的最近诊断记录。"""

    timestamp_local: str
    button: str | None
    state: str
    action_kind: str | None
    result: str
    detail: str = ""


@dataclass(frozen=True)
class DiagnosticSnapshot:
    """诊断计数和最近记录的不可变快照。"""

    input_events: int
    ignored_inputs: int
    mapping_events: int
    output_events: int
    command_events: int
    errors: int
    recent_events: tuple[DiagnosticRecord, ...]


class MappingDiagnostics:
    """线程安全的 Key Mapping 诊断收集器。"""

    def __init__(self, *, recent_limit: int = 100) -> None:
        if recent_limit <= 0:
            raise ValueError("最近事件数量必须大于 0")
        self._recent: deque[DiagnosticRecord] = deque(maxlen=recent_limit)
        self._input_events = 0
        self._ignored_inputs = 0
        self._mapping_events = 0
        self._output_events = 0
        self._command_events = 0
        self._errors = 0
        self._lock = threading.Lock()

    def record_input(self, event: ButtonEvent) -> None:
        """记录一个输入事件；未知或无绑定事件计入 ignored_inputs。"""

        ignored = event.button is None or event.state == "unknown"
        with self._lock:
            self._input_events += 1
            if ignored:
                self._ignored_inputs += 1
            self._recent.append(
                DiagnosticRecord(
                    timestamp_local=_timestamp(),
                    button=event.button,
                    state=event.state,
                    action_kind=None,
                    result="ignored" if ignored else "input",
                    detail=f"{event.collection}/{event.source}",
                )
            )

    def record_mapping(self, event: MappingEvent) -> None:
        """记录一个经过状态机的映射事件。"""

        with self._lock:
            self._mapping_events += 1
            self._recent.append(
                DiagnosticRecord(
                    timestamp_local=_timestamp(),
                    button=event.button,
                    state=event.state,
                    action_kind=event.action.kind,
                    result="mapping",
                )
            )

    def record_output(self, count: int) -> None:
        """记录实际提交给 SendInput 的事件数量。"""

        if count <= 0:
            return
        with self._lock:
            self._output_events += count
            self._recent.append(
                DiagnosticRecord(
                    timestamp_local=_timestamp(),
                    button=None,
                    state="output",
                    action_kind=None,
                    result="output",
                    detail=f"{count} keyboard events",
                )
            )

    def record_command(self) -> None:
        """记录一次命令行动作启动。"""

        with self._lock:
            self._command_events += 1
            self._recent.append(
                DiagnosticRecord(
                    timestamp_local=_timestamp(),
                    button=None,
                    state="down",
                    action_kind="command",
                    result="command",
                )
            )

    def record_error(self, error: Exception) -> None:
        """记录一次运行时错误的类型和短消息。"""

        with self._lock:
            self._errors += 1
            self._recent.append(
                DiagnosticRecord(
                    timestamp_local=_timestamp(),
                    button=None,
                    state="error",
                    action_kind=None,
                    result="error",
                    detail=f"{type(error).__name__}: {error}",
                )
            )

    def snapshot(self) -> DiagnosticSnapshot:
        """返回当前诊断快照。"""

        with self._lock:
            return DiagnosticSnapshot(
                input_events=self._input_events,
                ignored_inputs=self._ignored_inputs,
                mapping_events=self._mapping_events,
                output_events=self._output_events,
                command_events=self._command_events,
                errors=self._errors,
                recent_events=tuple(self._recent),
            )


def _timestamp() -> str:
    """生成带时区的本地时间戳。"""

    return datetime.now().astimezone().isoformat(timespec="milliseconds")


__all__ = ["DiagnosticRecord", "DiagnosticSnapshot", "MappingDiagnostics"]
