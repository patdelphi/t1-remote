"""程序说明：把已确认的语义按键转换为 Windows SendInput 键盘事件。

本模块默认只绑定安全的方向、确认、返回、菜单和媒体键。Power、Voice
以及未知按键需要用户显式配置，避免误触发系统电源或外部语音动作。
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from typing import Iterable, Mapping

from t1remote.core.input_mapping import ButtonEvent
from t1remote.core.key_mapping import KeyAction, MacroStep, MappingEvent


KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


@dataclass(frozen=True)
class OutputBinding:
    """一个物理按键对应的 Windows 虚拟键码。"""

    virtual_key: int
    extended: bool = False
    modifiers: tuple[int, ...] = ()


@dataclass(frozen=True)
class KeyboardOutput:
    """待提交给 SendInput 的单个键盘事件。"""

    virtual_key: int
    flags: int


DEFAULT_OUTPUT_BINDINGS: Mapping[str, OutputBinding] = {
    "Arrow Up": OutputBinding(0x26, extended=True),
    "Arrow Down": OutputBinding(0x28, extended=True),
    "Arrow Left": OutputBinding(0x25, extended=True),
    "Arrow Right": OutputBinding(0x27, extended=True),
    "OK": OutputBinding(0x0D),
    "Return": OutputBinding(0x1B),
    "Home": OutputBinding(0x24, extended=True),
    "Menu": OutputBinding(0x5D, extended=True),
    "Mute": OutputBinding(0xAD),
    "Volume Plus": OutputBinding(0xAF),
    "Volume Minus": OutputBinding(0xAE),
}

_KEY_VIRTUAL_KEYS: dict[str, int] = {
    "TAB": 0x09,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "APPS": 0x5D,
}
_KEY_VIRTUAL_KEYS.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
_KEY_VIRTUAL_KEYS.update({str(code - 0x30): code for code in range(0x30, 0x3A)})
_KEY_VIRTUAL_KEYS.update({f"F{index}": 0x6F + index for index in range(1, 13)})
_MEDIA_VIRTUAL_KEYS = {
    "VOLUME_MUTE": 0xAD,
    "VOLUME_DOWN": 0xAE,
    "VOLUME_UP": 0xAF,
}
_SPECIAL_VIRTUAL_KEYS = {
    **_MEDIA_VIRTUAL_KEYS,
    "MEDIA_NEXT_TRACK": 0xB0,
    "MEDIA_PREV_TRACK": 0xB1,
    "MEDIA_STOP": 0xB2,
    "MEDIA_PLAY_PAUSE": 0xB3,
    "LAUNCH_MAIL": 0xB4,
    "LAUNCH_MEDIA": 0xB5,
    "LAUNCH_APP1": 0xB6,
    "LAUNCH_APP2": 0xB7,
    "BROWSER_BACK": 0xA6,
    "BROWSER_FORWARD": 0xA7,
    "BROWSER_REFRESH": 0xA8,
    "BROWSER_STOP": 0xA9,
    "BROWSER_SEARCH": 0xAA,
    "BROWSER_FAVORITES": 0xAB,
    "BROWSER_HOME": 0xAC,
    "POWER": 0x5E,
    "SLEEP": 0x5F,
    "WAKE": 0x63,
    "CALCULATOR": 0xB7,
}
KEY_VIRTUAL_KEY_NAMES: tuple[str, ...] = tuple(_KEY_VIRTUAL_KEYS)
SPECIAL_HID_KEY_NAMES: tuple[str, ...] = tuple(_SPECIAL_VIRTUAL_KEYS)
_MODIFIER_VIRTUAL_KEYS = {"ALT": 0x12, "CTRL": 0x11, "SHIFT": 0x10, "WIN": 0x5B}
_EXTENDED_KEYS = {"PAGEUP", "PAGEDOWN", "END", "HOME", "LEFT", "UP", "RIGHT", "DOWN", "INSERT", "DELETE", "APPS"}


def build_output_events(
    event: ButtonEvent,
    bindings: Mapping[str, OutputBinding] = DEFAULT_OUTPUT_BINDINGS,
) -> tuple[KeyboardOutput, ...]:
    """将语义按键转换为按下/抬起事件；未绑定项返回空元组。"""

    if event.button is None or event.state not in ("down", "up"):
        return ()
    binding = bindings.get(event.button)
    if binding is None:
        return ()
    return _build_binding_events(event.state, binding)


def build_mapping_output_events(event: MappingEvent) -> tuple[KeyboardOutput, ...]:
    """把可配置映射事件转换为键盘、媒体键或快捷键输出。"""

    binding = binding_from_action(event.action)
    if binding is None:
        return ()
    return _build_binding_events(event.state, binding)


def binding_from_action(action: KeyAction) -> OutputBinding | None:
    """把配置动作解析为虚拟键码；未知键名直接报配置错误。"""

    if action.kind == "none":
        return None
    if action.kind in {"media", "special"}:
        virtual_key = _SPECIAL_VIRTUAL_KEYS.get(action.key or "")
        if virtual_key is None:
            raise ValueError(f"不支持的特殊功能键：{action.key}")
        return OutputBinding(virtual_key)
    if action.kind == "command":
        return None
    virtual_key = _KEY_VIRTUAL_KEYS.get(action.key or "")
    if virtual_key is None:
        raise ValueError(f"不支持的虚拟键：{action.key}")
    modifiers = tuple(_MODIFIER_VIRTUAL_KEYS[item] for item in action.modifiers)
    return OutputBinding(
        virtual_key,
        extended=(action.key or "") in _EXTENDED_KEYS,
        modifiers=modifiers,
    )


def binding_from_macro_step(step: MacroStep) -> OutputBinding:
    """把宏步骤转换为普通或特殊虚拟键绑定。"""

    if step.kind == "special":
        virtual_key = _SPECIAL_VIRTUAL_KEYS.get(step.key)
        if virtual_key is None:
            raise ValueError(f"不支持的宏特殊功能键：{step.key}")
    else:
        virtual_key = _KEY_VIRTUAL_KEYS.get(step.key)
        if virtual_key is None:
            raise ValueError(f"不支持的宏虚拟键：{step.key}")
    modifiers = tuple(_MODIFIER_VIRTUAL_KEYS[item] for item in step.modifiers)
    return OutputBinding(
        virtual_key,
        extended=step.key in _EXTENDED_KEYS,
        modifiers=modifiers,
    )


def build_macro_step_events(
    step: MacroStep,
) -> tuple[tuple[KeyboardOutput, ...], tuple[KeyboardOutput, ...]]:
    """生成一个宏步骤的按下和抬起事件，间隔由宏执行器处理。"""

    binding = binding_from_macro_step(step)
    return (
        _build_binding_events("down", binding),
        _build_binding_events("up", binding),
    )


def _build_binding_events(
    state: str, binding: OutputBinding
) -> tuple[KeyboardOutput, ...]:
    """按快捷键的正确顺序生成按下和抬起事件。"""

    key_flags = KEYEVENTF_EXTENDEDKEY if binding.extended else 0
    if state == "down":
        modifier_events = tuple(KeyboardOutput(item, 0) for item in binding.modifiers)
        return modifier_events + (KeyboardOutput(binding.virtual_key, key_flags),)
    if state == "up":
        key_up = KeyboardOutput(binding.virtual_key, key_flags | KEYEVENTF_KEYUP)
        modifier_events = tuple(
            KeyboardOutput(item, KEYEVENTF_KEYUP)
            for item in reversed(binding.modifiers)
        )
        return (key_up,) + modifier_events
    return ()


class KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT 结构。"""

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class INPUT(ctypes.Structure):
    """只声明 SendInput 所需的键盘输入联合体布局。"""

    _fields_ = (("type", wintypes.DWORD), ("ki", KEYBDINPUT))


