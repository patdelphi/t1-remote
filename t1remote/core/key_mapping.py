"""程序说明：定义 T1 Key Mapping 配置、校验、热加载和按键状态机。

核心层只产生语义映射事件，不直接调用 Windows API。输出层负责把动作转换
为 SendInput 事件，这样配置测试和按键状态机可以在没有真实设备的情况下验证。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from t1remote.core.capture_scope import REMOTE_BUTTONS
from t1remote.core.input_mapping import ButtonEvent


MAPPING_VERSION = 1
_ACTION_KINDS = {"none", "key", "media", "special", "shortcut", "combo", "command"}
_MODIFIER_NAMES = {"ALT", "CTRL", "SHIFT", "WIN"}


class MappingConfigError(ValueError):
    """映射配置格式、版本或字段内容不合法。"""


@dataclass(frozen=True)
class KeyAction:
    """一个按键对应的输出动作。"""

    kind: str
    key: str | None = None
    modifiers: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        kind = self.kind.lower()
        if kind not in _ACTION_KINDS:
            raise MappingConfigError(f"不支持的动作类型：{self.kind}")
        object.__setattr__(self, "kind", kind)
        if kind == "none":
            if self.key is not None or self.modifiers or self.argv:
                raise MappingConfigError("none 动作不能包含 key、modifiers 或 argv")
            return
        if kind == "command":
            if self.key is not None or self.modifiers:
                raise MappingConfigError("command 动作只能包含 argv")
            normalized_argv = tuple(self.argv)
            if not normalized_argv or any(
                not isinstance(item, str) or not item.strip()
                for item in normalized_argv
            ):
                raise MappingConfigError("command 动作必须包含非空 argv 字符串数组")
            object.__setattr__(self, "argv", normalized_argv)
            return
        if not isinstance(self.key, str) or not self.key.strip():
            raise MappingConfigError(f"{kind} 动作必须包含非空 key")
        normalized_key = self.key.strip().upper()
        object.__setattr__(self, "key", normalized_key)
        normalized_modifiers = tuple(item.strip().upper() for item in self.modifiers)
        if kind not in {"shortcut", "combo"} and normalized_modifiers:
            raise MappingConfigError("只有 combo/shortcut 动作可以包含 modifiers")
        if any(item not in _MODIFIER_NAMES for item in normalized_modifiers):
            raise MappingConfigError("快捷键修饰键只支持 ALT、CTRL、SHIFT、WIN")
        if len(set(normalized_modifiers)) != len(normalized_modifiers):
            raise MappingConfigError("快捷键修饰键不能重复")
        if kind in {"shortcut", "combo"} and not normalized_modifiers:
            raise MappingConfigError("combo/shortcut 动作至少需要一个 modifier")
        object.__setattr__(self, "modifiers", normalized_modifiers)

    @classmethod
    def from_dict(cls, raw: object) -> "KeyAction":
        """从 JSON 对象读取一个动作。"""

        if not isinstance(raw, dict):
            raise MappingConfigError("每个映射动作必须是对象")
        kind = raw.get("type")
        if not isinstance(kind, str):
            raise MappingConfigError("映射动作缺少 type")
        key = raw.get("key")
        modifiers = raw.get("modifiers", ())
        argv = raw.get("argv", ())
        if not isinstance(modifiers, (list, tuple)) or any(
            not isinstance(item, str) for item in modifiers
        ):
            raise MappingConfigError("modifiers 必须是字符串数组")
        if not isinstance(argv, (list, tuple)) or any(
            not isinstance(item, str) for item in argv
        ):
            raise MappingConfigError("argv 必须是字符串数组")
        return cls(
            kind,
            key if isinstance(key, str) else key,
            tuple(modifiers),
            tuple(argv),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 对象。"""

        if self.kind == "none":
            return {"type": "none"}
        if self.kind == "command":
            return {"type": "command", "argv": list(self.argv)}
        data: dict[str, Any] = {"type": self.kind, "key": self.key}
        if self.modifiers:
            data["modifiers"] = list(self.modifiers)
        return data


