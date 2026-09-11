"""程序说明：定义 T1 遥控区域的物理按键清单和脱敏采集记录模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Any


# 产品图对应 A 面 12 个正面键和 2 个侧面音量键。
REMOTE_BUTTONS: tuple[str, ...] = (
    "Power",
    "Arrow Up",
    "Arrow Down",
    "Arrow Left",
    "Arrow Right",
    "OK",
    "Return",
    "Voice",
    "Mute",
    "Home",
    "Menu",
    "Air Mouse",
    "Volume Plus",
    "Volume Minus",
)

# Air Mouse 会切换飞鼠模式并产生连续鼠标报文，采集界面暂不允许选择。
# Power 由驱动层拦截后允许在 Python 采集界面中选择，避免触发 Windows 电源动作。
DISABLED_CAPTURE_BUTTONS: tuple[str, ...] = ("Air Mouse",)
MAPPABLE_REMOTE_BUTTONS: tuple[str, ...] = tuple(
    button for button in REMOTE_BUTTONS if button not in DISABLED_CAPTURE_BUTTONS
)

T1_VID = "620A"
T1_PID = "0407"


def selected_capture_button(button: str | None) -> str | None:
    """返回可用于采集的当前标签；未选择或禁用按键时返回 None。"""

    if button is None or button not in REMOTE_BUTTONS:
        return None
    if button in DISABLED_CAPTURE_BUTTONS:
        return None
    return button


@dataclass(frozen=True)
class CaptureEvent:
    """一次与当前遥控区域标签关联的 Windows Raw Input 事件。"""

    timestamp_utc: str
    button: str
    raw_input_type: int
    collection: str
    device_family: str
    raw_data_hex: str
    state: str = "unknown"
    usage_page: int | None = None
    usage: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 可序列化对象。"""

        return asdict(self)


