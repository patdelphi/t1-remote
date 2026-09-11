"""程序说明：采集 T1 COL02/COL03 过滤驱动队列中的原始 HID 事件。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    selected_capture_button,
    validate_button_number,
)
from t1remote.core.hid_evidence import (
    HidReportSample,
    build_hid_evidence_report,
    classify_hid_report,
)
from t1remote.windows.driver_bridge import (
    BridgeError,
    DriverInputEvent,
    T1BridgeClient,
    build_default_interception_policy,
)
from t1remote.windows.hid_input import HidInputListener
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
        "report_id": None,
        "report_id_source": None,
        "report_category": classify_hid_report(
            collection=collection,
            usage_page=event.usage_page,
            usage=event.usage,
        ),
        "descriptor_status": "descriptor_unavailable",
        "state": _report_state(collection, event.report),
        "sequence": event.sequence,
        "timestamp_100ns": event.timestamp_100ns,
        "raw_data_hex": event.report.hex(" "),
    }


def build_capture_document(
    events: list[dict[str, Any]],
    *,
    report_descriptors: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """构造驱动采集夹具文档，并保存过滤器读取到的原始描述符。"""

    samples: list[HidReportSample] = []
    for event in events:
        try:
            raw_report_hex = str(event["raw_data_hex"])
            report = bytes.fromhex(raw_report_hex)
            samples.append(
                HidReportSample(
                    collection=str(event["collection"]),
                    report=report,
                    usage_page=int(event["usage_page"]),
                    usage=int(event["usage"]),
                    state=str(event.get("state", "unknown")),
                    report_id=(
                        int(event["report_id"])
                        if event.get("report_id") is not None
                        else None
                    ),
                    timestamp_utc=(
                        str(event["timestamp_utc"])
                        if event.get("timestamp_utc") is not None
                        else None
                    ),
                    timestamp_100ns=(
                        int(event["timestamp_100ns"])
                        if event.get("timestamp_100ns") is not None
                        else None
                    ),
                    sequence=(
                        int(event["sequence"])
                        if event.get("sequence") is not None
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    normalized_descriptors = {
        collection.upper(): bytes(descriptor)
        for collection, descriptor in (report_descriptors or {}).items()
        if descriptor
    }
    event_collections = {
        str(event.get("collection", "")).upper()
        for event in events
        if event.get("collection")
    }
    evidence_descriptor = None
    if len(event_collections) == 1:
        evidence_descriptor = normalized_descriptors.get(
            next(iter(event_collections))
        )

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
        "hid_descriptors": {
            collection: {
                "length": len(descriptor),
                "raw_descriptor_hex": descriptor.hex(" "),
            }
            for collection, descriptor in sorted(normalized_descriptors.items())
        },
        "hid_evidence": build_hid_evidence_report(
            samples,
            report_descriptor=evidence_descriptor,
        ),
    }


def _write_capture(
    path: Path,
    events: list[dict[str, Any]],
    *,
    report_descriptors: dict[str, bytes] | None = None,
) -> None:
    """以 UTF-8 BOM、CRLF 写入新夹具，禁止覆盖已有文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        build_capture_document(
            events,
            report_descriptors=report_descriptors,
        ),
        ensure_ascii=False,
        indent=2,
    )
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


def _read_report_descriptors(bridge: T1BridgeClient) -> dict[str, bytes]:
    """从过滤驱动读取目标 Collection 的原始 Report Descriptor。"""

    descriptors: dict[str, bytes] = {}
    for collection in ("COL02", "COL03"):
        try:
            descriptor = bridge.get_report_descriptor(collection)
        except (BridgeError, OSError, RuntimeError) as error:
            print(f"[HID] {collection} 描述符读取失败：{error}")
            continue
        if descriptor:
            descriptors[collection] = descriptor
            print(f"[HID] {collection} 已读取 Report Descriptor：{len(descriptor)} 字节")
    return descriptors


def _prime_hid_parser(bridge: T1BridgeClient) -> None:
    """请求 opaque preparsed data，让新驱动启用官方 parser 解码路径。"""

    getter = getattr(bridge, "get_preparsed_data", None)
    if not callable(getter):
        return
    for collection in ("COL02", "COL03"):
        try:
            preparsed_data = bytes(getter(collection))
        except (BridgeError, OSError, RuntimeError, TypeError, ValueError) as error:
            print(f"[HID] {collection} parser 能力读取失败，使用兼容解码：{error}")
            continue
        if preparsed_data:
            print(
                f"[HID] {collection} parser 能力已交给过滤驱动缓存："
                f"{len(preparsed_data)} 字节"
            )


def run(
    output_path: Path,
    *,
    hid_listener_factory: Callable[..., Any] = HidInputListener,
) -> int:
    """启动驱动队列采集会话，并保持目标 HID Collection 的读请求。"""

    guard = SingleInstanceGuard("Local\\T1Remote.DriverInspector")
    if not guard.acquire():
        print("已有一个 T1 Driver Inspector 会话正在运行")
        return 1

    bridge = T1BridgeClient()
    hid_listener: Any | None = None
    stop_requested = threading.Event()
    state_lock = threading.Lock()
    current_button: str | None = None
    events: list[dict[str, Any]] = []
    report_descriptors: dict[str, bytes] = {}

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
                print("已清除当前标签，后续驱动事件不会写入采集记录。")
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
        report_descriptors = _read_report_descriptors(bridge)
        _prime_hid_parser(bridge)
        bridge.flush_events()
        status = bridge.status()
        print(
            f"[驱动] {status.state} | COL 掩码={status.attached_collections} | "
            f"租约={'有效' if status.lease_active else '无效'}"
        )
        # HID 过滤器只有在下游存在持续 ReadFile 请求时才会收到真实报告。
        # 直读回调保持为空，采集事件统一从 T1Bridge_ReadEvent 消费一次，
        # 避免 HID 直读和桥接队列各写入一份相同事件。
        hid_listener = hid_listener_factory(
            on_event=lambda _event: None,
            on_error=lambda error: print(f"[HID] 读取失败：{error}"),
            target_collections=("COL02", "COL03"),
        )
        hid_listener.start()
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
                button = selected_capture_button(current_button)
            if button is None:
                continue
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
        if hid_listener is not None:
            try:
                hid_listener.stop()
            except Exception as error:
                print(f"停止 T1 HID 读请求失败：{error}")
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
        _write_capture(
            output_path,
            events,
            report_descriptors=report_descriptors,
        )
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
