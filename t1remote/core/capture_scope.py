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

# 这两个物理键可能触发系统级行为或大量连续鼠标报文，采集界面不允许选择。
DISABLED_CAPTURE_BUTTONS: tuple[str, ...] = ("Power", "Air Mouse")

T1_VID = "620A"
T1_PID = "0407"


@dataclass(frozen=True)
class CaptureEvent:
    """一次与当前遥控区域标签关联的 Windows Raw Input 事件。"""

    timestamp_utc: str
    button: str
    raw_input_type: int
    collection: str
    device_family: str
    raw_data_hex: str

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

    match = re.search(r"(?:^|[&\\])COL(\d{2})(?:&|\\|$)", device_path or "", re.IGNORECASE)
    return f"COL{match.group(1)}".upper() if match else "UNKNOWN"


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