@dataclass(frozen=True)
class LogicalAction:
    """把一次按键按下/抬起合并后的逻辑操作。"""

    action_id: int
    button: str
    state: str
    down_timestamp_utc: str | None
    up_timestamp_utc: str | None
    duration_ms: int | None
    packet_count: int
    raw_event_indexes: tuple[int, ...]
    raw_input_type: int
    collection: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 可序列化对象。"""

        data = asdict(self)
        data["raw_event_indexes"] = list(self.raw_event_indexes)
        return data


def _report_state_and_signature(event: CaptureEvent) -> tuple[str, str]:
    """从 Windows 键盘数据或 HID 报文中提取状态和配对签名。"""

    try:
        raw_data = bytes.fromhex(event.raw_data_hex)
    except ValueError:
        return "unknown", "invalid"

    if event.raw_input_type == 1 and len(raw_data) >= 12:
        # RAWKEYBOARD：VKey 位于 6，Message 位于 8。
        virtual_key = int.from_bytes(raw_data[6:8], "little")
        message = int.from_bytes(raw_data[8:12], "little")
        if message in (0x0100, 0x0104):
            state = "down"
        elif message in (0x0101, 0x0105):
            state = "up"
        else:
            state = "unknown"
        return state, f"keyboard:{virtual_key:04x}"

    if event.raw_input_type == 2 and raw_data:
        # T1 Consumer Control 的释放包通常只保留 Report ID，其余字节为 0。
        report_id = raw_data[0]
        if any(raw_data[1:]):
            return "down", f"hid:{report_id:02x}:{raw_data[1:].hex()}"
        return "up", f"hid:{report_id:02x}"

    return "unknown", f"raw:{event.raw_data_hex}"


def capture_metadata(
    raw_input_type: int,
    collection: str,
    raw_data_hex: str,
) -> tuple[str, int | None, int | None]:
    """从原始 Windows 输入数据推导状态和 Usage 元数据。

    这里只解析当前 T1 已知的 Raw Input 布局；遇到未知布局时返回
    ``unknown``，避免用猜测结果污染物理按键夹具。
    """

    try:
        raw_data = bytes.fromhex(raw_data_hex)
    except ValueError:
        return "unknown", None, None

    normalized_collection = collection.upper()
    if raw_input_type == 1 and len(raw_data) >= 12:
        virtual_key = int.from_bytes(raw_data[6:8], "little")
        message = int.from_bytes(raw_data[8:12], "little")
        if message in (0x0100, 0x0104):
            return "down", 0x07, virtual_key
        if message in (0x0101, 0x0105):
            return "up", 0x07, virtual_key
        return "unknown", 0x07, virtual_key

    if raw_input_type == 2 and normalized_collection == "COL02":
        usage = int.from_bytes(raw_data[1:3], "little") if len(raw_data) >= 3 else 0
        return ("down" if usage else "up"), 0x0C, usage

    if raw_input_type == 2 and normalized_collection == "COL03":
        usage = raw_data[1] if len(raw_data) >= 2 else 0
        return ("down" if usage else "up"), 0x01, usage

    return "unknown", None, None


def _duration_ms(start: str | None, end: str | None) -> int | None:
    """计算按下到抬起的毫秒数，时间格式异常时返回 None。"""

    if not start or not end:
        return None
    try:
        milliseconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        return max(0, round(milliseconds))
    except ValueError:
        return None


def build_logical_actions(events: list[CaptureEvent]) -> list[LogicalAction]:
    """将原始包配对成逻辑操作，并把重复按下包合并到同一操作。"""

    mutable_actions: list[dict[str, Any]] = []
    active: dict[tuple[str, str], int] = {}

    for event_index, event in enumerate(events, start=1):
        state, signature = _report_state_and_signature(event)
        key = (event.button, signature)

        if state == "down":
            if key in active:
                action = mutable_actions[active[key]]
                action["packet_count"] += 1
                action["raw_event_indexes"].append(event_index)
                continue
            action_index = len(mutable_actions)
            mutable_actions.append(
                {
                    "button": event.button,
                    "state": "pressed",
                    "down_timestamp_utc": event.timestamp_utc,
                    "up_timestamp_utc": None,
                    "duration_ms": None,
                    "packet_count": 1,
                    "raw_event_indexes": [event_index],
                    "raw_input_type": event.raw_input_type,
                    "collection": event.collection,
                    "signature": signature,
                }
            )
            active[key] = action_index
            continue

        if state == "up":
            matching_key = key
            if key not in active and signature.startswith("hid:"):
                prefix = signature + ":"
                candidates = [
                    active_key
                    for active_key in active
                    if active_key[0] == event.button and active_key[1].startswith(prefix)
                ]
                if candidates:
                    matching_key = candidates[-1]
            if matching_key in active:
                action = mutable_actions[active.pop(matching_key)]
                action["state"] = "press_release"
                action["up_timestamp_utc"] = event.timestamp_utc
                action["duration_ms"] = _duration_ms(
                    action["down_timestamp_utc"], event.timestamp_utc
                )
                action["packet_count"] += 1
                action["raw_event_indexes"].append(event_index)
                continue
            mutable_actions.append(
                {
                    "button": event.button,
                    "state": "release_only",
                    "down_timestamp_utc": None,
                    "up_timestamp_utc": event.timestamp_utc,
                    "duration_ms": None,
                    "packet_count": 1,
                    "raw_event_indexes": [event_index],
                    "raw_input_type": event.raw_input_type,
                    "collection": event.collection,
                    "signature": signature,
                }
            )
            continue

        # 未知格式不能安全配对，保留为独立记录，避免误合并不同按键。
        mutable_actions.append(
            {
                "button": event.button,
                "state": "unknown",
                "down_timestamp_utc": event.timestamp_utc,
                "up_timestamp_utc": None,
                "duration_ms": None,
                "packet_count": 1,
                "raw_event_indexes": [event_index],
                "raw_input_type": event.raw_input_type,
                "collection": event.collection,
                "signature": signature,
            }
        )

    return [
        LogicalAction(action_id=index, **action)
        for index, action in enumerate(mutable_actions, start=1)
    ]


def build_capture_coverage(events: list[CaptureEvent]) -> dict[str, Any]:
    """生成 14 键采集覆盖摘要，不把未确认按键标记为已完成。"""

    required_buttons = tuple(
        button for button in REMOTE_BUTTONS if button not in DISABLED_CAPTURE_BUTTONS
    )
    observed = {
        event.button for event in events if event.button in required_buttons
    }
    paired = {
        action.button
        for action in build_logical_actions(events)
        if action.state == "press_release" and action.button in required_buttons
    }
    ordered_observed = [button for button in required_buttons if button in observed]
    ordered_paired = [button for button in required_buttons if button in paired]
    missing = [button for button in required_buttons if button not in paired]
    return {
        "required_buttons": list(required_buttons),
        "disabled_buttons": list(DISABLED_CAPTURE_BUTTONS),
        "observed_buttons": ordered_observed,
        "paired_press_release_buttons": ordered_paired,
        "missing_buttons": missing,
        "complete": not missing,
    }


def build_physical_mapping_table(events: list[CaptureEvent]) -> list[dict[str, Any]]:
    """生成按键物理映射观察表，不把观察结果写入动作配置。"""

    actions = build_logical_actions(events)
    table: list[dict[str, Any]] = []
    for button in REMOTE_BUTTONS:
        button_events = [event for event in events if event.button == button]
        button_actions = [action for action in actions if action.button == button]
        if button in DISABLED_CAPTURE_BUTTONS:
            status = "disabled"
        elif any(action.state == "press_release" for action in button_actions):
            status = "confirmed"
        elif button_events:
            status = "observed"
        else:
            status = "missing"

        collections = sorted({event.collection for event in button_events})
        raw_input_types = sorted({event.raw_input_type for event in button_events})
        states: set[str] = set()
        usages: set[tuple[int, int]] = set()
        samples: list[str] = []
        for event in button_events:
            state, usage_page, usage = capture_metadata(
                event.raw_input_type,
                event.collection,
                event.raw_data_hex,
            )
            states.add(event.state if event.state in {"down", "up"} else state)
            resolved_page = event.usage_page if event.usage_page is not None else usage_page
            resolved_usage = event.usage if event.usage is not None else usage
            if resolved_page is not None and resolved_usage not in (None, 0):
                usages.add((resolved_page, resolved_usage))
            if event.raw_data_hex not in samples and len(samples) < 3:
                samples.append(event.raw_data_hex)
        table.append(
            {
                "button": button,
                "status": status,
                "collections": collections,
                "raw_input_types": raw_input_types,
                "states": sorted(states),
                "usages": [
                    {"usage_page": f"0x{page:02X}", "usage": f"0x{usage:X}"}
                    for page, usage in sorted(usages)
                ],
                "paired_actions": sum(
                    action.state == "press_release" for action in button_actions
                ),
                "sample_reports": samples,
            }
        )
    return table


def is_t1_device_path(device_path: str) -> bool:
    """只匹配当前 T1 的 VID/PID，避免采集普通键鼠。"""

    if not device_path:
        return False
    normalized = device_path.upper()
    vid_matches = (f"VID_{T1_VID}", f"VID&01{T1_VID}", f"VID&{T1_VID}")
    pid_matches = (f"PID_{T1_PID}", f"PID&{T1_PID}")
    return any(item in normalized for item in vid_matches) and any(
        item in normalized for item in pid_matches
    )


def collection_from_device_path(device_path: str) -> str:
    """从 Raw Input 设备路径提取 Collection 编号。"""

    match = re.search(
        r"(?:^|[&#\\])COL(\d{2})(?=[&#\\]|$)",
        device_path or "",
        re.IGNORECASE,
    )
    return f"COL{match.group(1)}".upper() if match else "UNKNOWN"


def classify_hid_transport(device_path: str) -> str:
    """根据 HID 接口路径给出脱敏的传输类型提示。"""

    normalized = (device_path or "").upper()
    if (
        "BTHLEDEVICE" in normalized
        or "00001812-0000-1000-8000-00805F9B34FB" in normalized
    ):
        return "ble-hid"
    if "\\USB#" in normalized or normalized.startswith("USB#"):
        return "usb-hid"
    if "HID#" in normalized or normalized.startswith("HID#"):
        return "hid-unknown"
    return "unknown"


def redacted_device_family(device_path: str) -> str:
    """返回不包含蓝牙地址和完整设备路径的设备归属信息。"""

    if not is_t1_device_path(device_path):
        return "UNKNOWN"
    return f"T1-Remote/{collection_from_device_path(device_path)}"


def validate_button_number(button_number: int) -> str:
    """把交互式编号转换为按键名称，非法编号统一抛出 ValueError。"""

    if not 1 <= button_number <= len(REMOTE_BUTTONS):
        raise ValueError(f"遥控区域按键编号必须在 1-{len(REMOTE_BUTTONS)} 之间")
    return REMOTE_BUTTONS[button_number - 1]
