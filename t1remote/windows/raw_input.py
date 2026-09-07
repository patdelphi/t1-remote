"""程序说明：使用隐藏 Win32 消息窗口接收 Raw Input，并返回 T1 原始报文。"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import threading
from typing import Callable

import win32api
import win32gui


WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_PAGEONLY = 0x00000020
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
RAW_INPUT_ERROR = 0xFFFFFFFF


class RawInputDevice(ctypes.Structure):
    """RegisterRawInputDevices 使用的设备注册结构。"""

    _fields_ = (
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    )


class RawInputHeader(ctypes.Structure):
    """RAWINPUTHEADER 的平台相关布局。"""

    _fields_ = (
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", ctypes.c_size_t),
    )


@dataclass(frozen=True)
class RawInputEvent:
    """去除 Windows 句柄后的原始输入事件。"""

    device_path: str
    raw_input_type: int
    raw_data: bytes


class RawInputListener:
    """在后台线程中接收 Raw Input，不设置 RIDEV_NOLEGACY。"""

    def __init__(
        self,
        on_event: Callable[[RawInputEvent], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_error = on_error
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._window_class_name = f"T1RemoteInspector_{os.getpid()}_{id(self)}"
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error: Exception | None = None
        self._user32: ctypes.WinDLL | None = None

    def start(self) -> None:
        """启动隐藏窗口和 Raw Input 消息循环。"""

        if os.name != "nt":
            raise RuntimeError("T1 Raw Input Inspector 只能在 Windows 上运行")
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Raw Input 监听器已经启动")

        self._ready.clear()
        self._stop_requested.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._message_thread,
            name="t1-raw-input",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise TimeoutError("等待 Raw Input 消息窗口启动超时")
        if self._startup_error:
            raise RuntimeError("Raw Input 监听器启动失败") from self._startup_error

    def stop(self) -> None:
        """请求消息循环退出，并等待后台线程结束。"""

        self._stop_requested.set()
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, WM_CLOSE, 0, 0)
            except Exception as exc:  # Windows API 失败时仍继续等待线程退出
                self._report_error(exc)
        if self._thread:
            self._thread.join(timeout=5)

    def _message_thread(self) -> None:
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._create_window()
            self._register_devices()
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            self._report_error(exc)
        finally:
            self._hwnd = None

    def _create_window(self) -> None:
        """创建不显示 UI 的消息窗口。"""

        window_class = win32gui.WNDCLASS()
        window_class.hInstance = win32api.GetModuleHandle(None)
        window_class.lpszClassName = self._window_class_name
        window_class.lpfnWndProc = self._window_proc
        win32gui.RegisterClass(window_class)
        self._hwnd = win32gui.CreateWindowEx(
            0,
            self._window_class_name,
            "T1 Remote Raw Input",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            window_class.hInstance,
            None,
        )

    def _register_devices(self) -> None:
        """注册键盘、鼠标、Consumer Control 和常见 Vendor Defined 页面。"""

        if not self._user32 or not self._hwnd:
            raise RuntimeError("Raw Input API 尚未初始化")

        flags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
        registrations = [
            RawInputDevice(0x01, 0x06, flags, self._hwnd),  # Keyboard
            RawInputDevice(0x01, 0x02, flags, self._hwnd),  # Mouse
            RawInputDevice(0x0C, 0x01, flags, self._hwnd),  # Consumer Control
            RawInputDevice(
                0xFF00,
                0x00,
                flags | RIDEV_PAGEONLY,
                self._hwnd,
            ),  # Vendor Defined
        ]
        registration_array = (RawInputDevice * len(registrations))(*registrations)
        result = self._user32.RegisterRawInputDevices(
            registration_array,
            len(registrations),
            ctypes.sizeof(RawInputDevice),
        )
        if not result:
            error_code = ctypes.get_last_error()
            raise ctypes.WinError(error_code)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        """处理 Raw Input 消息，回调内只做轻量报文复制。"""

        try:
            if message == WM_INPUT:
                self._handle_input(lparam)
            elif message == WM_CLOSE:
                win32gui.DestroyWindow(hwnd)
            elif message == WM_DESTROY:
                win32gui.PostQuitMessage(0)
        except Exception as exc:
            self._report_error(exc)
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _handle_input(self, raw_input_handle: int) -> None:
        """读取一条 Raw Input，并解析设备路径和有效报文字节。"""

        raw_data = self._get_raw_input_data(raw_input_handle)
        header_size = ctypes.sizeof(RawInputHeader)
        if len(raw_data) < header_size:
            return

        raw_input_type = int.from_bytes(raw_data[0:4], "little")
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        device_start = 8
        device_end = device_start + pointer_size
        device_handle = int.from_bytes(raw_data[device_start:device_end], "little")
        device_path = self._get_device_name(device_handle)
        payload = self._extract_payload(raw_data, raw_input_type, header_size)
        self._on_event(RawInputEvent(device_path, raw_input_type, payload))

    def _get_raw_input_data(self, raw_input_handle: int) -> bytes:
        """通过 GetRawInputData 读取完整的 RAWINPUT 缓冲区。"""

        if not self._user32:
            raise RuntimeError("Raw Input API 尚未初始化")
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RawInputHeader)
        result = self._user32.GetRawInputData(
            ctypes.c_void_p(raw_input_handle),
            RID_INPUT,
            None,
            ctypes.byref(size),
            header_size,
        )
        if result == RAW_INPUT_ERROR:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = (ctypes.c_ubyte * size.value)()
        result = self._user32.GetRawInputData(
            ctypes.c_void_p(raw_input_handle),
            RID_INPUT,
            buffer,
            ctypes.byref(size),
            header_size,
        )
        if result == RAW_INPUT_ERROR:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buffer[:result])

    def _get_device_name(self, device_handle: int) -> str:
        """查询 Raw Input 设备路径；失败时返回空字符串。"""

        if not self._user32 or not device_handle:
            return ""
        size = wintypes.UINT(0)
        result = self._user32.GetRawInputDeviceInfoW(
            ctypes.c_void_p(device_handle),
            RIDI_DEVICENAME,
            None,
            ctypes.byref(size),
        )
        if result == RAW_INPUT_ERROR or size.value == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        result = self._user32.GetRawInputDeviceInfoW(
            ctypes.c_void_p(device_handle),
            RIDI_DEVICENAME,
            buffer,
            ctypes.byref(size),
        )
        if result == RAW_INPUT_ERROR:
            return ""
        return buffer.value

    @staticmethod
    def _extract_payload(raw_data: bytes, raw_input_type: int, header_size: int) -> bytes:
        """提取 HID 报文；键盘和鼠标保留 Raw Input 数据区。"""

        if raw_input_type == 2 and len(raw_data) >= header_size + 8:
            report_size = int.from_bytes(
                raw_data[header_size : header_size + 4], "little"
            )
            report_count = int.from_bytes(
                raw_data[header_size + 4 : header_size + 8], "little"
            )
            payload_start = header_size + 8
            payload_length = report_size * report_count
            if payload_length and len(raw_data) >= payload_start + payload_length:
                return raw_data[payload_start : payload_start + payload_length]
        return raw_data[header_size:]

    def _report_error(self, error: Exception) -> None:
        """将异常交给调用方，避免吞掉 Windows API 错误。"""

        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                # 错误回调不能再次打断 Raw Input 消息循环。
                pass

