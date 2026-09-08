"""程序说明：提供 Tkinter 映射编辑器使用的表单转换和摘要函数。

本模块不创建窗口，也不执行 SendInput 或外部命令。它把界面字段转换成
KeyAction，并在保存前复用 KeyAction 的配置校验，方便无 GUI 自动化测试。
"""

from __future__ import annotations

from typing import Any, Iterable

from t1remote.core.key_mapping import KeyAction, MappingConfigError


ACTION_TYPE_LABELS: dict[str, str] = {
    "none": "未映射",
    "key": "单键",
    "combo": "组合键",
    "special": "HID 特殊功能",
    "command": "命令行",
}

FORM_MODIFIERS: tuple[str, ...] = ("ALT", "CTRL", "SHIFT", "WIN")


def build_command_argv(
    program: str,
    argument_lines: Iterable[str],
) -> tuple[str, ...]:
    """把程序路径和“一行一个参数”表单转换为 argv。"""

    normalized_program = program.strip()
    if not normalized_program:
        raise MappingConfigError("命令行动作必须填写程序路径")
    arguments = tuple(
        argument.strip() for argument in argument_lines if argument.strip()
    )
    return (normalized_program, *arguments)


def build_action_from_form(
    kind: str,
    *,
    key: str,
    modifiers: Iterable[str],
    program: str,
    argument_lines: Iterable[str],
) -> KeyAction:
    """把编辑器表单转换为 KeyAction，并执行统一配置校验。"""

    normalized_kind = kind.strip().lower()
    if normalized_kind not in ACTION_TYPE_LABELS:
        raise MappingConfigError(f"不支持的前台动作类型：{kind}")
    if normalized_kind == "none":
        return KeyAction("none")
    if normalized_kind == "command":
        return KeyAction("command", argv=build_command_argv(program, argument_lines))
    if normalized_kind == "special":
        return KeyAction("special", key=key)
    if normalized_kind == "combo":
        return KeyAction("combo", key=key, modifiers=tuple(modifiers))
    return KeyAction("key", key=key)


def action_to_form(action: KeyAction) -> dict[str, Any]:
    """把 KeyAction 展开成编辑器字段，命令参数按行返回。"""

    program = action.argv[0] if action.kind == "command" and action.argv else ""
    argument_lines = action.argv[1:] if action.kind == "command" else ()
    return {
        "kind": action.kind,
        "key": action.key or "",
        "modifiers": tuple(action.modifiers),
        "program": program,
        "argument_lines": tuple(argument_lines),
    }


def format_action_summary(action: KeyAction) -> str:
    """生成按键列表中使用的短动作摘要。"""

    if action.kind == "none":
        return ACTION_TYPE_LABELS["none"]
    if action.kind == "command":
        return f"命令：{' '.join(action.argv)}"
    if action.kind in {"combo", "shortcut"}:
        prefix = "+".join(action.modifiers)
        return f"{prefix}+{action.key}" if prefix else str(action.key)
    if action.kind in {"special", "media"}:
        return f"特殊：{action.key}"
    return f"按键：{action.key}"


__all__ = [
    "ACTION_TYPE_LABELS",
    "FORM_MODIFIERS",
    "action_to_form",
    "build_action_from_form",
    "build_command_argv",
    "format_action_summary",
]
