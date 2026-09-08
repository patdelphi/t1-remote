"""程序说明：只读枚举 T1 HID Collection 的 Usage 和报告长度能力。"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from typing import Any, Iterable

from t1remote.core.capture_scope import collection_from_device_path, is_t1_device_path
from t1remote.windows.hid_input import HIDP_CAPS, enumerate_hid_paths


GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B0007
MAX_REPORT_DESCRIPTOR_BYTES = 4096


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
        else:
            usage_min = usage_max = int(item.Usage.NotRange.Usage)
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
            )
        )
    return tuple(result)


def _read_report_descriptor(
    kernel32: ctypes.WinDLL,
    handle: int,
) -> bytes:
    """通过只读 HID IOCTL 取得报告描述符；当前接口失败时返回空字节串。"""

    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    buffer = ctypes.create_string_buffer(MAX_REPORT_DESCRIPTOR_BYTES)
    bytes_returned = wintypes.DWORD(0)
    if not kernel32.DeviceIoControl(
        ctypes.c_void_p(handle),
        IOCTL_HID_GET_REPORT_DESCRIPTOR,
        None,
        0,
        buffer,
        MAX_REPORT_DESCRIPTOR_BYTES,
        ctypes.byref(bytes_returned),
        None,
    ):
        return b""
    size = min(int(bytes_returned.value), MAX_REPORT_DESCRIPTOR_BYTES)
    return bytes(buffer.raw[:size])


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
            report_descriptor = _read_report_descriptor(kernel32, int(handle))
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
                    report_descriptor=report_descriptor,
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

    return [
        {
            "collection": info.collection or collection_from_device_path(info.device_path),
            "usage_page": f"0x{info.usage_page:02X}",
            "usage": f"0x{info.usage:02X}",
            "input_report_length": info.input_report_length,
            "output_report_length": info.output_report_length,
            "feature_report_length": info.feature_report_length,
            "report_descriptor_length": len(info.report_descriptor),
            "report_descriptor_hex": info.report_descriptor.hex(" "),
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
                }
                for capability in info.input_button_capabilities
            ],
            "device_family": f"T1-Remote/{info.collection}",
        }
        for info in infos
    ]


__all__ = [
    "HidCollectionInfo",
    "HidInputButtonCapability",
    "inspect_hid_collections",
    "summarize_hid_collections",
]
