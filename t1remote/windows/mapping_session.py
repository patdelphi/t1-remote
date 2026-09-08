"""程序说明：封装 T1 Key Mapping 的桥接、Raw Input、热加载和生命周期。

该模块为命令行入口和前台监视器提供同一套会话行为：启动时校验配置并建立
驱动租约，运行中合并 COL01 与 COL02/COL03 输入，停止或异常时释放活动输出。
依赖通过构造器注入，核心生命周期可以在没有真实 T1 设备的情况下测试。
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, Protocol

from t1remote.core.capture_scope import collection_from_device_path, is_t1_device_path
from t1remote.core.key_mapping import (
    MappingConfig,
    MappingEngine,
    load_mapping_config,
)
from t1remote.core.mapping_diagnostics import DiagnosticSnapshot, MappingDiagnostics
from t1remote.core.mapping_watch import MappingConfigWatcher
from t1remote.windows.driver_bridge import (
    BridgeError,
    T1BridgeClient,
    build_default_interception_policy,
)
from t1remote.windows.mapping_runtime import MappingRuntimeError, T1MappingRuntime
from t1remote.windows.raw_input import (
    GIDC_ARRIVAL,
    GIDC_REMOVAL,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    PBT_APMSUSPEND,
    RawInputEvent,
    RawInputListener,
)
from t1remote.windows.send_input import KeyboardOutput, WindowsInputEmitter
from t1remote.windows.single_instance import SingleInstanceGuard


class BridgeLike(Protocol):
    """会话依赖的桥接客户端最小接口。"""

    is_open: bool

    def open(self, policy: object) -> None: ...

    def start(self) -> None: ...

    def heartbeat(self) -> None: ...

    def status(self) -> object: ...

    def read_event(self) -> object | None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class RawListenerLike(Protocol):
    """会话依赖的 Raw Input 监听器最小接口。"""

    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class MappingSessionStatus:
    """前台显示的会话和驱动状态。"""

    state: str
    message: str
    driver_state: str | None
    attached_collections: int
    lease_active: bool
    diagnostics: DiagnosticSnapshot


class MappingSessionError(RuntimeError):
    """T1 Mapping 会话启动、运行或停止失败。"""


class _DryRunEmitter:
    """测试模式输出器，不调用 Windows SendInput。"""

    def emit(self, _outputs: tuple[KeyboardOutput, ...]) -> None:
        pass


class _DryRunCommandExecutor:
    """测试模式命令执行器，不启动外部程序。"""

    def run(self, _argv: tuple[str, ...]) -> None:
        pass


class T1MappingSession:
    """管理一个完整的 T1 Mapping 运行会话。"""

    def __init__(
        self,
        config_path: str,
        *,
        dry_run: bool = False,
        on_log: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        bridge_factory: Callable[[], BridgeLike] = T1BridgeClient,
        raw_listener_factory: Callable[..., RawListenerLike] = RawInputListener,
        instance_name: str = "Local\\T1Remote.MappingSession",
    ) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self._on_log = on_log
        self._on_error = on_error
        self._bridge_factory = bridge_factory
        self._raw_listener_factory = raw_listener_factory
        self._instance_name = instance_name
        self._state = "stopped"
        self._message = "未启动"
        self._driver_state: str | None = None
        self._attached_collections = 0
        self._lease_active = False
        self._state_lock = threading.RLock()
        self._bridge_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._bridge: BridgeLike | None = None
        self._raw_listener: RawListenerLike | None = None
        self._watcher: MappingConfigWatcher | None = None
        self._runtime: T1MappingRuntime | None = None
        self._instance_guard: SingleInstanceGuard | None = None
        self._diagnostics = MappingDiagnostics()

    def start(self) -> None:
        """校验配置并启动驱动、输入监听、热加载和心跳线程。"""

        with self._state_lock:
            if self._state in {"starting", "running", "stopping"}:
                raise MappingSessionError(f"会话当前状态不允许启动：{self._state}")
            self._state = "starting"
            self._message = "正在启动"
        try:
            config = load_mapping_config(self.config_path)
            self._diagnostics = MappingDiagnostics()
            emitter = _DryRunEmitter() if self.dry_run else WindowsInputEmitter()
            command_executor = _DryRunCommandExecutor() if self.dry_run else None
            # 使用加载后的配置构造独立状态机，避免启动后再次读取文件。
            self._runtime = T1MappingRuntime(
                emitter=emitter,
                engine=MappingEngine(config),
                command_executor=command_executor,
                diagnostics=self._diagnostics,
            )
            self._instance_guard = SingleInstanceGuard(self._instance_name)
            if not self._instance_guard.acquire():
                raise MappingSessionError("已有一个 T1 Mapping 会话正在运行")
            bridge = self._bridge_factory()
            self._bridge = bridge
            bridge.open(build_default_interception_policy())
            bridge.start()
            with self._bridge_lock:
                bridge.heartbeat()
                self._update_driver_status(bridge.status())
            self._raw_listener = self._raw_listener_factory(
                on_event=self._handle_raw_event,
                on_error=self._report_error,
                on_device_change=self._handle_device_change,
                on_power_event=self._handle_power_event,
            )
            self._raw_listener.start()
            self._watcher = MappingConfigWatcher(
                self.config_path,
                on_reload=self._reload_config,
                on_error=self._report_error,
            )
            self._watcher.start()
            self._stop_event.clear()
            self._worker_threads = [
                threading.Thread(
                    target=self._driver_loop,
                    name="t1-mapping-session-driver",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._heartbeat_loop,
                    name="t1-mapping-session-heartbeat",
                    daemon=True,
                ),
            ]
            for thread in self._worker_threads:
                thread.start()
            with self._state_lock:
                self._state = "running"
                self._message = "运行中"
            self._log("T1 Mapping 会话已启动")
        except Exception as error:
            self._report_error(error)
            self._cleanup()
            with self._state_lock:
                self._state = "error"
                self._message = str(error)
            if isinstance(error, MappingSessionError):
                raise
            raise MappingSessionError("T1 Mapping 会话启动失败") from error

    def stop(self) -> None:
        """停止监听、释放按键、关闭桥接租约并释放单实例。"""

        with self._state_lock:
            if self._state == "stopped":
                return
            self._state = "stopping"
            self._message = "正在停止"
        self._cleanup()
        with self._state_lock:
            self._state = "stopped"
            self._message = "已停止"
        self._log("T1 Mapping 会话已停止")

    def status(self) -> MappingSessionStatus:
        """返回前台可安全读取的会话快照。"""

        with self._state_lock:
            return MappingSessionStatus(
                state=self._state,
                message=self._message,
                driver_state=self._driver_state,
                attached_collections=self._attached_collections,
                lease_active=self._lease_active,
                diagnostics=self._diagnostics.snapshot(),
            )

    def _reload_config(self, config: MappingConfig) -> None:
        if not self._runtime:
            return
        self._runtime.reload(config)
        self._log(f"映射配置已热加载：{self.config_path}")

    def _handle_raw_event(self, event: RawInputEvent) -> None:
        if not is_t1_device_path(event.device_path):
            return
        collection = collection_from_device_path(event.device_path)
        if collection != "COL01" or not self._runtime:
            return
        try:
            self._log_mapping_events(
                self._runtime.process_report(collection, event.raw_input_type, event.raw_data)
            )
        except MappingRuntimeError as error:
            self._report_error(error)
            self._stop_event.set()

    def _handle_device_change(self, event_code: int) -> None:
        """设备到达或移除时清理活动按键，避免断连留下粘键。"""

        if event_code == GIDC_REMOVAL:
            self._reset_runtime("T1 HID 设备移除，已释放活动映射")
        elif event_code == GIDC_ARRIVAL:
            self._log("T1 HID 设备重新到达，等待输入报告")

    def _handle_power_event(self, event_code: int) -> None:
        """睡眠时释放状态，恢复时重新发送一次桥接心跳。"""

        if event_code == PBT_APMSUSPEND:
            self._reset_runtime("系统进入睡眠，已释放活动映射")
        elif event_code in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
            self._log("系统恢复，正在重新确认 T1 桥接状态")
            try:
                if self._bridge:
                    with self._bridge_lock:
                        self._bridge.heartbeat()
                        self._update_driver_status(self._bridge.status())
            except BridgeError as error:
                self._report_error(error)
                self._stop_event.set()

    def _reset_runtime(self, message: str) -> None:
        if not self._runtime:
            return
        try:
            self._runtime.reset()
            self._log(message)
        except MappingRuntimeError as error:
            self._report_error(error)
            self._stop_event.set()

    def _driver_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self._bridge or not self._runtime:
                    return
                with self._bridge_lock:
                    event = self._bridge.read_event()
                if event is None:
                    self._log_mapping_events(self._runtime.poll())
                    self._stop_event.wait(0.03)
                    continue
                self._log_mapping_events(
                    self._runtime.process_report(
                        event.collection,
                        2,
                        event.report,
                        usage_page=event.usage_page,
                        usage=event.usage,
                    )
                )
            except (BridgeError, MappingRuntimeError) as error:
                self._report_error(error)
                self._stop_event.set()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                if not self._bridge:
                    return
                with self._bridge_lock:
                    self._bridge.heartbeat()
                    self._update_driver_status(self._bridge.status())
            except BridgeError as error:
                self._report_error(error)
                self._stop_event.set()

    def _log_mapping_events(self, events: tuple[object, ...]) -> None:
        for event in events:
            self._log(
                f"映射 {event.button} {event.state} -> "
                f"{event.action.kind}:{event.action.key}"
            )

    def _update_driver_status(self, status: object) -> None:
        self._driver_state = str(getattr(status, "state", "unknown"))
        self._attached_collections = int(getattr(status, "attached_collections", 0))
        self._lease_active = bool(getattr(status, "lease_active", False))

    def _cleanup(self) -> None:
        """清理所有已部分启动的资源；每个边界单独处理异常。"""

        self._stop_event.set()
        if self._raw_listener:
            try:
                self._raw_listener.stop()
            except Exception as error:
                self._report_error(error)
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception as error:
                self._report_error(error)
        current_thread = threading.current_thread()
        for thread in self._worker_threads:
            if thread is not current_thread:
                thread.join(timeout=2)
        if self._runtime:
            try:
                self._runtime.reset()
            except MappingRuntimeError as error:
                self._report_error(error)
        if self._bridge:
            try:
                with self._bridge_lock:
                    if self._bridge.is_open:
                        self._bridge.stop()
                    self._bridge.close()
            except Exception as error:
                self._report_error(error)
        if self._instance_guard:
            self._instance_guard.release()
        self._worker_threads = []
        self._raw_listener = None
        self._watcher = None
        self._bridge = None
        self._instance_guard = None

    def _log(self, message: str) -> None:
        if self._on_log:
            try:
                self._on_log(message)
            except Exception as error:
                self._report_error(error)

    def _report_error(self, error: Exception) -> None:
        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                pass


__all__ = ["MappingSessionError", "MappingSessionStatus", "T1MappingSession"]
