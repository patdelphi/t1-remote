"""程序说明：运行 T1 Key Mapping MVP 的本机测试入口。

默认配置会启动 T1 驱动拦截并调用 Windows SendInput。Power、Voice 和未知
Usage 不会产生系统输出；按 Ctrl+C 退出时会释放活动按键、停止租约并关闭会话。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import threading

from t1remote.core.capture_scope import collection_from_device_path, is_t1_device_path
from t1remote.core.key_mapping import (
    MappingConfig,
    MappingConfigError,
    MappingEngine,
    load_mapping_config,
)
from t1remote.core.mapping_watch import MappingConfigWatcher
from t1remote.windows.driver_bridge import (
    BridgeError,
    T1BridgeClient,
    build_default_interception_policy,
)
from t1remote.windows.mapping_runtime import MappingRuntimeError, T1MappingRuntime
from t1remote.windows.raw_input import RawInputEvent, RawInputListener
from t1remote.windows.send_input import KeyboardOutput, WindowsInputEmitter
from t1remote.windows.single_instance import SingleInstanceGuard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "t1-key-mapping.json"


class _LockedEmitter:
    """串行提交键盘输出，避免 Raw Input 和驱动线程交错快捷键。"""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self._lock = threading.Lock()

    def emit(self, outputs: tuple[KeyboardOutput, ...]) -> None:
        with self._lock:
            self._delegate.emit(outputs)  # type: ignore[attr-defined]


class _DryRunEmitter:
    """只打印输出，不调用 SendInput，供配置检查使用。"""

    def emit(self, outputs: tuple[KeyboardOutput, ...]) -> None:
        if outputs:
            text = ", ".join(
                f"VK=0x{item.virtual_key:02X}/flags=0x{item.flags:02X}"
                for item in outputs
            )
            print(f"[dry-run] 输出：{text}")


class _DryRunCommandExecutor:
    """只打印命令，不启动外部进程。"""

    def run(self, argv: tuple[str, ...]) -> None:
        print(f"[dry-run] 命令：{argv}")


def _load_config(path: Path) -> MappingConfig:
    """读取用户配置；文件缺失时使用代码内安全默认值。"""

    if not path.exists():
        print(f"[配置] 未找到 {path}，使用安全默认映射")
        return MappingConfig.default()
    try:
        config = load_mapping_config(path)
    except MappingConfigError as error:
        raise SystemExit(f"[配置错误] {error}") from error
    print(f"[配置] 已加载：{path}")
    return config


def _print_mapping_events(events: tuple[object, ...]) -> None:
    """打印已产生输出的语义动作。"""

    for event in events:
        print(f"[映射] {event.button} {event.state} -> {event.action.kind}:{event.action.key}")  # type: ignore[attr-defined]


def _print_diagnostics(runtime: T1MappingRuntime) -> None:
    """打印会话计数和最近脱敏事件。"""

    snapshot = runtime.diagnostics
    print(
        "[诊断] "
        f"输入={snapshot.input_events}，忽略={snapshot.ignored_inputs}，"
        f"映射={snapshot.mapping_events}，输出={snapshot.output_events}，"
        f"命令={snapshot.command_events}，错误={snapshot.errors}"
    )
    for record in snapshot.recent_events[-10:]:
        print(
            f"[最近] {record.timestamp_local} {record.result} "
            f"{record.button or '-'} {record.state} {record.action_kind or '-'}"
        )


def run(config_path: Path, dry_run: bool = False) -> int:
    """启动并运行 Key Mapping 测试会话。"""

    config = _load_config(config_path)
    delegate = _DryRunEmitter() if dry_run else WindowsInputEmitter()
    emitter = _LockedEmitter(delegate)
    command_executor = _DryRunCommandExecutor() if dry_run else None
    runtime = T1MappingRuntime(
        emitter=emitter,
        engine=MappingEngine(config),
        command_executor=command_executor,
    )
    bridge = T1BridgeClient()
    bridge_lock = threading.Lock()
    stop_requested = threading.Event()
    worker_threads: list[threading.Thread] = []
    raw_listener: RawInputListener | None = None

    def reload_mapping_config(replacement: MappingConfig) -> None:
        """热加载有效配置，并先释放旧配置的活动输出。"""

        releases = runtime.reload(replacement)
        _print_mapping_events(releases)
        print(f"[配置] 已热加载：{config_path}")

    def report_error(prefix: str, error: Exception) -> None:
        print(f"[{prefix}错误] {error}")

    instance_guard = SingleInstanceGuard("Local\\T1Remote.MappingSession")
    if not instance_guard.acquire():
        print("[启动错误] 已有一个 T1 Mapping 会话正在运行")
        return 1
    config_watcher = MappingConfigWatcher(
        config_path,
        on_reload=reload_mapping_config,
        on_error=lambda error: report_error("配置监视", error),
    )

    def handle_raw_event(event: RawInputEvent) -> None:
        if not is_t1_device_path(event.device_path):
            return
        collection = collection_from_device_path(event.device_path)
        # COL02/COL03 由过滤驱动队列提供，避免与 Raw Input 重复输出。
        if collection != "COL01":
            return
        try:
            mapped = runtime.process_report(collection, event.raw_input_type, event.raw_data)
            _print_mapping_events(mapped)
        except MappingRuntimeError as error:
            report_error("映射", error)
            stop_requested.set()

    def read_driver_events() -> None:
        while not stop_requested.is_set():
            try:
                with bridge_lock:
                    event = bridge.read_event()
                if event is None:
                    _print_mapping_events(runtime.poll())
                    stop_requested.wait(0.03)
                    continue
                mapped = runtime.process_report(
                    event.collection,
                    2,
                    event.report,
                    usage_page=event.usage_page,
                    usage=event.usage,
                )
                _print_mapping_events(mapped)
            except (BridgeError, MappingRuntimeError) as error:
                report_error("驱动/映射", error)
                stop_requested.set()

    def heartbeat() -> None:
        while not stop_requested.wait(1.0):
            try:
                with bridge_lock:
                    bridge.heartbeat()
            except BridgeError as error:
                report_error("心跳", error)
                stop_requested.set()

    try:
        bridge.open(build_default_interception_policy())
        bridge.start()
        with bridge_lock:
            bridge.heartbeat()
        status = bridge.status()
        print(
            f"[驱动] {status.state} | COL 掩码={status.attached_collections} | "
            f"租约={'有效' if status.lease_active else '无效'}"
        )
        if dry_run:
            print("[模式] dry-run：不会调用 SendInput")
        else:
            print("[模式] 实际输出：会调用 SendInput")

        raw_listener = RawInputListener(
            on_event=handle_raw_event,
            on_error=lambda error: report_error("Raw Input", error),
        )
        raw_listener.start()
        config_watcher.start()
        worker_threads = [
            threading.Thread(target=read_driver_events, name="t1-mapping-driver", daemon=True),
            threading.Thread(target=heartbeat, name="t1-mapping-heartbeat", daemon=True),
        ]
        for thread in worker_threads:
            thread.start()
        print("[运行] 请操作 T1 遥控器；Ctrl+C 退出")
        while not stop_requested.wait(0.5):
            pass
    except (BridgeError, OSError, RuntimeError) as error:
        report_error("启动", error)
        return 1
    except KeyboardInterrupt:
        print("\n[退出] 收到 Ctrl+C")
    finally:
        stop_requested.set()
        if raw_listener:
            try:
                raw_listener.stop()
            except Exception as error:
                report_error("Raw Input 停止", error)
        config_watcher.stop()
        for thread in worker_threads:
            thread.join(timeout=2)
        try:
            runtime.reset()
        except MappingRuntimeError as error:
            report_error("按键释放", error)
        _print_diagnostics(runtime)
        try:
            with bridge_lock:
                if bridge.is_open:
                    bridge.stop()
                    bridge.close()
        except BridgeError as error:
            report_error("驱动停止", error)
        instance_guard.release()
    return 0


def main() -> int:
    """解析命令行参数并启动测试会话。"""

    parser = argparse.ArgumentParser(description="运行 T1 Key Mapping MVP 测试")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="映射 JSON 配置路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印输出，不调用 Windows SendInput",
    )
    args = parser.parse_args()
    return run(args.config, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
