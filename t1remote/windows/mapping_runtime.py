"""程序说明：串联 T1 报文解码、Key Mapping 状态机和 SendInput 输出。"""

from __future__ import annotations

from typing import Protocol

from t1remote.core.input_mapping import ButtonEvent, T1InputDecoder
from t1remote.core.key_mapping import MappingConfig, MappingEngine, MappingEvent
from t1remote.windows.command_runner import WindowsCommandExecutor
from t1remote.windows.send_input import KeyboardOutput, build_mapping_output_events


class OutputEmitter(Protocol):
    """键盘输出器的最小协议，便于使用假对象测试。"""

    def emit(self, outputs: tuple[KeyboardOutput, ...]) -> None:
        """提交一批键盘输出事件。"""


class CommandExecutor(Protocol):
    """命令行动作执行器的最小协议。"""

    def run(self, argv: tuple[str, ...]) -> None:
        """启动一条命令。"""


class MappingRuntimeError(RuntimeError):
    """Key Mapping 运行时输出失败。"""


class T1MappingRuntime:
    """处理一条 T1 输入到系统输出的完整用户态链路。"""

    def __init__(
        self,
        emitter: OutputEmitter,
        *,
        decoder: T1InputDecoder | None = None,
        engine: MappingEngine | None = None,
        command_executor: CommandExecutor | None = None,
    ) -> None:
        self._emitter = emitter
        self._decoder = decoder or T1InputDecoder()
        self._engine = engine or MappingEngine()
        self._command_executor = command_executor or WindowsCommandExecutor()

    @property
    def config(self) -> MappingConfig:
        """返回当前生效的映射配置。"""

        return self._engine.config

    def process_report(
        self,
        collection: str,
        raw_input_type: int,
        report: bytes,
        *,
        usage_page: int | None = None,
        usage: int | None = None,
    ) -> tuple[MappingEvent, ...]:
        """解码并输出一条 Raw Input 或驱动报告。"""

        input_event = self._decoder.feed(
            collection,
            raw_input_type,
            report,
            usage_page=usage_page,
            usage=usage,
        )
        return self.process_button_event(input_event)

    def process_button_event(self, event: ButtonEvent) -> tuple[MappingEvent, ...]:
        """处理已经解码的语义按键事件。"""

        mapping_events = self._engine.handle(event)
        self._emit_mapping_events(mapping_events)
        return mapping_events

    def reload(self, config: MappingConfig) -> tuple[MappingEvent, ...]:
        """切换配置并先释放旧配置产生的活动输出。"""

        releases = self._engine.reload(config)
        self._emit_mapping_events(releases)
        return releases

    def reset(self) -> tuple[MappingEvent, ...]:
        """设备断开或程序退出时释放活动输出并清空解码状态。"""

        releases = self._engine.reset()
        self._decoder.reset()
        self._emit_mapping_events(releases)
        return releases

    def _emit_mapping_events(self, events: tuple[MappingEvent, ...]) -> None:
        """把状态机事件转换并提交给输出器。"""

        for event in events:
            try:
                if event.action.kind == "command":
                    # 命令是瞬时动作，只在按下时执行，抬起只负责结束状态机。
                    if event.state == "down":
                        self._command_executor.run(event.action.argv)
                    continue
                outputs = build_mapping_output_events(event)
                self._emitter.emit(outputs)
            except (OSError, RuntimeError, ValueError) as exc:
                raise MappingRuntimeError(
                    f"输出按键动作失败：{event.button}/{event.action.kind}"
                ) from exc


__all__ = [
    "CommandExecutor",
    "MappingRuntimeError",
    "OutputEmitter",
    "T1MappingRuntime",
]
