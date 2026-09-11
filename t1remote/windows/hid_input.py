"""程序说明：枚举 T1 HID Collection，并用 Win32 ReadFile 持续读取输入报告。"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import re
import threading
import uuid
from typing import Callable, Iterable

from t1remote.core.capture_scope import (
    collection_from_device_path,
    is_t1_device_path,
)


HID_INTERFACE_GUID = "4d1e55b2-f16f-11cf-88cb-001111000030"
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
ERROR_INVALID_HANDLE = 6
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
DEFAULT_REPORT_LENGTH = 64
MAX_REPORT_LENGTH = 4096


class GUID(ctypes.Structure):
    """SetupAPI 使用的 Windows GUID 布局。"""

    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    """SetupDiEnumDeviceInterfaces 的输出结构。"""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    )


class OVERLAPPED(ctypes.Structure):
    """ReadFile 异步调用使用的重叠 I/O 结构。"""

    _fields_ = (
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


class HIDP_CAPS(ctypes.Structure):
    """HidP_GetCaps 所需的最小完整 HIDP_CAPS 布局。"""

    _fields_ = (
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    )


@dataclass(frozen=True)
class HidInputEvent:
    """一次 HID Collection 直读得到的原始输入报告。"""

    device_path: str
    collection: str
    report: bytes


@dataclass
class _Reader:
    """一个设备句柄和它的后台读取线程。"""

    collection: str
    device_path: str
    handle: int
    event: int
    report_length: int
    thread: threading.Thread | None = None


def normalize_target_collections(collections: Iterable[str]) -> tuple[str, ...]:
    """校验并规范化目标 Collection 名称，同时按输入顺序去重。"""

    normalized_collections: list[str] = []
    for collection in collections:
        normalized = str(collection).upper()
        if not re.fullmatch(r"COL\d{2}", normalized):
            raise ValueError("target Collection 必须使用 COL01 形式")
        if not 1 <= int(normalized[3:]) < 32:
            raise ValueError("target Collection 编号必须在 1-31 范围内")
        if normalized not in normalized_collections:
            normalized_collections.append(normalized)
    if not normalized_collections:
        raise ValueError("至少需要一个 target Collection")
    return tuple(normalized_collections)


def filter_target_hid_paths(
    paths: Iterable[str],
    target_collections: Iterable[str],
) -> list[tuple[str, str]]:
    """筛出 T1 目标 Collection 路径，并按完整路径去重。"""

    targets = set(normalize_target_collections(target_collections))
    matched: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for path in paths:
        collection = collection_from_device_path(path)
        if not is_t1_device_path(path) or collection not in targets:
            continue
        path_key = path.upper()
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        matched.append((collection, path))
    return matched


def _interface_guid() -> GUID:
    """将 HID 设备接口 GUID 转为 SetupAPI 可用的 ctypes 结构。"""

    return GUID.from_buffer_copy(uuid.UUID(HID_INTERFACE_GUID).bytes_le)


def enumerate_hid_paths(
    target_collections: Iterable[str] = ("COL02", "COL03"),
) -> list[tuple[str, str]]:
    """通过 SetupAPI 枚举当前机器上 T1 目标 HID Collection。"""

    if os.name != "nt":
        raise RuntimeError("T1 HID 直读监听器只能在 Windows 上运行")

    targets = normalize_target_collections(target_collections)
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    guid = _interface_guid()
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid),
        None,
        None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not info_set or int(info_set) == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    paths: list[str] = []
    try:
        index = 0
        while True:
            interface_data = SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            result = setupapi.SetupDiEnumDeviceInterfaces(
                info_set,
                None,
                ctypes.byref(guid),
                index,
                ctypes.byref(interface_data),
            )
            if not result:
                error_code = ctypes.get_last_error()
                if error_code == ERROR_NO_MORE_ITEMS:
                    break
                raise ctypes.WinError(error_code)

            required_size = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface_data),
                None,
                0,
                ctypes.byref(required_size),
                None,
            )
            if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
                raise ctypes.WinError(ctypes.get_last_error())
            # SetupAPI 在 x64 要求 cbSize=8，但 Unicode DevicePath 实际紧跟
            # DWORD 从偏移 4 开始；两者不能共用同一个偏移量。
            detail_cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            detail_offset = 4
            detail_buffer = ctypes.create_string_buffer(
                max(required_size.value, detail_offset + ctypes.sizeof(ctypes.c_wchar))
            )
            ctypes.cast(detail_buffer, ctypes.POINTER(wintypes.DWORD))[0] = (
                detail_cb_size
            )
            result = setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface_data),
                ctypes.cast(detail_buffer, ctypes.c_void_p),
                required_size.value,
                ctypes.byref(required_size),
                None,
            )
            if not result:
                raise ctypes.WinError(ctypes.get_last_error())
            path = ctypes.wstring_at(
                ctypes.addressof(detail_buffer) + detail_offset
            )
            paths.append(path)
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)

    return filter_target_hid_paths(paths, targets)


class HidInputListener:
    """为指定 HID Collection 发起持续的异步 ReadFile 请求。"""

    def __init__(
        self,
        on_event: Callable[[HidInputEvent], None],
        on_error: Callable[[Exception], None] | None = None,
        target_collections: Iterable[str] = ("COL02", "COL03"),
        path_enumerator: Callable[
            [Iterable[str]], list[tuple[str, str]]
        ] = enumerate_hid_paths,
    ) -> None:
        self._on_event = on_event
        self._on_error = on_error
        self._target_collections = normalize_target_collections(target_collections)
        self._path_enumerator = path_enumerator
        self._readers: list[_Reader] = []
        self._lock = threading.Lock()
        # 启动和停止共享同一组句柄，必须避免并发修改 readers。
        self._lifecycle_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._kernel32: ctypes.WinDLL | None = None
        self._hid: ctypes.WinDLL | None = None

    @property
    def is_running(self) -> bool:
        """返回监听器是否已建立至少一个后台读取线程。"""

        with self._lock:
            return any(reader.thread and reader.thread.is_alive() for reader in self._readers)

    def start(self) -> None:
        """枚举目标接口、打开句柄并启动后台读取线程。"""

        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        """在生命周期锁内完成监听器启动。"""

        if os.name != "nt":
            raise RuntimeError("T1 HID 直读监听器只能在 Windows 上运行")
        if self.is_running:
            raise RuntimeError("T1 HID 直读监听器已经启动")

        self._stop_requested.clear()
        self._configure_apis()
        candidates = self._path_enumerator(self._target_collections)
        if not candidates:
            raise RuntimeError("未找到 T1 COL02/COL03 HID 直读接口")

        opened: list[_Reader] = []
        errors: list[str] = []
        try:
            for collection, path in candidates:
                handle = 0
                event = 0
                try:
                    handle = self._open_handle(path)
                    event = self._create_event()
                    report_length = self._get_report_length(handle)
                    reader = _Reader(collection, path, handle, event, report_length)
                    reader.thread = threading.Thread(
                        target=self._reader_loop,
                        args=(reader,),
                        name=f"t1-hid-{collection.lower()}",
                        daemon=True,
                    )
                    opened.append(reader)
                except Exception as error:
                    if event:
                        self._close_handle(event)
                    if handle:
                        self._close_handle(handle)
                    errors.append(f"{collection}: {error}")
            if not opened:
                detail = "；".join(errors) or "没有可打开的 HID 接口"
                raise RuntimeError(f"无法打开 T1 HID 直读接口：{detail}")
            with self._lock:
                self._readers = opened
            for reader in opened:
                assert reader.thread is not None
                reader.thread.start()
            for error in errors:
                self._report_error(RuntimeError(error))
        except Exception:
            for reader in opened:
                self._close_reader(reader)
            raise

    def stop(self) -> None:
        """取消挂起的 ReadFile 请求，并等待读取线程退出。"""

        with self._lifecycle_lock:
            self._stop()

    def _stop(self) -> None:
        """在生命周期锁内完成监听器停止。"""

        self._stop_requested.set()
        with self._lock:
            readers = list(self._readers)
        for reader in readers:
            self._cancel_reader(reader)
        for reader in readers:
            if reader.thread:
                # CancelIoEx 不等待请求完成；必须等待读取线程退出后再关闭句柄。
                reader.thread.join()
            self._close_reader(reader)
        with self._lock:
            self._readers.clear()

    def _configure_apis(self) -> None:
        """初始化 Kernel32、HID API 函数签名，避免 ctypes 隐式截断句柄。"""

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._hid = ctypes.WinDLL("hid", use_last_error=True)
        kernel32 = self._kernel32
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
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(OVERLAPPED),
        ]
        kernel32.GetOverlappedResult.restype = wintypes.BOOL
        kernel32.GetOverlappedResult.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        kernel32.CancelIoEx.restype = wintypes.BOOL
        kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.ResetEvent.restype = wintypes.BOOL
        kernel32.ResetEvent.argtypes = [ctypes.c_void_p]

        hid = self._hid
        hid.HidD_GetPreparsedData.restype = wintypes.BOOL
        hid.HidD_GetPreparsedData.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        hid.HidD_FreePreparsedData.restype = wintypes.BOOL
        hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        hid.HidP_GetCaps.restype = ctypes.c_int32
        hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]

    def _open_handle(self, path: str) -> int:
        """以重叠读方式打开一个 HID Collection 接口。"""

        if not self._kernel32:
            raise RuntimeError("Kernel32 API 尚未初始化")
        handle = self._kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if not handle or int(handle) == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def _create_event(self) -> int:
        """创建手动复位事件，供一个 ReadFile 请求等待完成。"""

        if not self._kernel32:
            raise RuntimeError("Kernel32 API 尚未初始化")
        event = self._kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(event)

    def _get_report_length(self, handle: int) -> int:
        """从 HID 报告描述符读取输入报告长度，失败时使用安全上限。"""

        if not self._hid:
            return DEFAULT_REPORT_LENGTH
        preparsed_data = ctypes.c_void_p()
        if not self._hid.HidD_GetPreparsedData(
            ctypes.c_void_p(handle), ctypes.byref(preparsed_data)
        ):
            return DEFAULT_REPORT_LENGTH
        try:
            caps = HIDP_CAPS()
            status = int(self._hid.HidP_GetCaps(preparsed_data, ctypes.byref(caps)))
            report_length = int(caps.InputReportByteLength)
            if status < 0 or not 0 < report_length <= MAX_REPORT_LENGTH:
                return DEFAULT_REPORT_LENGTH
            return report_length
        finally:
            self._hid.HidD_FreePreparsedData(preparsed_data)

    def _reader_loop(self, reader: _Reader) -> None:
        """持续读取一个 Collection，并把每个完成的报告交给调用方。"""

        if not self._kernel32:
            return
        kernel32 = self._kernel32
        buffer = ctypes.create_string_buffer(reader.report_length)
        while not self._stop_requested.is_set():
            kernel32.ResetEvent(ctypes.c_void_p(reader.event))
            overlapped = OVERLAPPED(hEvent=reader.event)
            bytes_read = wintypes.DWORD(0)
            result = kernel32.ReadFile(
                ctypes.c_void_p(reader.handle),
                buffer,
                reader.report_length,
                None,
                ctypes.byref(overlapped),
            )
            if not result:
                error_code = ctypes.get_last_error()
                if error_code != ERROR_IO_PENDING:
                    if error_code not in (ERROR_OPERATION_ABORTED, ERROR_INVALID_HANDLE):
                        self._report_error(ctypes.WinError(error_code))
                    return

                # 超时只表示报告还未到达，不能在同一句柄上再次发起并发 ReadFile。
                while True:
                    wait_result = kernel32.WaitForSingleObject(
                        ctypes.c_void_p(reader.event), 100
                    )
                    if wait_result == WAIT_TIMEOUT:
                        if self._stop_requested.is_set():
                            self._cancel_reader(reader)
                        continue
                    if wait_result != WAIT_OBJECT_0:
                        self._report_error(
                            RuntimeError(f"等待 HID 报告失败，结果码：{wait_result}")
                        )
                        return
                    break
            if not kernel32.GetOverlappedResult(
                ctypes.c_void_p(reader.handle),
                ctypes.byref(overlapped),
                ctypes.byref(bytes_read),
                False,
            ):
                error_code = ctypes.get_last_error()
                if error_code not in (ERROR_OPERATION_ABORTED, ERROR_INVALID_HANDLE):
                    self._report_error(ctypes.WinError(error_code))
                return

            if self._stop_requested.is_set():
                return
            report = bytes(buffer[: bytes_read.value])
            if not report:
                continue
            try:
                self._on_event(HidInputEvent(reader.device_path, reader.collection, report))
            except Exception as error:
                self._report_error(error)

    def _cancel_reader(self, reader: _Reader) -> None:
        """取消一个可能挂起的重叠读请求。"""

        if not self._kernel32:
            return
        self._kernel32.CancelIoEx(ctypes.c_void_p(reader.handle), None)

    def _close_reader(self, reader: _Reader) -> None:
        """在读取线程退出后关闭句柄和事件。"""

        if reader.handle:
            self._close_handle(reader.handle)
            reader.handle = 0
        if reader.event:
            self._close_handle(reader.event)
            reader.event = 0

    def _close_handle(self, handle: int) -> None:
        """关闭临时打开的 Win32 句柄。"""

        if self._kernel32 and handle:
            self._kernel32.CloseHandle(ctypes.c_void_p(handle))

    def _report_error(self, error: Exception) -> None:
        """把底层异常交给调用方，避免回调异常打断监听线程。"""

        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                pass


__all__ = [
    "HidInputEvent",
    "HidInputListener",
    "enumerate_hid_paths",
    "filter_target_hid_paths",
    "normalize_target_collections",
]
