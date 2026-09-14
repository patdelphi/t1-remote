"""程序说明：封装 T1 Key Mapping 的桥接、Raw Input、热加载和生命周期。

该模块为命令行入口和前台监视器提供同一套会话行为：启动时校验配置并建立
驱动租约，运行中合并 COL01 与 COL02/COL03 输入，停止或异常时释放活动输出。
依赖通过构造器注入，核心生命周期可以在没有真实 T1 设备的情况下测试。
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
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
from t1remote.windows.hid_descriptor import (
    HidCollectionInfo,
    describe_input_data,
    inspect_preparsed_data,
    parse_input_data,
)
from t1remote.windows.hid_input import HidInputListener
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
from t1remote.windows.send_input import OutputEvent, WindowsInputEmitter
from t1remote.windows.single_instance import SingleInstanceGuard


MAPPING_POLL_INTERVAL_SECONDS = 0.01


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


class HidListenerLike(Protocol):
    """为 COL02/COL03 提供持续 ReadFile 请求的监听器最小接口。"""

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

    def emit(self, _outputs: tuple[OutputEvent, ...]) -> None:
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
        hid_listener_factory: Callable[..., HidListenerLike] = HidInputListener,
        instance_name: str = "Local\\T1Remote.MappingSession",
    ) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self._on_log = on_log
        self._on_error = on_error
        self._bridge_factory = bridge_factory
        self._raw_listener_factory = raw_listener_factory
        self._hid_listener_factory = hid_listener_factory
        self._instance_name = instance_name
        self._state = "stopped"
        self._message = "未启动"
        self._driver_state: str | None = None
        self._attached_collections = 0
        self._lease_active = False
        self._state_lock = threading.RLock()
        # 启动和停止涉及同一组句柄，必须避免在启动尚未完成时并发停止。
        self._lifecycle_lock = threading.RLock()
        # stop()、工作线程故障清理和启动失败可能并发进入；串行化资源释放，
        # 避免两个线程同时关闭同一个桥接句柄或释放单实例互斥体。
        self._cleanup_lock = threading.Lock()
        self._bridge_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        # 驱动使用 KeQueryInterruptTime()，这里换算到 Python monotonic 时钟，
        # 使队列中连续到达的按下/抬起事件仍保留真实间隔。
        self._driver_clock_offset: float | None = None
        self._bridge: BridgeLike | None = None
        self._hid_parser_data: dict[str, tuple[bytes, HidCollectionInfo]] = {}
        self._raw_listener: RawListenerLike | None = None
        self._hid_listener: HidListenerLike | None = None
        self._watcher: MappingConfigWatcher | None = None
        self._runtime: T1MappingRuntime | None = None
        self._instance_guard: SingleInstanceGuard | None = None
        self._diagnostics = MappingDiagnostics()

    def start(self, config: MappingConfig | None = None) -> None:
        """串行化启动入口，防止 stop() 抢先释放尚未完成的资源。"""

        with self._lifecycle_lock:
            self._start(config)

    def _start(self, config: MappingConfig | None = None) -> None:
        """校验配置并启动驱动、输入监听、热加载和心跳线程。"""

        with self._state_lock:
            if self._state in {"starting", "running", "stopping"}:
                raise MappingSessionError(f"会话当前状态不允许启动：{self._state}")
            self._state = "starting"
            self._message = "正在启动"
        try:
            # GUI 可以在启动前先读取并校验配置，传入后避免重复读取同一文件。
            config = config if config is not None else load_mapping_config(self.config_path)
            self._diagnostics = MappingDiagnostics()
            self._driver_clock_offset = None
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
            bridge.open(
                build_default_interception_policy(
                    enabled=not self.dry_run,
                    # 常驻拦截：策略写入驱动并在下次启动时加载，不依赖 App 租约。
                    lease_required=False,
                )
            )
            # 新会话不消费上一次会话遗留的零报告或按下报告，避免旧队列
            # 与当前物理按键状态拼成一对错误的 Voice up。
            flush_events = getattr(bridge, "flush_events", None)
            if callable(flush_events):
                flush_events()
            if not self.dry_run:
                self._prime_hid_parser(bridge)
            bridge.start()
            with self._bridge_lock:
                bridge.heartbeat()
                self._update_driver_status(bridge.status())
            if not self.dry_run:
                # 过滤驱动在 HID ReadFile 完成时才会看到 COL02/COL03 报告。
                # 这里仅建立读请求，实际报文统一由 bridge.read_event() 消费，
                # 避免直读回调和桥接队列重复触发一次映射。
                self._hid_listener = self._hid_listener_factory(
                    on_event=lambda _event: None,
                    on_error=self._report_error,
                    target_collections=("COL02", "COL03"),
                )
                self._hid_listener.start()
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
                # 工作线程可能在这里之前就发现输出故障；不能把 error 覆盖成 running。
                if self._state == "starting":
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
        """串行化停止入口，避免与启动流程交叉。"""

        with self._lifecycle_lock:
            self._stop()

    def _stop(self) -> None:
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

    def reload_config(self) -> None:
        """手动读取当前配置并切换运行时映射。"""

        with self._state_lock:
            if self._state != "running" or self._runtime is None:
                raise MappingSessionError("Mapping 会话未运行，无法重新加载配置")
            runtime = self._runtime

        # 复用运行时的 reload 逻辑，先释放旧动作，避免留下粘键或正在执行的宏。
        config = load_mapping_config(self.config_path)
        runtime.reload(config)
        self._log(f"映射配置已重新加载：{self.config_path}")

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
        with self._state_lock:
            if self._state != "running" or self._runtime is None:
                return
            runtime = self._runtime
        runtime.reload(config)
        self._log(f"映射配置已热加载：{self.config_path}")

    def _handle_raw_event(self, event: RawInputEvent) -> None:
        if not is_t1_device_path(event.device_path):
            return
        collection = collection_from_device_path(event.device_path)
        with self._state_lock:
            runtime = self._runtime if self._state == "running" else None
        if collection != "COL01" or runtime is None:
            return
        if not self.dry_run:
            # 正式会话中 COL01 已由 Bridge 驱动事件提供；Raw Input 仅保留
            # 设备/电源通知，不能再次把同一按键送入 Mapping。
            return
        try:
            self._log_mapping_events(
                runtime.process_report(collection, event.raw_input_type, event.raw_data)
            )
        except MappingRuntimeError as error:
            self._fail_from_worker(error)

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
                with self._bridge_lock:
                    bridge = self._bridge
                    if bridge:
                        bridge.heartbeat()
                        self._update_driver_status(bridge.status())
            except BridgeError as error:
                self._fail_from_worker(error)

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
                runtime = self._runtime
                if not runtime:
                    return
                with self._bridge_lock:
                    bridge = self._bridge
                    if not bridge:
                        return
                    event = bridge.read_event()
                if event is None:
                    self._log_mapping_events(runtime.poll())
                    self._stop_event.wait(MAPPING_POLL_INTERVAL_SECONDS)
                    continue
                parser_usage = self._parser_usage_for_event(event)
                usage_page = (
                    parser_usage[0]
                    if parser_usage is not None
                    else event.usage_page
                )
                usage = parser_usage[1] if parser_usage is not None else event.usage
                self._log_mapping_events(
                    runtime.process_report(
                        event.collection,
                        2,
                        event.report,
                        usage_page=usage_page,
                        usage=usage,
                        now=self._driver_event_time(
                            getattr(event, "timestamp_100ns", 0)
                        ),
                    )
                )
            except (BridgeError, MappingRuntimeError) as error:
                self._fail_from_worker(error)

    def _driver_event_time(self, timestamp_100ns: object) -> float | None:
        """把驱动的 100ns 时间戳转换到 Python monotonic 时钟。"""

        try:
            timestamp = int(timestamp_100ns)
        except (TypeError, ValueError):
            return None
        if timestamp <= 0:
            return None
        timestamp_seconds = timestamp / 10_000_000
        if self._driver_clock_offset is None:
            self._driver_clock_offset = time.monotonic() - timestamp_seconds
        return timestamp_seconds + self._driver_clock_offset

    def _prime_hid_parser(self, bridge: BridgeLike) -> None:
        """取得 opaque preparsed data，让新驱动缓存官方 HID parser 输入。"""

        getter = getattr(bridge, "get_preparsed_data", None)
        self._hid_parser_data = {}
        if not callable(getter):
            raise MappingSessionError("驱动缺少 HID parser 接口，未启用过滤")
        parser_data = {}
        # 键盘集合（COL01）也加载 parser：有 parser 时驱动用官方解析结果，
        # 无 parser 时驱动有 boot 布局解码兜底，所以它的缺失不阻断会话。
        for collection in ("COL02", "COL03", "COL01"):
            try:
                preparsed_data = bytes(getter(collection))
                if not preparsed_data:
                    raise ValueError("preparsed data 为空")
                info = inspect_preparsed_data(
                    preparsed_data,
                    collection=collection,
                )
            except (BridgeError, OSError, RuntimeError, TypeError, ValueError) as error:
                if collection == "COL01":
                    self._log(
                        f"{collection} HID parser 不可用，改用驱动内置键盘解码：{error}"
                    )
                    continue
                raise MappingSessionError(
                    f"{collection} HID parser 不可用，未启用过滤：{error}"
                ) from error
            parser_data[collection] = (preparsed_data, info)
            self._log(
                f"{collection} HID parser 已启用："
                f"{len(info.input_button_capabilities)} 个输入按钮能力"
            )
        # 完整准备后一次替换，工作线程不会看到半初始化缓存。
        self._hid_parser_data = parser_data

    def _parser_usage_for_event(self, event: object) -> tuple[int, int] | None:
        """仅在 parser 得到唯一 Usage 时替换驱动的兼容解码结果。"""

        collection = str(getattr(event, "collection", "")).upper()
        parser_data = self._hid_parser_data.get(collection)
        if parser_data is None:
            return None
        preparsed_data, info = parser_data
        try:
            input_data = parse_input_data(
                preparsed_data,
                bytes(getattr(event, "report", b"")),
            )
            descriptions = describe_input_data(
                input_data,
                info.input_button_capabilities,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        matches = {
            (match.usage_page, match.usage)
            for description in descriptions
            for match in description.button_matches
        }
        if len(matches) != 1:
            return None
        return next(iter(matches))

    def _fail_from_worker(self, error: Exception) -> None:
        """标记运行时故障并异步释放驱动，避免界面继续显示 running。"""

        with self._state_lock:
            if self._state in {"stopped", "stopping", "error"}:
                return
            self._state = "error"
            self._message = str(error)
        self._report_error(error)
        threading.Thread(
            target=self._cleanup_after_worker_failure,
            name="t1-mapping-failure-cleanup",
            daemon=True,
        ).start()

    def _cleanup_after_worker_failure(self) -> None:
        """在独立线程中清理故障会话，避免从 Raw Input 线程自连接。"""

        # 故障清理也必须与下一次 start() 串行，避免清理线程释放新会话的句柄。
        with self._lifecycle_lock:
            self._cleanup()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                with self._bridge_lock:
                    bridge = self._bridge
                    if not bridge:
                        return
                    bridge.heartbeat()
                    status = bridge.status()
                    # 蓝牙 HID 重连后，设备可能已经重新附着，但旧租约对应的
                    # 过滤状态仍为 stopped；重新 start 才能让 Mapping 继续收报告。
                    if (
                        not self.dry_run
                        and getattr(status, "state", "") == "stopped"
                        and int(getattr(status, "attached_collections", 0)) != 0
                    ):
                        self._prime_hid_parser(bridge)
                        bridge.start()
                        status = bridge.status()
                        self._log("T1 HID 已重连，Mapping 过滤器已自动恢复")
                    self._update_driver_status(status)
            except (BridgeError, MappingSessionError) as error:
                self._fail_from_worker(error)

    def _log_mapping_events(self, events: tuple[object, ...]) -> None:
        for event in events:
            trigger = getattr(getattr(event, "action", None), "trigger", None)
            trigger_kind = getattr(trigger, "kind", "press")
            state = trigger_kind if event.state == "down" and trigger_kind != "press" else event.state
            self._log(
                f"映射 {event.button} {state} -> "
                f"{event.action.kind}:{event.action.key}"
            )

    def _update_driver_status(self, status: object) -> None:
        self._driver_state = str(getattr(status, "state", "unknown"))
        self._attached_collections = int(getattr(status, "attached_collections", 0))
        self._lease_active = bool(getattr(status, "lease_active", False))

    def _cleanup(self) -> None:
        """清理所有已部分启动的资源；每个边界单独处理异常。"""

        with self._cleanup_lock:
            self._stop_event.set()
            if self._raw_listener:
                try:
                    self._raw_listener.stop()
                except Exception as error:
                    self._report_error(error)
            if self._hid_listener:
                try:
                    self._hid_listener.stop()
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
                self._runtime = None
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
            self._hid_parser_data.clear()
            self._raw_listener = None
            self._hid_listener = None
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
