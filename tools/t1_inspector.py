"""程序说明：交互式采集 T1 遥控区域 14 个实体按键的 Raw Input 报文。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    CaptureEvent,
    build_capture_coverage,
    build_logical_actions,
    build_physical_mapping_table,
    capture_metadata,
    collection_from_device_path,
    is_t1_device_path,
    redacted_device_family,
    validate_button_number,
)
from t1remote.windows.raw_input import RawInputEvent, RawInputListener


RAW_INPUT_TYPE_NAMES = {
    0: "mouse",
    1: "keyboard",
    2: "hid",
}


def _utc_now() -> str:
    """生成可排序的 UTC 时间戳。"""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_capture(output_path: Path, events: list[CaptureEvent]) -> None:
    """以 UTF-8 BOM 和 CRLF 写入脱敏 JSON 夹具。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "format_version": 1,
        "scope": "remote_control_14_keys",
        "device": {
            "name": "T1-Remote",
            "vid": "620A",
            "pid": "0407",
        },
        "events": [event.to_dict() for event in events],
        "logical_actions": [
            action.to_dict() for action in build_logical_actions(events)
        ],
        "capture_coverage": build_capture_coverage(events),
        "physical_mapping": build_physical_mapping_table(events),
    }
    content = json.dumps(document, ensure_ascii=False, indent=2)
    with output_path.open("w", encoding="utf-8-sig", newline="\r\n") as file:
        file.write(content)
        file.write("\r\n")


def _load_capture(input_path: Path) -> list[CaptureEvent]:
    """读取已有脱敏采集事件，供 GUI 跨会话保留结果。"""

    if not input_path.exists():
        return []
    try:
        document = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_events = document.get("events", [])
    if not isinstance(raw_events, list):
        return []
    events: list[CaptureEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue
        try:
            events.append(
                CaptureEvent(
                    timestamp_utc=str(raw_event["timestamp_utc"]),
                    button=str(raw_event["button"]),
                    raw_input_type=int(raw_event["raw_input_type"]),
                    collection=str(raw_event["collection"]),
                    device_family=str(raw_event["device_family"]),
                    raw_data_hex=str(raw_event["raw_data_hex"]),
                    state=str(raw_event.get("state", "unknown")),
                    usage_page=(
                        int(raw_event["usage_page"])
                        if raw_event.get("usage_page") is not None
                        else None
                    ),
                    usage=(
                        int(raw_event["usage"])
                        if raw_event.get("usage") is not None
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return events


def _write_capture_if_nonempty(
    output_path: Path,
    events: list[CaptureEvent],
) -> bool:
    """只在有新事件时保存，避免空退出覆盖已有采集文件。"""

    if not events:
        return False
    _write_capture(output_path, events)
    return True


def _append_capture_if_nonempty(
    output_path: Path,
    new_events: list[CaptureEvent],
) -> bool:
    """只追加本次采集的新事件，保留已有夹具内容。"""

    if not new_events:
        return False
    existing_events = _load_capture(output_path)
    _write_capture(output_path, existing_events + new_events)
    return True


def _build_event(raw_event: RawInputEvent, button: str) -> CaptureEvent:
    """把底层事件转换为不包含蓝牙地址的采集记录。"""

    collection = collection_from_device_path(raw_event.device_path)
    raw_data_hex = raw_event.raw_data.hex(" ")
    state, usage_page, usage = capture_metadata(
        raw_event.raw_input_type,
        collection,
        raw_data_hex,
    )
    return CaptureEvent(
        timestamp_utc=_utc_now(),
        button=button,
        raw_input_type=raw_event.raw_input_type,
        collection=collection,
        device_family=redacted_device_family(raw_event.device_path),
        raw_data_hex=raw_data_hex,
        state=state,
        usage_page=usage_page,
        usage=usage,
    )


def _print_button_list() -> None:
    """打印物理按键编号，避免把键盘面同名按键混入采集。"""

    print("遥控区域按键编号：")
    for number, button in enumerate(REMOTE_BUTTONS, start=1):
        print(f"  {number:2d}. {button}")


def main() -> int:
    """启动交互式采集。"""

    parser = argparse.ArgumentParser(
        description="只采集 T1-Remote 遥控区域按键的 Raw Input 报文"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("captures") / "t1-remote-control.json",
        help="脱敏 JSON 输出路径",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="使用命令行模式，不打开 Tkinter 窗口",
    )
    args = parser.parse_args()

    if not args.cli:
        from tools.t1_inspector_gui import run_gui

        return run_gui(args.output)

    current_button: str | None = None
    state_lock = threading.Lock()
    events: list[CaptureEvent] = []
    events_lock = threading.Lock()
    persisted_event_count = 0

    def on_event(raw_event: RawInputEvent) -> None:
        """过滤 T1 路径，并把当前手动选择的物理键写入内存。"""

        # 不按 Usage、按键类型或当前标签丢弃 T1 报文；未选标签的报文标记为“未标记”。
        if not is_t1_device_path(raw_event.device_path):
            return
        with state_lock:
            button = current_button or "未标记"

        event = _build_event(raw_event, button)
        with events_lock:
            events.append(event)
            event_number = len(events)
        event_type = RAW_INPUT_TYPE_NAMES.get(raw_event.raw_input_type, "unknown")
        print(
            f"[#{event_number}] {button} | {event_type} | "
            f"{event.collection} | {event.raw_data_hex}"
        )

    def on_error(error: Exception) -> None:
        """输出底层 Windows API 异常，不中断用户操作。"""

        print(f"[Raw Input 错误] {error}")

    listener = RawInputListener(on_event=on_event, on_error=on_error)
    try:
        listener.start()
    except Exception as error:
        print(f"启动 Inspector 失败：{error}")
        return 1

    _print_button_list()
    print("输入编号后按回车，接着只按对应的遥控区域按键。")
    print("命令：s 保存，0 清除当前标签，q 退出并保存。")
    try:
        while True:
            command = input("Inspector> ").strip()
            if command.lower() == "q":
                break
            if command.lower() == "s":
                with events_lock:
                    pending_events = events[persisted_event_count:]
                    if _append_capture_if_nonempty(args.output, pending_events):
                        persisted_event_count = len(events)
                        print(f"已追加 {len(pending_events)} 条新事件：{args.output}")
                    else:
                        print("当前没有待存储的新事件，保留已有 JSON 文件。")
                continue
            if command == "0":
                with state_lock:
                    current_button = None
                print("已清除当前按键标签，后续 T1 报文不会写入夹具。")
                continue
            if not command.isdigit():
                print("请输入 1-14、0、s 或 q。")
                continue
            try:
                selected_button = validate_button_number(int(command))
            except ValueError as error:
                print(error)
                continue
            if selected_button in DISABLED_CAPTURE_BUTTONS:
                print(f"{selected_button} 键已禁用，采集期间不要触发实体按键。")
                continue
            with state_lock:
                current_button = selected_button
            print(f"当前标签：{selected_button}")
    except KeyboardInterrupt:
        print("\n收到中断，准备保存。")
    finally:
        listener.stop()
        with events_lock:
            pending_events = events[persisted_event_count:]
            if _append_capture_if_nonempty(args.output, pending_events):
                print(f"已追加 {len(pending_events)} 条新事件：{args.output}")
            else:
                print("没有待存储的新事件，保留已有 JSON 文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
