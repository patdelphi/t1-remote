"""程序说明：采集 T1 COL02/COL03 过滤驱动队列中的原始 HID 事件。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    validate_button_number,
)
from t1remote.windows.driver_bridge import (
    BridgeError,
    DriverInputEvent,
    T1BridgeClient,
    build_default_interception_policy,
)
from t1remote.windows.single_instance import SingleInstanceGuard


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    """生成可排序的 UTC 时间戳。"""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _report_state(collection: str, report: bytes) -> str:
    """依据当前已确认的 COL02/COL03 报文布局标记按下或释放。"""

    if len(report) < 2:
        return "unknown"
    if collection.upper() == "COL03":
        return "down" if report[1] else "up"
    return "down" if any(report[1:]) else "up"


def serialize_driver_event(
    event: DriverInputEvent,
    button: str,
    *,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """转换驱动事件为不包含设备路径的 JSON 记录。"""

    collection = event.collection.upper()
    return {
        "timestamp_utc": timestamp_utc or _utc_now(),
        "button": button,
        "raw_input_type": 2,
        "collection": collection,
        "device_family": f"T1-Remote/{collection}",
        "usage_page": event.usage_page,
        "usage_page_hex": f"0x{event.usage_page:04X}",
        "usage": event.usage,
        "usage_hex": f"0x{event.usage:04X}",
        "state": _report_state(collection, event.report),
        "sequence": event.sequence,
        "timestamp_100ns": event.timestamp_100ns,
        "raw_data_hex": event.report.hex(" "),
    }


def build_capture_document(events: list[dict[str, Any]]) -> dict[str, Any]:
    """构造驱动采集夹具文档。"""

    return {
        "format_version": 1,
        "scope": "remote_control_14_keys",
        "source": "T1Bridge_ReadEvent",
        "device": {
            "name": "T1-Remote",
            "vid": "620A",
            "pid": "0407",
        },
        "events": events,
    }


def _write_capture(path: Path, events: list[dict[str, Any]]) -> None:
    """以 UTF-8 BOM、CRLF 写入新夹具，禁止覆盖已有文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(build_capture_document(events), ensure_ascii=False, indent=2)
    with path.open("x", encoding="utf-8-sig", newline="\r\n") as file:
        file.write(content)
        file.write("\r\n")


def _default_output_path() -> Path:
    """生成不会覆盖已有文件的默认输出路径。"""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = PROJECT_ROOT / "captures" / f"t1-driver-control-{stamp}"
    candidate = base.with_suffix(".json")
    suffix = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{suffix}").with_suffix(".json")
        suffix += 1
    return candidate


def run(output_path: Path) -> int:
    """启动驱动队列采集会话。"""

    guard = SingleInstanceGuard("Local\\T1Remote.DriverInspector")
    if not guard.acquire():
        print("已有一个 T1 Driver Inspector 会话正在运行")
        return 1

    bridge = T1BridgeClient()
    stop_requested = threading.Event()
    state_lock = threading.Lock()
    current_button: str | None = None
    events: list[dict[str, Any]] = []

    def command_loop() -> None:
        """读取按键标签和退出命令，不阻塞驱动事件读取。"""

        nonlocal current_button
        print("输入编号后按回车，接着只按对应遥控键；q 退出并保存。")
        print("可采集按键：" + "、".join(REMOTE_BUTTONS))
        while not stop_requested.is_set():
            try:
                command = input("Driver Inspector> ").strip()
            except (EOFError, KeyboardInterrupt):
                stop_requested.set()
                return
            if command.lower() == "q":
                stop_requested.set()
                return
            if command == "0":
                with state_lock:
                    current_button = None
                print("已清除当前标签，后续事件仍显示但标记为未标记。")
                continue
            if not command.isdigit():
                print("请输入 1-14、0 或 q。")
                continue
            try:
                selected = validate_button_number(int(command))
            except ValueError as error:
                print(error)
                continue
            if selected in DISABLED_CAPTURE_BUTTONS:
                print(f"{selected} 键已禁用，采集期间不要触发实体按键。")
                continue
            with state_lock:
                current_button = selected
            print(f"当前标签：{selected}")

    try:
        bridge.open(build_default_interception_policy())
        bridge.start()
        bridge.heartbeat()
        bridge.flush_events()
        status = bridge.status()
        print(
            f"[驱动] {status.state} | COL 掩码={status.attached_collections} | "
            f"租约={'有效' if status.lease_active else '无效'}"
        )
        command_thread = threading.Thread(
            target=command_loop,
            name="t1-driver-inspector-command",
            daemon=True,
        )
        command_thread.start()
        last_heartbeat = time.monotonic()
        while not stop_requested.wait(0.03):
            if time.monotonic() - last_heartbeat >= 1.0:
                bridge.heartbeat()
                last_heartbeat = time.monotonic()
            event = bridge.read_event()
            if event is None:
                continue
            with state_lock:
                button = current_button or "未标记"
            record = serialize_driver_event(event, button)
            events.append(record)
            print(
                f"[#{len(events)}] {button} | {event.collection} | "
                f"{record['usage_page_hex']}:{record['usage_hex']} | "
                f"{record['state']} | {record['raw_data_hex']}"
            )
    except (BridgeError, OSError, RuntimeError) as error:
        print(f"启动 Driver Inspector 失败：{error}")
        return 1
    finally:
        stop_requested.set()
        try:
            if bridge.is_open:
                bridge.stop()
                bridge.close()
        except BridgeError as error:
            print(f"停止 Driver Inspector 失败：{error}")
        guard.release()

    if not events:
        print("没有驱动事件，未创建采集夹具。")
        return 0
    try:
        _write_capture(output_path, events)
        print(f"已保存 {len(events)} 条驱动事件：{output_path}")
    except (FileExistsError, OSError, TypeError, ValueError) as error:
        print(f"保存驱动采集夹具失败：{error}")
        return 1
    return 0


def main() -> int:
    """解析参数并启动驱动事件采集。"""

    parser = argparse.ArgumentParser(description="采集 T1 COL02/COL03 驱动事件")
    parser.add_argument("--output", type=Path, default=None, help="新的 JSON 输出路径")
    args = parser.parse_args()
    return run(args.output or _default_output_path())


if __name__ == "__main__":
    raise SystemExit(main())
