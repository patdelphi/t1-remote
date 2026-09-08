"""程序说明：解析 HID Report Descriptor 的字段布局，不执行设备 I/O。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class HidReportDescriptorError(ValueError):
    """HID Report Descriptor 截断或结构非法。"""


@dataclass(frozen=True)
class HidReportField:
    """一个 Input、Output 或 Feature 主项对应的字段摘要。"""

    report_type: str
    report_id: int
    usage_page: int
    usages: tuple[int, ...]
    usage_min: int | None
    usage_max: int | None
    bit_offset: int
    bit_width: int
    count: int
    flags: int
    collection_path: tuple[tuple[int, int], ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的十六进制字段摘要。"""

        return {
            "report_type": self.report_type,
            "report_id": self.report_id,
            "usage_page": f"0x{self.usage_page:02X}",
            "usages": [f"0x{usage:X}" for usage in self.usages],
            "usage_min": (
                f"0x{self.usage_min:X}" if self.usage_min is not None else None
            ),
            "usage_max": (
                f"0x{self.usage_max:X}" if self.usage_max is not None else None
            ),
            "bit_offset": self.bit_offset,
            "bit_width": self.bit_width,
            "count": self.count,
            "flags": f"0x{self.flags:02X}",
            "collection_path": [
                {
                    "usage_page": f"0x{page:02X}",
                    "usage": f"0x{usage:X}",
                }
                for page, usage in self.collection_path
            ],
        }


@dataclass(frozen=True)
class HidReportDescriptor:
    """报告描述符解析结果。"""

    fields: tuple[HidReportField, ...]
    report_bit_lengths: dict[tuple[str, int], int]


def _item_value(data: bytes) -> int:
    """按 HID 小端规则把 Item 数据解释为无符号整数。"""

    return int.from_bytes(data, "little", signed=False) if data else 0


def _usage_value(value: int, current_page: int) -> tuple[int, int]:
    """解析短 Usage；带高 16 位时使用其显式 Usage Page。"""

    if value > 0xFFFF:
        return (value >> 16) & 0xFFFF, value & 0xFFFF
    return current_page, value


def parse_hid_report_descriptor(descriptor: bytes) -> HidReportDescriptor:
    """解析标准短 Item，并返回每个主项的位布局。"""

    if not isinstance(descriptor, bytes):
        raise TypeError("descriptor 必须是 bytes")
    fields: list[HidReportField] = []
    report_bit_lengths: dict[tuple[str, int], int] = {}
    global_state: dict[str, int] = {
        "usage_page": 0,
        "logical_min": 0,
        "logical_max": 0,
        "report_size": 0,
        "report_count": 0,
        "report_id": 0,
    }
    global_stack: list[dict[str, int]] = []
    local_usages: list[tuple[int, int]] = []
    local_usage_min: tuple[int, int] | None = None
    local_usage_max: tuple[int, int] | None = None
    collection_path: list[tuple[int, int]] = []
    offsets: dict[tuple[str, int], int] = {}
    index = 0

    while index < len(descriptor):
        prefix = descriptor[index]
        index += 1
        if prefix == 0xFE:
            if index + 2 > len(descriptor):
                raise HidReportDescriptorError("长 Item 头部不完整")
            data_size = descriptor[index]
            index += 2  # data size and long item tag
            if index + data_size > len(descriptor):
                raise HidReportDescriptorError("长 Item 数据不完整")
            index += data_size
            continue

        size_code = prefix & 0x03
        data_size = 4 if size_code == 3 else size_code
        item_type = (prefix >> 2) & 0x03
        item_tag = (prefix >> 4) & 0x0F
        if index + data_size > len(descriptor):
            raise HidReportDescriptorError("短 Item 数据不完整")
        data = descriptor[index : index + data_size]
        index += data_size
        value = _item_value(data)

        if item_type == 1:  # Global
            if item_tag == 0x0:
                global_state["usage_page"] = value
            elif item_tag == 0x1:
                global_state["logical_min"] = value
            elif item_tag == 0x2:
                global_state["logical_max"] = value
            elif item_tag == 0x7:
                global_state["report_size"] = value
            elif item_tag == 0x8:
                if value > 0xFF:
                    raise HidReportDescriptorError("Report ID 超出 8 位")
                global_state["report_id"] = value
            elif item_tag == 0x9:
                global_state["report_count"] = value
            elif item_tag == 0xA:
                global_stack.append(global_state.copy())
            elif item_tag == 0xB:
                if not global_stack:
                    raise HidReportDescriptorError("Global Pop 没有对应的 Push")
                global_state = global_stack.pop()
            continue

        if item_type == 2:  # Local
            if item_tag == 0x0:
                local_usages.append(
                    _usage_value(value, global_state["usage_page"])
                )
            elif item_tag == 0x1:
                local_usage_min = _usage_value(value, global_state["usage_page"])
            elif item_tag == 0x2:
                local_usage_max = _usage_value(value, global_state["usage_page"])
            continue

        if item_type != 0:  # Reserved
            continue

        if item_tag == 0xA:  # Collection
            if local_usages:
                collection_path.append(local_usages[0])
            else:
                collection_path.append((global_state["usage_page"], 0))
            local_usages = []
            local_usage_min = None
            local_usage_max = None
            continue
        if item_tag == 0xC:  # End Collection
            if not collection_path:
                raise HidReportDescriptorError("End Collection 没有对应的 Collection")
            collection_path.pop()
            local_usages = []
            local_usage_min = None
            local_usage_max = None
            continue
        if item_tag not in (0x8, 0x9, 0xB):
            local_usages = []
            local_usage_min = None
            local_usage_max = None
            continue

        report_type = {0x8: "input", 0x9: "output", 0xB: "feature"}[item_tag]
        report_id = global_state["report_id"]
        bit_width = global_state["report_size"]
        count = global_state["report_count"]
        if bit_width < 0 or count < 0:
            raise HidReportDescriptorError("Report Size 或 Count 不能为负数")
        key = (report_type, report_id)
        bit_offset = offsets.get(key, 0)
        offsets[key] = bit_offset + bit_width * count
        usage_ids = tuple(usage for _page, usage in local_usages)
        usage_min = local_usage_min[1] if local_usage_min is not None else None
        usage_max = local_usage_max[1] if local_usage_max is not None else None
        fields.append(
            HidReportField(
                report_type=report_type,
                report_id=report_id,
                usage_page=(
                    local_usages[0][0]
                    if local_usages
                    else global_state["usage_page"]
                ),
                usages=usage_ids,
                usage_min=usage_min,
                usage_max=usage_max,
                bit_offset=bit_offset,
                bit_width=bit_width,
                count=count,
                flags=value,
                collection_path=tuple(collection_path),
            )
        )
        report_bit_lengths[key] = offsets[key]
        local_usages = []
        local_usage_min = None
        local_usage_max = None

    if collection_path:
        raise HidReportDescriptorError("Collection 未闭合")
    return HidReportDescriptor(tuple(fields), report_bit_lengths)


__all__ = [
    "HidReportDescriptor",
    "HidReportDescriptorError",
    "HidReportField",
    "parse_hid_report_descriptor",
]
