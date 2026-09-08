"""程序说明：把已确认的 T1 HID/Keyboard 报文解码为语义按键事件。

这里只负责设备报文到物理按键的识别，不执行 SendInput，也不处理宏、长按
和应用快捷键。未知 Usage 会保留在事件中，调用方可以记录但不能自动注入。
"""

from __future__ import annotations

from dataclasses import dataclass


_CONSUMER_BUTTONS = {
    0x221: "Voice",
    0x223: "Home",
    0x224: "Return",
    0xE2: "Mute",
    0xE9: "Volume Plus",
    0xEA: "Volume Minus",
}

_SYSTEM_BUTTONS = {0x01: "Power"}

_KEYBOARD_BUTTONS = {
    0x0D: "OK",
    0x25: "Arrow Left",
    0x26: "Arrow Up",
    0x27: "Arrow Right",
    0x28: "Arrow Down",
    0x5D: "Menu",
}

_BUTTON_INPUT_KINDS = {
    **{button: "keyboard" for button in _KEYBOARD_BUTTONS.values()},
    **{button: "hid" for button in _CONSUMER_BUTTONS.values()},
    **{button: "hid" for button in _SYSTEM_BUTTONS.values()},
    "Air Mouse": "mouse",
}


def button_input_kind(button: str) -> str:
    """返回已确认物理按键的输入类型：keyboard、hid 或 mouse。"""

    return _BUTTON_INPUT_KINDS.get(button, "unknown")


@dataclass(frozen=True)
class ButtonEvent:
    """一次 T1 物理按键事件；button 为空表示未知 Usage。"""

    button: str | None
    state: str
    collection: str
    source: str
    report: bytes
    usage_page: int | None = None
    usage: int | None = None


class T1InputDecoder:
    """按 Collection 维护按下状态，并解码当前已确认的 T1 报文布局。"""

    def __init__(self) -> None:
        self._active_buttons: dict[str, str] = {}

    def feed(
        self,
        collection: str,
        raw_input_type: int,
        report: bytes,
        *,
        usage_page: int | None = None,
        usage: int | None = None,
    ) -> ButtonEvent:
        """解码一条 Raw Input 或驱动事件，不对未知报文做猜测。"""

        normalized_collection = collection.upper()
        if raw_input_type == 1 and normalized_collection == "COL01":
            return self._decode_keyboard(normalized_collection, report)
        if raw_input_type == 2 and normalized_collection == "COL02":
            return self._decode_consumer(
                normalized_collection, report, usage_page, usage
            )
        if raw_input_type == 2 and normalized_collection == "COL03":
            return self._decode_system(normalized_collection, report, usage_page, usage)
        return ButtonEvent(
            button=None,
            state="unknown",
            collection=normalized_collection,
            source="unknown",
            report=bytes(report),
            usage_page=usage_page,
            usage=usage,
        )

    def reset(self) -> None:
        """清除设备断开或会话停止时的活动按键状态。"""

        self._active_buttons.clear()

    def _decode_keyboard(self, collection: str, report: bytes) -> ButtonEvent:
        if len(report) < 12:
            return self._unknown(collection, "keyboard", report)
        virtual_key = int.from_bytes(report[6:8], "little")
        message = int.from_bytes(report[8:12], "little")
        button = _KEYBOARD_BUTTONS.get(virtual_key)
        if message in (0x0100, 0x0104):
            state = "down"
            if button:
                self._active_buttons[f"{collection}:{virtual_key}"] = button
        elif message in (0x0101, 0x0105):
            state = "up"
            button = self._active_buttons.pop(f"{collection}:{virtual_key}", button)
        else:
            state = "unknown"
        return ButtonEvent(
            button=button,
            state=state if button else "unknown",
            collection=collection,
            source="keyboard",
            report=bytes(report),
            usage_page=0x07,
            usage=virtual_key,
        )

    def _decode_consumer(
        self,
        collection: str,
        report: bytes,
        usage_page: int | None,
        usage: int | None,
    ) -> ButtonEvent:
        report_usage = self._little_endian_usage(report)
        # 驱动事件的 usage 字段在释放包中会保留上一帧 Usage；报告本身为 0
        # 时必须优先按释放处理，不能被元数据重新解释成按下。
        parsed_usage = report_usage or (usage if len(report) < 3 else 0)
        if parsed_usage:
            button = _CONSUMER_BUTTONS.get(parsed_usage)
            if button:
                self._active_buttons[collection] = button
                return self._known(
                    button, "down", collection, "hid", report, usage_page or 0x0C, parsed_usage
                )
            return self._unknown(
                collection, "hid", report, usage_page or 0x0C, parsed_usage
            )
        button = self._active_buttons.pop(collection, None)
        if button:
            return self._known(
                button, "up", collection, "hid", report, usage_page or 0x0C, 0
            )
        return self._unknown(collection, "hid", report, usage_page or 0x0C, 0)

    def _decode_system(
        self,
        collection: str,
        report: bytes,
        usage_page: int | None,
        usage: int | None,
    ) -> ButtonEvent:
        report_usage = report[1] if len(report) > 1 else 0
        parsed_usage = report_usage or (usage if len(report) < 2 else 0)
        if parsed_usage:
            button = _SYSTEM_BUTTONS.get(parsed_usage)
            if button:
                self._active_buttons[collection] = button
                return self._known(
                    button, "down", collection, "hid", report, usage_page or 0x01, parsed_usage
                )
            return self._unknown(
                collection, "hid", report, usage_page or 0x01, parsed_usage
            )
        button = self._active_buttons.pop(collection, None)
        if button:
            return self._known(
                button, "up", collection, "hid", report, usage_page or 0x01, 0
            )
        return self._unknown(collection, "hid", report, usage_page or 0x01, 0)

    @staticmethod
    def _little_endian_usage(report: bytes) -> int:
        if len(report) >= 3:
            return int.from_bytes(report[1:3], "little")
        return report[1] if len(report) >= 2 else 0

    @staticmethod
    def _known(
        button: str,
        state: str,
        collection: str,
        source: str,
        report: bytes,
        usage_page: int,
        usage: int,
    ) -> ButtonEvent:
        return ButtonEvent(
            button=button,
            state=state,
            collection=collection,
            source=source,
            report=bytes(report),
            usage_page=usage_page,
            usage=usage,
        )

    @staticmethod
    def _unknown(
        collection: str,
        source: str,
        report: bytes,
        usage_page: int | None = None,
        usage: int | None = None,
    ) -> ButtonEvent:
        return ButtonEvent(
            button=None,
            state="unknown",
            collection=collection,
            source=source,
            report=bytes(report),
            usage_page=usage_page,
            usage=usage,
        )


__all__ = ["ButtonEvent", "T1InputDecoder", "button_input_kind"]
