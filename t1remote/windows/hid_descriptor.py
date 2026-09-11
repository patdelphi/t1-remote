"""程序说明：只读枚举 T1 HID Collection 的 Usage 和报告长度能力。"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from typing import Any, Iterable

from t1remote.core.capture_scope import collection_from_device_path, is_t1_device_path
from t1remote.core.hid_report_descriptor import (
    HidReportField,
    parse_hid_report_descriptor,
)
from t1remote.windows.hid_input import HIDP_CAPS, enumerate_hid_paths


GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


@dataclass(frozen=True)
class HidCollectionInfo:
    """一个 T1 HID Collection 的脱敏能力摘要。"""

    collection: str
    device_path: str
    usage_page: int
    usage: int
    input_report_length: int
    output_report_length: int
    feature_report_length: int
    input_button_capabilities: tuple["HidInputButtonCapability", ...] = ()
    report_descriptor: bytes = b""
    report_fields: tuple[HidReportField, ...] = ()


@dataclass(frozen=True)
class HidInputButtonCapability:
    """一个输入按钮字段的只读 HIDP 能力摘要。"""

    report_id: int
    usage_page: int
    usage_min: int
    usage_max: int
    is_range: bool
    report_count: int
    link_collection: int
    is_absolute: bool
    data_index_min: int = 0
    data_index_max: int = 0


@dataclass(frozen=True)
class HidInputData:
    """HidP_GetData 返回的一条 DataIndex 和原始值。"""

    data_index: int
    raw_value: int


@dataclass(frozen=True)
class HidInputButtonMatch:
    """DataIndex 命中的一个 HIDP_BUTTON_CAPS Usage 候选。"""

    capability_index: int
    report_id: int
    usage_page: int
    usage: int


@dataclass(frozen=True)
class HidInputDataDescription:
    """一条 HidP_GetData 结果及其可证明的 Usage 候选。"""

    data_index: int
    raw_value: int
    button_matches: tuple[HidInputButtonMatch, ...] = ()


class _HidpButtonRange(ctypes.Structure):
    """HIDP_BUTTON_CAPS.Range 的 ctypes 布局。"""

    _fields_ = (
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    )


class _HidpButtonNotRange(ctypes.Structure):
    """HIDP_BUTTON_CAPS.NotRange 的 ctypes 布局。"""

    _fields_ = (
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    )


class _HidpButtonUsageUnion(ctypes.Union):
    """HIDP_BUTTON_CAPS 尾部 Usage 联合体。"""

    _fields_ = (
        ("Range", _HidpButtonRange),
        ("NotRange", _HidpButtonNotRange),
    )


class _HidpButtonCaps(ctypes.Structure):
    """HIDP_BUTTON_CAPS 的固定布局，用于只读解析。"""

    _fields_ = (
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("Reserved", wintypes.ULONG * 9),
        ("Usage", _HidpButtonUsageUnion),
    )


class _HidpData(ctypes.Structure):
    """HIDP_DATA 的 ctypes 布局；Reserved 和联合体保持原始大小。"""

    _fields_ = (
        ("DataIndex", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("RawValue", wintypes.ULONG),
    )


HIDP_INPUT = 0


def _read_input_button_capabilities(
    hid: ctypes.WinDLL,
    preparsed_data: ctypes.c_void_p,
    caps: HIDP_CAPS,
) -> tuple[HidInputButtonCapability, ...]:
    """读取输入按钮字段；失败时返回空摘要，不影响基础能力探测。"""

    count = int(caps.NumberInputButtonCaps)
    if count <= 0:
        return ()
    hid.HidP_GetButtonCaps.restype = ctypes.c_int32
    hid.HidP_GetButtonCaps.argtypes = [
        wintypes.USHORT,
        ctypes.POINTER(_HidpButtonCaps),
        ctypes.POINTER(wintypes.USHORT),
        ctypes.c_void_p,
    ]
    values = (_HidpButtonCaps * count)()
    actual_count = wintypes.USHORT(count)
    status = int(
        hid.HidP_GetButtonCaps(
            HIDP_INPUT,
            values,
            ctypes.byref(actual_count),
            preparsed_data,
        )
    )
    if status < 0:
        return ()
    result: list[HidInputButtonCapability] = []
    for item in values[: actual_count.value]:
        is_range = bool(item.IsRange)
        if is_range:
            usage_min = int(item.Usage.Range.UsageMin)
            usage_max = int(item.Usage.Range.UsageMax)
            data_index_min = int(item.Usage.Range.DataIndexMin)
            data_index_max = int(item.Usage.Range.DataIndexMax)
        else:
            usage_min = usage_max = int(item.Usage.NotRange.Usage)
            data_index_min = data_index_max = int(item.Usage.NotRange.DataIndex)
        result.append(
            HidInputButtonCapability(
                report_id=int(item.ReportID),
                usage_page=int(item.UsagePage),
                usage_min=usage_min,
                usage_max=usage_max,
                is_range=is_range,
                report_count=int(item.ReportCount),
                link_collection=int(item.LinkCollection),
                is_absolute=bool(item.IsAbsolute),
                data_index_min=data_index_min,
                data_index_max=data_index_max,
            )
        )
    return tuple(result)


def inspect_preparsed_data(
    preparsed_data: bytes | bytearray | memoryview,
    *,
    collection: str = "",
    device_path: str = "",
) -> HidCollectionInfo:
    """用 Windows HID parser 读取桥接返回的 opaque preparsed data。"""

    if os.name != "nt":
        raise RuntimeError("HID preparsed data 探测只能在 Windows 上运行")
    if not isinstance(preparsed_data, (bytes, bytearray, memoryview)):
        raise TypeError("preparsed_data 必须是 bytes-like 对象")
    data = bytes(preparsed_data)
    if not data:
        raise ValueError("preparsed_data 不能为空")

    hid = ctypes.WinDLL("hid", use_last_error=True)
    hid.HidP_GetCaps.restype = ctypes.c_int32
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    buffer = ctypes.create_string_buffer(data)
    preparsed_pointer = ctypes.cast(buffer, ctypes.c_void_p)
    caps = HIDP_CAPS()
    status = int(hid.HidP_GetCaps(preparsed_pointer, ctypes.byref(caps)))
    if status < 0:
        raise RuntimeError(f"HidP_GetCaps 失败：0x{status & 0xFFFFFFFF:08X}")

    input_button_capabilities = _read_input_button_capabilities(
        hid,
        preparsed_pointer,
        caps,
    )
    return HidCollectionInfo(
        collection=collection.upper(),
        device_path=device_path,
        usage_page=int(caps.UsagePage),
        usage=int(caps.Usage),
        input_report_length=int(caps.InputReportByteLength),
        output_report_length=int(caps.OutputReportByteLength),
        feature_report_length=int(caps.FeatureReportByteLength),
        input_button_capabilities=input_button_capabilities,
    )


def parse_input_data(
    preparsed_data: bytes | bytearray | memoryview,
    report: bytes | bytearray | memoryview,
) -> tuple[HidInputData, ...]:
    """用 HidP_GetData 提取输入报告的 DataIndex 和原始值。"""

    if os.name != "nt":
        raise RuntimeError("HID input data 解析只能在 Windows 上运行")
    if not isinstance(preparsed_data, (bytes, bytearray, memoryview)):
        raise TypeError("preparsed_data 必须是 bytes-like 对象")
    if not isinstance(report, (bytes, bytearray, memoryview)):
        raise TypeError("report 必须是 bytes-like 对象")
    preparsed_bytes = bytes(preparsed_data)
    report_bytes = bytes(report)
    if not preparsed_bytes:
        raise ValueError("preparsed_data 不能为空")
    if not report_bytes:
        raise ValueError("report 不能为空")

    hid = ctypes.WinDLL("hid", use_last_error=True)
    hid.HidP_MaxDataListLength.restype = wintypes.ULONG
    hid.HidP_MaxDataListLength.argtypes = [wintypes.USHORT, ctypes.c_void_p]
    hid.HidP_GetData.restype = ctypes.c_int32
    hid.HidP_GetData.argtypes = [
        wintypes.USHORT,
        ctypes.POINTER(_HidpData),
        ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        wintypes.ULONG,
    ]
    preparsed_buffer = ctypes.create_string_buffer(preparsed_bytes)
    preparsed_pointer = ctypes.cast(preparsed_buffer, ctypes.c_void_p)
    max_data_length = int(
        hid.HidP_MaxDataListLength(HIDP_INPUT, preparsed_pointer)
    )
    if max_data_length <= 0:
        raise RuntimeError("HidP_MaxDataListLength 返回了无效长度")
    data_list = (_HidpData * max_data_length)()
    data_length = wintypes.ULONG(max_data_length)
    report_buffer = (ctypes.c_ubyte * len(report_bytes)).from_buffer_copy(
        report_bytes
    )
    status = int(
        hid.HidP_GetData(
            HIDP_INPUT,
            data_list,
            ctypes.byref(data_length),
            preparsed_pointer,
            report_buffer,
            len(report_bytes),
        )
    )
    if status < 0:
        raise RuntimeError(f"HidP_GetData 失败：0x{status & 0xFFFFFFFF:08X}")
    return tuple(
        HidInputData(
            data_index=int(item.DataIndex),
            raw_value=int(item.RawValue),
        )
        for item in data_list[: int(data_length.value)]
    )


def describe_input_data(
    input_data: Iterable[HidInputData],
    capabilities: Iterable[HidInputButtonCapability],
) -> tuple[HidInputDataDescription, ...]:
    """按 HIDP_BUTTON_CAPS 将 DataIndex 映射为可审计的 Usage 候选。

    Microsoft 定义范围型能力的 DataIndex 与 Usage 是一一对应且顺序一致的。
    这里仅使用该关系生成候选；找不到或出现多个候选时都保留证据，不猜测
    字节偏移、Report ID 或业务名称。
    """

    capability_list = tuple(capabilities)
    descriptions: list[HidInputDataDescription] = []
    for item in input_data:
        if not isinstance(item, HidInputData):
            raise TypeError("input_data 必须只包含 HidInputData")
        matches: list[HidInputButtonMatch] = []
        for capability_index, capability in enumerate(capability_list):
            if not isinstance(capability, HidInputButtonCapability):
                raise TypeError("capabilities 必须只包含 HidInputButtonCapability")
            if not (
                capability.data_index_min
                <= item.data_index
                <= capability.data_index_max
            ):
                continue
            if capability.is_range:
                data_span = (
                    capability.data_index_max - capability.data_index_min + 1
                )
                usage_span = capability.usage_max - capability.usage_min + 1
                if data_span != usage_span:
                    continue
                usage = capability.usage_min + (
                    item.data_index - capability.data_index_min
                )
            else:
                if capability.usage_min != capability.usage_max:
                    continue
                usage = capability.usage_min
            matches.append(
                HidInputButtonMatch(
                    capability_index=capability_index,
                    report_id=capability.report_id,
                    usage_page=capability.usage_page,
                    usage=usage,
                )
            )
        descriptions.append(
            HidInputDataDescription(
                data_index=item.data_index,
                raw_value=item.raw_value,
                button_matches=tuple(matches),
            )
        )
    return tuple(descriptions)


def inspect_hid_collections(
    target_collections: Iterable[str] = ("COL01", "COL02", "COL03", "COL04", "COL05"),
) -> tuple[HidCollectionInfo, ...]:
    """枚举目标 T1 Collection，并读取 HIDP_CAPS。"""

    if os.name != "nt":
        raise RuntimeError("HID Collection 探测只能在 Windows 上运行")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hid = ctypes.WinDLL("hid", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    hid.HidD_GetPreparsedData.restype = wintypes.BOOL
    hid.HidD_GetPreparsedData.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    hid.HidD_FreePreparsedData.restype = wintypes.BOOL
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid.HidP_GetCaps.restype = ctypes.c_int32
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]

    infos: list[HidCollectionInfo] = []
    for collection, path in enumerate_hid_paths(target_collections):
        if not is_t1_device_path(path):
            continue
        handle = kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if not handle or int(handle) == invalid_handle:
            continue
        preparsed_data = ctypes.c_void_p()
        try:
            if not hid.HidD_GetPreparsedData(
                ctypes.c_void_p(handle), ctypes.byref(preparsed_data)
            ):
                continue
            caps = HIDP_CAPS()
            if int(hid.HidP_GetCaps(preparsed_data, ctypes.byref(caps))) < 0:
                continue
            input_button_capabilities = _read_input_button_capabilities(
                hid,
                preparsed_data,
                caps,
            )
            infos.append(
                HidCollectionInfo(
                    collection=collection,
                    device_path=path,
                    usage_page=int(caps.UsagePage),
                    usage=int(caps.Usage),
                    input_report_length=int(caps.InputReportByteLength),
                    output_report_length=int(caps.OutputReportByteLength),
                    feature_report_length=int(caps.FeatureReportByteLength),
                    input_button_capabilities=input_button_capabilities,
                )
            )
        finally:
            if preparsed_data:
                hid.HidD_FreePreparsedData(preparsed_data)
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    return tuple(infos)


def summarize_hid_collections(
    infos: Iterable[HidCollectionInfo],
) -> list[dict[str, Any]]:
    """转换为不包含完整设备路径的 JSON 摘要。"""

    summaries: list[dict[str, Any]] = []
    for info in infos:
        report_fields = info.report_fields
        if not report_fields and info.report_descriptor:
            try:
                report_fields = parse_hid_report_descriptor(info.report_descriptor).fields
            except (HidReportDescriptorError, TypeError):
                report_fields = ()
        summaries.append(
            {
                "collection": info.collection or collection_from_device_path(info.device_path),
                "usage_page": f"0x{info.usage_page:02X}",
                "usage": f"0x{info.usage:02X}",
                "input_report_length": info.input_report_length,
                "output_report_length": info.output_report_length,
                "feature_report_length": info.feature_report_length,
                "report_descriptor_length": len(info.report_descriptor),
                "report_descriptor_hex": info.report_descriptor.hex(" "),
                "report_descriptor_status": _report_descriptor_status(info),
                "report_fields": [field.to_dict() for field in report_fields],
                "input_button_capabilities": [
                    {
                        "report_id": capability.report_id,
                        "usage_page": f"0x{capability.usage_page:02X}",
                        "usage_min": f"0x{capability.usage_min:02X}",
                        "usage_max": f"0x{capability.usage_max:02X}",
                        "is_range": capability.is_range,
                        "report_count": capability.report_count,
                        "link_collection": capability.link_collection,
                        "is_absolute": capability.is_absolute,
                        "data_index_min": capability.data_index_min,
                        "data_index_max": capability.data_index_max,
                    }
                    for capability in info.input_button_capabilities
                ],
                "device_family": f"T1-Remote/{info.collection}",
            }
        )
    return summaries


def _report_descriptor_status(info: HidCollectionInfo) -> str:
    """区分描述符可用、损坏和当前接口无法取得三种现场状态。"""

    if not info.report_descriptor:
        return "descriptor_unavailable"
    try:
        parse_hid_report_descriptor(info.report_descriptor)
    except (HidReportDescriptorError, TypeError, ValueError):
        return "descriptor_invalid"
    return "descriptor_available"


__all__ = [
    "HidCollectionInfo",
    "HidInputButtonCapability",
    "HidInputButtonMatch",
    "HidInputData",
    "HidInputDataDescription",
    "describe_input_data",
    "inspect_preparsed_data",
    "inspect_hid_collections",
    "parse_input_data",
    "summarize_hid_collections",
]