@dataclass(frozen=True)
class MappingConfig:
    """版本化的物理按键到输出动作配置。"""

    mappings: dict[str, KeyAction]
    version: int = MAPPING_VERSION

    def __post_init__(self) -> None:
        if self.version != MAPPING_VERSION:
            raise MappingConfigError(
                f"不支持的映射配置版本：{self.version}，当前版本为 {MAPPING_VERSION}"
            )
        normalized: dict[str, KeyAction] = {}
        for button, action in self.mappings.items():
            if button not in REMOTE_BUTTONS:
                raise MappingConfigError(f"未知遥控按键：{button}")
            if not isinstance(action, KeyAction):
                raise MappingConfigError(f"按键 {button} 的动作对象无效")
            normalized[button] = action
        object.__setattr__(self, "mappings", normalized)

    @classmethod
    def default(cls) -> "MappingConfig":
        """返回安全的首版默认映射。"""

        return cls(
            mappings={
                "Power": KeyAction("none"),
                "Arrow Up": KeyAction("key", "UP"),
                "Arrow Down": KeyAction("key", "DOWN"),
                "Arrow Left": KeyAction("key", "LEFT"),
                "Arrow Right": KeyAction("key", "RIGHT"),
                "OK": KeyAction("key", "ENTER"),
                "Return": KeyAction("key", "ESC"),
                "Voice": KeyAction("none"),
                "Mute": KeyAction("media", "VOLUME_MUTE"),
                "Home": KeyAction("key", "HOME"),
                "Menu": KeyAction("key", "APPS"),
                "Air Mouse": KeyAction("none"),
                "Volume Plus": KeyAction("media", "VOLUME_UP"),
                "Volume Minus": KeyAction("media", "VOLUME_DOWN"),
            }
        )

    @classmethod
    def from_dict(cls, raw: object) -> "MappingConfig":
        """从 JSON 对象读取配置，缺失按键沿用默认动作。"""

        if not isinstance(raw, dict):
            raise MappingConfigError("映射配置根节点必须是对象")
        version = raw.get("version", MAPPING_VERSION)
        if not isinstance(version, int):
            raise MappingConfigError("映射配置 version 必须是整数")
        mappings = raw.get("mappings")
        if not isinstance(mappings, dict):
            raise MappingConfigError("映射配置缺少 mappings 对象")
        result = dict(cls.default().mappings)
        for button, action in mappings.items():
            if not isinstance(button, str):
                raise MappingConfigError("映射按键名必须是字符串")
            result[button] = KeyAction.from_dict(action)
        return cls(mappings=result, version=version)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 对象。"""

        return {
            "version": self.version,
            "mappings": {
                button: self.mappings[button].to_dict()
                for button in REMOTE_BUTTONS
                if button in self.mappings
            },
        }


@dataclass(frozen=True)
class MappingEvent:
    """映射状态机产生的一次输出动作事件。"""

    button: str
    state: str
    action: KeyAction


class MappingEngine:
    """处理按键按下/抬起并抑制重复事件和孤立释放。"""

    def __init__(self, config: MappingConfig | None = None) -> None:
        self._config = config or MappingConfig.default()
        self._active_buttons: set[str] = set()

    @property
    def config(self) -> MappingConfig:
        """返回当前生效配置。"""

        return self._config

    def handle(self, event: ButtonEvent) -> tuple[MappingEvent, ...]:
        """处理语义按键事件，未知或未绑定动作直接忽略。"""

        if event.button is None or event.state not in ("down", "up"):
            return ()
        action = self._config.mappings.get(event.button)
        if action is None or action.kind == "none":
            return ()
        if event.state == "down":
            if event.button in self._active_buttons:
                return ()
            self._active_buttons.add(event.button)
        elif event.button not in self._active_buttons:
            return ()
        else:
            self._active_buttons.remove(event.button)
        return (MappingEvent(event.button, event.state, action),)

    def reload(self, config: MappingConfig) -> tuple[MappingEvent, ...]:
        """切换配置并返回旧动作的抬起事件，避免留下粘键。"""

        releases = tuple(
            MappingEvent(button, "up", self._config.mappings[button])
            for button in sorted(self._active_buttons)
            if self._config.mappings[button].kind != "none"
        )
        self._active_buttons.clear()
        self._config = config
        return releases

    def reset(self) -> tuple[MappingEvent, ...]:
        """设备断开或应用退出时释放当前活动动作。"""

        releases = tuple(
            MappingEvent(button, "up", self._config.mappings[button])
            for button in sorted(self._active_buttons)
            if self._config.mappings[button].kind != "none"
        )
        self._active_buttons.clear()
        return releases


def load_mapping_config(path: str | Path) -> MappingConfig:
    """读取 UTF-8/UTF-8 BOM JSON 配置并转换为 MappingConfig。"""

    config_path = Path(path)
    try:
        document = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MappingConfigError(f"读取映射配置失败：{config_path}") from exc
    return MappingConfig.from_dict(document)


def save_mapping_config(path: str | Path, config: MappingConfig) -> None:
    """以 UTF-8 BOM、CRLF 和临时文件替换方式保存配置。"""

    config_path = Path(path)
    temporary_path = config_path.with_name(f"{config_path.name}.tmp")
    content = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8-sig", newline="\r\n") as file:
            file.write(content)
            file.write("\r\n")
        temporary_path.replace(config_path)
    except OSError as exc:
        raise MappingConfigError(f"保存映射配置失败：{config_path}") from exc


__all__ = [
    "KeyAction",
    "MappingConfig",
    "MappingConfigError",
    "MappingEngine",
    "MappingEvent",
    "load_mapping_config",
    "save_mapping_config",
]