class WindowsInputEmitter:
    """提交键盘事件；调用方负责保证按下和抬起成对出现。"""

    def __init__(self) -> None:
        self._user32: ctypes.WinDLL | None = None

    def emit(self, outputs: Iterable[KeyboardOutput]) -> None:
        """批量调用 SendInput，失败时抛出 Windows API 异常。"""

        if os.name != "nt":
            raise RuntimeError("SendInput 只能在 Windows 上运行")
        items = tuple(outputs)
        if not items:
            return
        if self._user32 is None:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.SendInput.restype = wintypes.UINT
            self._user32.SendInput.argtypes = [
                wintypes.UINT,
                ctypes.POINTER(INPUT),
                ctypes.c_int,
            ]
        inputs = (INPUT * len(items))()
        for index, output in enumerate(items):
            inputs[index].type = 1  # INPUT_KEYBOARD
            inputs[index].ki.wVk = output.virtual_key
            inputs[index].ki.wScan = 0
            inputs[index].ki.dwFlags = output.flags
            inputs[index].ki.time = 0
            inputs[index].ki.dwExtraInfo = 0
        sent = int(self._user32.SendInput(len(items), inputs, ctypes.sizeof(INPUT)))
        if sent != len(items):
            raise ctypes.WinError(ctypes.get_last_error())


__all__ = [
    "DEFAULT_OUTPUT_BINDINGS",
    "INPUT",
    "KEYBDINPUT",
    "KeyboardOutput",
    "KEY_VIRTUAL_KEY_NAMES",
    "OutputBinding",
    "SPECIAL_HID_KEY_NAMES",
    "WindowsInputEmitter",
    "binding_from_action",
    "binding_from_macro_step",
    "build_macro_step_events",
    "build_mapping_output_events",
    "build_output_events",
]
