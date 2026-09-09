"""程序说明：把 ICO 文件绑定到 Windows 进程和 Tk 主窗口。"""

from __future__ import annotations

import ctypes
from pathlib import Path
import os


APP_USER_MODEL_ID = "PatDelphi.T1Remote.Mapping"
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040


def set_process_app_user_model_id(app_id: str) -> None:
    """设置 Windows 任务栏分组标识，避免窗口沿用 Python 默认图标。"""

    if os.name != "nt":
        return
    normalized = app_id.strip()
    if not normalized:
        raise ValueError("Windows 应用标识不能为空")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    setter = shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_long
    result = int(setter(normalized))
    if result != 0:
        raise OSError(result, "设置 Windows 应用标识失败")


def load_icon_handles(icon_path: str | Path) -> tuple[int, ...]:
    """从 ICO 文件加载任务栏所需的大、小图标句柄。"""

    if os.name != "nt":
        return ()
    path = Path(icon_path)
    if not path.is_file():
        raise FileNotFoundError(f"图标文件不存在：{path}")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    loader = user32.LoadImageW
    loader.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    loader.restype = ctypes.c_void_p
    handles: list[int] = []
    try:
        for size in (32, 16):
            handle = int(
                loader(
                    None,
                    str(path),
                    IMAGE_ICON,
                    size,
                    size,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
                or 0
            )
            if not handle:
                error_code = ctypes.get_last_error()
                raise ctypes.WinError(error_code)
            handles.append(handle)
    except Exception:
        destroy_icon_handles(handles)
        raise
    return tuple(handles)


def set_window_icons(hwnd: int, handles: tuple[int, ...]) -> None:
    """把大、小图标句柄写入窗口，确保任务栏直接使用自定义图标。"""

    if os.name != "nt" or not hwnd or len(handles) < 2:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    sender = user32.SendMessageW
    sender.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    sender.restype = ctypes.c_ssize_t
    sender(hwnd, WM_SETICON, ICON_BIG, handles[0])
    sender(hwnd, WM_SETICON, ICON_SMALL, handles[1])


def destroy_icon_handles(handles: tuple[int, ...] | list[int]) -> None:
    """释放由 LoadImageW 创建的图标句柄。"""

    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    destroy = user32.DestroyIcon
    destroy.argtypes = [ctypes.c_void_p]
    destroy.restype = ctypes.c_int
    for handle in handles:
        if handle:
            try:
                destroy(handle)
            except OSError:
                pass


__all__ = [
    "APP_USER_MODEL_ID",
    "destroy_icon_handles",
    "load_icon_handles",
    "set_process_app_user_model_id",
    "set_window_icons",
]
