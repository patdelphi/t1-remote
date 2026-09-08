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
            infos.append(
                HidCollectionInfo(
                    collection=collection,
                    device_path=path,
                    usage_page=int(caps.UsagePage),
                    usage=int(caps.Usage),
                    input_report_length=int(caps.InputReportByteLength),
                    output_report_length=int(caps.OutputReportByteLength),
                    feature_report_length=int(caps.FeatureReportByteLength),
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
            "device_family": f"T1-Remote/{info.collection}",
        }
        for info in infos
    ]


__all__ = ["HidCollectionInfo", "inspect_hid_collections", "summarize_hid_collections"]
