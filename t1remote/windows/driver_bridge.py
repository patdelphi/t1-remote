"""程序说明：定义 Python 与 T1 原生拦截桥接 DLL 之间的最小稳定协议。

本模块只负责设备身份、Usage 策略和 DLL 生命周期，不声称自己可以拦截
Windows 输入。真正的吞键逻辑必须位于签名的 HID 过滤驱动中。
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from pathlib import Path
import os
from typing import Callable, Iterable


BRIDGE_ABI_VERSION = 1
MAX_BLOCKED_USAGES = 32
MAX_TARGET_COLLECTIONS = 8
MAX_REPORT_BYTES = 64
T1_VID = 0x620A
T1_PID = 0x0407
ERROR_NO_MORE_ITEMS = 259

FLAG_ENABLED = 0x0001
FLAG_DROP_UNMAPPED = 0x0002
FLAG_REMAP = 0x0004
CAPABILITY_REPORT_REMAP = 0x00000008

_COLLECTION_PATTERN = "COL"


class BridgeError(RuntimeError):
    """桥接 DLL 或驱动返回了可识别的错误。"""


class BridgeUnavailable(BridgeError):
    """当前机器没有可加载的桥接 DLL 或不满足 Windows 条件。"""


class BridgeProtocolError(BridgeError):
    """桥接 DLL 的 ABI 与 Python 端不兼容。"""


@dataclass(frozen=True)
class HidUsage:
    """一个 HID Usage，可选绑定到具体 Collection。"""

    usage_page: int
    usage: int
    collection: str | None = None
    mapped_usage: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.usage_page <= 0xFFFF:
            raise ValueError("usage_page 必须在 0x0000-0xFFFF 范围内")
        if not 0 <= self.usage <= 0xFFFF:
            raise ValueError("usage 必须在 0x0000-0xFFFF 范围内")
        if self.mapped_usage is not None and not 0 <= self.mapped_usage <= 0xFFFF:
            raise ValueError("mapped_usage 必须在 0x0000-0xFFFF 范围内")
        if self.collection is not None:
            normalized = self.collection.upper()
            if not normalized.startswith(_COLLECTION_PATTERN) or not normalized[3:].isdigit():
                raise ValueError("collection 必须使用 COL01 形式")
            number = int(normalized[3:])
            if not 0 <= number <= 0xFFFF:
                raise ValueError("collection 编号超出范围")
            object.__setattr__(self, "collection", normalized)


class NativeHidUsage(ctypes.Structure):
    """与 t1bridge.dll 对齐的 C 结构。"""

    _pack_ = 1
    _fields_ = (
        ("usage_page", ctypes.c_uint16),
        ("usage", ctypes.c_uint16),
        ("collection", ctypes.c_uint16),
        ("mapped_usage", ctypes.c_uint16),
    )


class NativeBridgePolicy(ctypes.Structure):
    """桥接策略的固定大小 ABI 结构。"""

    _pack_ = 1
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("vid", ctypes.c_uint16),
        ("pid", ctypes.c_uint16),
        ("flags", ctypes.c_uint32),
        ("usage_count", ctypes.c_uint32),
        ("target_collection_count", ctypes.c_uint32),
        ("target_collections", ctypes.c_uint16 * MAX_TARGET_COLLECTIONS),
        ("usages", NativeHidUsage * MAX_BLOCKED_USAGES),
    )


class NativeBridgeStatus(ctypes.Structure):
    """驱动状态的固定大小 ABI 结构。"""

    _pack_ = 1
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("state", ctypes.c_uint32),
        ("last_error", ctypes.c_int32),
        ("dropped_reports", ctypes.c_uint64),
    )


class NativeBridgeCapabilities(ctypes.Structure):
    """驱动运行时能力结构。"""

    _pack_ = 1
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("max_blocked_usages", ctypes.c_uint32),
        ("max_target_collections", ctypes.c_uint32),
        ("max_report_bytes", ctypes.c_uint32),
        ("event_queue_capacity", ctypes.c_uint32),
    )


class NativeBridgeStats(ctypes.Structure):
    """驱动运行时统计结构。"""

    _pack_ = 1
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("received_reports", ctypes.c_uint64),
        ("blocked_reports", ctypes.c_uint64),
        ("queued_events", ctypes.c_uint64),
        ("dropped_events", ctypes.c_uint64),
        ("buffer_errors", ctypes.c_uint64),
        ("queue_depth", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    )


class NativeBridgeEvent(ctypes.Structure):
    """与驱动事件队列对齐的固定布局结构。"""

    _pack_ = 1
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("sequence", ctypes.c_uint64),
        ("usage_page", ctypes.c_uint16),
        ("usage", ctypes.c_uint16),
        ("collection", ctypes.c_uint16),
        ("report_length", ctypes.c_uint16),
        ("report", ctypes.c_ubyte * MAX_REPORT_BYTES),
    )


@dataclass(frozen=True)
class InterceptionPolicy:
    """只允许 T1 VID/PID 的 HID 过滤策略。"""

    blocked_usages: tuple[HidUsage, ...] = ()
    target_collections: tuple[str, ...] = ("COL01", "COL02", "COL04", "COL05")
    enabled: bool = True
    drop_unmapped: bool = False
    remap_enabled: bool = True
    vid: int = T1_VID
    pid: int = T1_PID

    def __post_init__(self) -> None:
        if self.vid != T1_VID or self.pid != T1_PID:
            raise ValueError("当前策略只允许匹配 T1 VID=0x620A、PID=0x0407")
        if len(self.blocked_usages) > MAX_BLOCKED_USAGES:
            raise ValueError(f"blocked_usages 不能超过 {MAX_BLOCKED_USAGES} 项")
        if len(self.target_collections) > MAX_TARGET_COLLECTIONS:
            raise ValueError(
                f"target_collections 不能超过 {MAX_TARGET_COLLECTIONS} 项"
            )
        source_keys = {
            (usage.usage_page, usage.usage, usage.collection)
            for usage in self.blocked_usages
        }
        if len(source_keys) != len(self.blocked_usages):
            raise ValueError("同一个源 Usage 不允许配置多条映射")

        normalized_collections: list[str] = []
        for collection in self.target_collections:
            normalized = collection.upper()
            if not normalized.startswith(_COLLECTION_PATTERN) or not normalized[3:].isdigit():
                raise ValueError("target_collections 必须使用 COL01 形式")
            if _collection_number(normalized) > 0xFFFF:
                raise ValueError("target_collections 编号超出范围")
            if normalized not in normalized_collections:
                normalized_collections.append(normalized)
        object.__setattr__(self, "target_collections", tuple(normalized_collections))

    def to_native(self) -> NativeBridgePolicy:
        """转换成固定布局结构，供 ctypes 传给 DLL。"""

        native = NativeBridgePolicy()
        native.size = ctypes.sizeof(NativeBridgePolicy)
        native.abi_version = BRIDGE_ABI_VERSION
        native.vid = self.vid
        native.pid = self.pid
        native.flags = 0
        if self.enabled:
            native.flags |= FLAG_ENABLED
        if self.drop_unmapped:
            native.flags |= FLAG_DROP_UNMAPPED
        if self.remap_enabled:
            native.flags |= FLAG_REMAP
        native.usage_count = len(self.blocked_usages)
        native.target_collection_count = len(self.target_collections)
        for index, collection in enumerate(self.target_collections):
            native.target_collections[index] = _collection_number(collection)

        default_collection = 0
        if len(self.target_collections) == 1:
            default_collection = _collection_number(self.target_collections[0])
        for index, usage in enumerate(self.blocked_usages):
            collection = usage.collection
            collection_number = (
                _collection_number(collection)
                if collection is not None
                else default_collection
            )
            native.usages[index] = NativeHidUsage(
                usage.usage_page,
                usage.usage,
                collection_number,
                usage.mapped_usage or 0,
            )
        return native


@dataclass(frozen=True)
class BridgeStatus:
    """面向 UI 的桥接状态，不暴露 ctypes 结构。"""

    state: str
    last_error: int
    dropped_reports: int
    abi_version: int

    @classmethod
    def from_native(cls, native: NativeBridgeStatus | object) -> "BridgeStatus":
        """将 C 状态码转换为稳定的字符串状态。"""

        state_names = {
            0: "unknown",
            1: "stopped",
            2: "running",
            3: "error",
        }
        state_value = int(getattr(native, "state"))
        return cls(
            state=state_names.get(state_value, "unknown"),
            last_error=int(getattr(native, "last_error")),
            dropped_reports=int(getattr(native, "dropped_reports")),
            abi_version=int(getattr(native, "abi_version")),
        )


@dataclass(frozen=True)
class BridgeCapabilities:
    """驱动当前支持的运行时能力。"""

    flags: int
    max_blocked_usages: int
    max_target_collections: int
    max_report_bytes: int
    event_queue_capacity: int
    abi_version: int


@dataclass(frozen=True)
class BridgeStats:
    """驱动报告接收、拦截和队列统计。"""

    received_reports: int
    blocked_reports: int
    queued_events: int
    dropped_events: int
    buffer_errors: int
    queue_depth: int
    abi_version: int


@dataclass(frozen=True)
class DriverInputEvent:
    """驱动保存的原始 T1 输入事件。"""

    sequence: int
    usage_page: int
    usage: int
    collection: str
    report: bytes


def _collection_number(collection: str) -> int:
    """把 COL01 转成驱动协议中的数字编号。"""

    normalized = collection.upper()
    if not normalized.startswith(_COLLECTION_PATTERN) or not normalized[3:].isdigit():
        raise ValueError("collection 必须使用 COL01 形式")
    return int(normalized[3:])


def _configure_function(library: object, name: str, restype: object, argtypes: list[object]) -> object:
    """配置一个导出函数；测试替身没有 ctypes 属性时也保持可调用。"""

    try:
        function = getattr(library, name)
    except AttributeError as exc:
        raise BridgeProtocolError(f"桥接 DLL 缺少导出函数：{name}") from exc
    try:
        function.restype = restype
        function.argtypes = argtypes
    except (AttributeError, TypeError):
        # Python 测试替身不需要 ctypes 的签名属性。
        pass
    return function


class T1BridgeClient:
    """调用 t1bridge.dll 的安全生命周期封装。"""

    def __init__(
        self,
        dll_path: str | Path | None = None,
        *,
        library_loader: Callable[[str], object] | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self._dll_path = Path(dll_path) if dll_path else None
        self._library_loader = library_loader
        self._is_windows = os.name == "nt" if is_windows is None else is_windows
        self._library: object | None = None
        self._handle = ctypes.c_void_p()
        self._policy: InterceptionPolicy | None = None

    @property
    def is_open(self) -> bool:
        """返回桥接句柄是否已经打开。"""

        return bool(self._handle.value)

    def open(self, policy: InterceptionPolicy) -> None:
        """加载 DLL、校验 ABI 并打开驱动会话。"""

        if self.is_open:
            raise BridgeError("T1 桥接会话已经打开")
        if not self._is_windows:
            raise BridgeUnavailable("设备级拦截桥接只能在 Windows 上运行")

        library = self._load_library()
        get_abi = _configure_function(
            library,
            "T1Bridge_GetAbiVersion",
            ctypes.c_uint32,
            [],
        )
        abi_version = int(get_abi())
        if abi_version != BRIDGE_ABI_VERSION:
            raise BridgeProtocolError(
                f"桥接 ABI 不兼容：需要 {BRIDGE_ABI_VERSION}，实际为 {abi_version}"
            )

        open_function = _configure_function(
            library,
            "T1Bridge_Open",
            ctypes.c_int32,
            [ctypes.POINTER(NativeBridgePolicy), ctypes.POINTER(ctypes.c_void_p)],
        )
        native_policy = policy.to_native()
        handle = ctypes.c_void_p()
        result = int(open_function(ctypes.byref(native_policy), ctypes.byref(handle)))
        self._raise_if_failed("T1Bridge_Open", result)
        if not handle.value:
            raise BridgeError("桥接 DLL 返回了空句柄")
        self._library = library
        self._handle = handle
        self._policy = policy

    def set_policy(self, policy: InterceptionPolicy) -> None:
        """热更新拦截策略，失败时保留旧策略。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            "T1Bridge_SetPolicy",
            ctypes.c_int32,
            [ctypes.c_void_p, ctypes.POINTER(NativeBridgePolicy)],
        )
        native_policy = policy.to_native()
        result = int(function(self._handle, ctypes.byref(native_policy)))
        self._raise_if_failed("T1Bridge_SetPolicy", result)
        self._policy = policy

    def start(self) -> None:
        """启动驱动过滤会话。"""

        self._call_handle_function("T1Bridge_Start")

    def stop(self) -> None:
        """停止驱动过滤会话，但保留打开的桥接句柄。"""

        self._call_handle_function("T1Bridge_Stop")

    def status(self) -> BridgeStatus:
        """读取驱动状态，供 UI 显示真实状态。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            "T1Bridge_GetStatus",
            ctypes.c_int32,
            [ctypes.c_void_p, ctypes.POINTER(NativeBridgeStatus)],
        )
        native_status = NativeBridgeStatus()
        native_status.size = ctypes.sizeof(NativeBridgeStatus)
        result = int(function(self._handle, ctypes.byref(native_status)))
        self._raise_if_failed("T1Bridge_GetStatus", result)
        return BridgeStatus.from_native(native_status)

    def capabilities(self) -> BridgeCapabilities:
        """读取驱动能力，供 UI 做兼容性判断。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            "T1Bridge_GetCapabilities",
            ctypes.c_int32,
            [ctypes.c_void_p, ctypes.POINTER(NativeBridgeCapabilities)],
        )
        native = NativeBridgeCapabilities()
        native.size = ctypes.sizeof(NativeBridgeCapabilities)
        result = int(function(self._handle, ctypes.byref(native)))
        self._raise_if_failed("T1Bridge_GetCapabilities", result)
        return BridgeCapabilities(
            flags=int(native.flags),
            max_blocked_usages=int(native.max_blocked_usages),
            max_target_collections=int(native.max_target_collections),
            max_report_bytes=int(native.max_report_bytes),
            event_queue_capacity=int(native.event_queue_capacity),
            abi_version=int(native.abi_version),
        )

    def stats(self) -> BridgeStats:
        """读取驱动运行时统计。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            "T1Bridge_GetStats",
            ctypes.c_int32,
            [ctypes.c_void_p, ctypes.POINTER(NativeBridgeStats)],
        )
        native = NativeBridgeStats()
        native.size = ctypes.sizeof(NativeBridgeStats)
        result = int(function(self._handle, ctypes.byref(native)))
        self._raise_if_failed("T1Bridge_GetStats", result)
        return BridgeStats(
            received_reports=int(native.received_reports),
            blocked_reports=int(native.blocked_reports),
            queued_events=int(native.queued_events),
            dropped_events=int(native.dropped_events),
            buffer_errors=int(native.buffer_errors),
            queue_depth=int(native.queue_depth),
            abi_version=int(native.abi_version),
        )

    def flush_events(self) -> None:
        """清空驱动事件队列，不影响当前策略和拦截状态。"""

        self._call_handle_function("T1Bridge_FlushEvents")

    def read_event(self) -> DriverInputEvent | None:
        """读取一条被驱动拦截前保存的原始事件；队列为空时返回 None。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            "T1Bridge_ReadEvent",
            ctypes.c_int32,
            [ctypes.c_void_p, ctypes.POINTER(NativeBridgeEvent)],
        )
        native_event = NativeBridgeEvent()
        native_event.size = ctypes.sizeof(NativeBridgeEvent)
        result = int(function(self._handle, ctypes.byref(native_event)))
        if result == ERROR_NO_MORE_ITEMS:
            return None
        self._raise_if_failed("T1Bridge_ReadEvent", result)
        if native_event.abi_version != BRIDGE_ABI_VERSION:
            raise BridgeProtocolError(
                f"驱动事件 ABI 不兼容：需要 {BRIDGE_ABI_VERSION}，实际为 {native_event.abi_version}"
            )
        report_length = int(native_event.report_length)
        if report_length > MAX_REPORT_BYTES:
            raise BridgeProtocolError("驱动事件报告长度超出固定缓冲区")
        return DriverInputEvent(
            sequence=int(native_event.sequence),
            usage_page=int(native_event.usage_page),
            usage=int(native_event.usage),
            collection=f"COL{int(native_event.collection):02d}",
            report=bytes(native_event.report[:report_length]),
        )

    def close(self) -> None:
        """关闭桥接会话；关闭失败也清理本地句柄，避免重复使用。"""

        if not self.is_open:
            return
        assert self._library is not None
        try:
            function = _configure_function(
                self._library,
                "T1Bridge_Close",
                None,
                [ctypes.c_void_p],
            )
            function(self._handle)
        finally:
            self._handle = ctypes.c_void_p()
            self._library = None
            self._policy = None

    def __enter__(self) -> "T1BridgeClient":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _load_library(self) -> object:
        """按明确路径加载 DLL，找不到时返回可诊断错误。"""

        loader = self._library_loader
        if loader is None:
            loader = getattr(ctypes, "WinDLL")

        project_root = Path(__file__).resolve().parents[2]
        candidates: list[str] = []
        if self._dll_path:
            candidates.append(str(self._dll_path))
        candidates.extend(
            [
                str(project_root / "native" / "t1bridge.dll"),
                str(project_root / "native" / "t1bridge" / "t1bridge.dll"),
                str(
                    project_root
                    / "native"
                    / "t1bridge"
                    / "x64"
                    / "Release"
                    / "t1bridge.dll"
                ),
                str(project_root / "bin" / "t1bridge.dll"),
                "t1bridge.dll",
            ]
        )

        errors: list[str] = []
        for candidate in candidates:
            try:
                return loader(candidate)
            except (OSError, FileNotFoundError) as exc:
                errors.append(f"{candidate}: {exc}")
        detail = "；".join(errors[-3:])
        raise BridgeUnavailable(f"找不到可用的 t1bridge.dll：{detail}")

    def _call_handle_function(self, name: str) -> None:
        """调用只接收句柄的桥接函数。"""

        self._require_open()
        assert self._library is not None
        function = _configure_function(
            self._library,
            name,
            ctypes.c_int32,
            [ctypes.c_void_p],
        )
        result = int(function(self._handle))
        self._raise_if_failed(name, result)

    def _require_open(self) -> None:
        if not self.is_open or self._library is None:
            raise BridgeError("T1 桥接会话尚未打开")

    @staticmethod
    def _raise_if_failed(operation: str, result: int) -> None:
        if result != 0:
            raise BridgeError(f"{operation} 失败，错误码：{result}")


__all__ = [
    "CAPABILITY_REPORT_REMAP",
    "BridgeCapabilities",
    "BridgeError",
    "BridgeProtocolError",
    "BridgeStatus",
    "BridgeStats",
    "BridgeUnavailable",
    "DriverInputEvent",
    "HidUsage",
    "InterceptionPolicy",
    "NativeBridgeEvent",
    "NativeBridgeCapabilities",
    "NativeBridgeStats",
    "T1BridgeClient",
]
