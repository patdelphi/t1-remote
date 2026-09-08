"""程序说明：定义 T1 Key Mapping 配置、校验、热加载和按键状态机。

核心层只产生语义映射事件，不直接调用 Windows API。输出层负责把动作转换
为 SendInput 事件，这样配置测试和按键状态机可以在没有真实设备的情况下验证。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

from t1remote.core.capture_scope import MAPPABLE_REMOTE_BUTTONS
from t1remote.core.input_mapping import ButtonEvent


MAPPING_VERSION = 1
_ACTION_KINDS = {
    "none",
    "key",
    "media",
    "special",
    "shortcut",
    "combo",
    "command",
    "macro",
    "text",
}
_MODIFIER_NAMES = {"ALT", "CTRL", "SHIFT", "WIN"}
_TRIGGER_KINDS = {"press", "long_press", "double_click", "hold_repeat"}
_MACRO_STEP_KINDS = {"key", "special"}


class MappingConfigError(ValueError):
    """映射配置格式、版本或字段内容不合法。"""


@dataclass(frozen=True)
class TriggerConfig:
    """一个映射动作的触发方式和时间参数。"""

    kind: str = "press"
    threshold_ms: int = 500
    window_ms: int = 300
    interval_ms: int = 100

    def __post_init__(self) -> None:
        normalized_kind = self.kind.strip().lower()
        if normalized_kind not in _TRIGGER_KINDS:
            raise MappingConfigError(f"不支持的触发方式：{self.kind}")
        object.__setattr__(self, "kind", normalized_kind)
        for name, value, minimum, maximum in (
            ("threshold_ms", self.threshold_ms, 100, 5000),
            ("window_ms", self.window_ms, 100, 2000),
            ("interval_ms", self.interval_ms, 50, 2000),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MappingConfigError(f"{name} 必须是整数")
            if not minimum <= value <= maximum:
                raise MappingConfigError(
                    f"{name} 必须在 {minimum}-{maximum} 之间"
                )

    @classmethod
    def from_dict(cls, raw: object) -> "TriggerConfig":
        """从 JSON 对象读取触发参数；缺失时使用单击默认值。"""

        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise MappingConfigError("trigger 必须是对象")
        kind = raw.get("kind", "press")
        if not isinstance(kind, str):
            raise MappingConfigError("trigger.kind 必须是字符串")
        values = {
            name: raw.get(name, default)
            for name, default in (
                ("threshold_ms", 500),
                ("window_ms", 300),
                ("interval_ms", 100),
            )
        }
        return cls(kind=kind, **values)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 对象。"""

        data: dict[str, Any] = {"kind": self.kind}
        if self.kind == "long_press":
            data["threshold_ms"] = self.threshold_ms
        elif self.kind == "double_click":
            data["window_ms"] = self.window_ms
        elif self.kind == "hold_repeat":
            data["interval_ms"] = self.interval_ms
        return data


@dataclass(frozen=True)
class MacroStep:
    """宏中的一个原子键击；按下和抬起后等待 delay_ms 再执行下一步。"""

    kind: str = "key"
    key: str = ""
    modifiers: tuple[str, ...] = ()
    delay_ms: int = 0

    def __post_init__(self) -> None:
        normalized_kind = self.kind.strip().lower()
        if normalized_kind not in _MACRO_STEP_KINDS:
            raise MappingConfigError(f"不支持的宏步骤类型：{self.kind}")
        if not isinstance(self.key, str) or not self.key.strip():
            raise MappingConfigError("宏步骤必须包含非空 key")
        if isinstance(self.delay_ms, bool) or not isinstance(self.delay_ms, int):
            raise MappingConfigError("宏步骤间隔必须是整数")
        if not 0 <= self.delay_ms <= 60000:
            raise MappingConfigError("宏步骤间隔必须在 0-60000 毫秒之间")
        normalized_modifiers = tuple(item.strip().upper() for item in self.modifiers)
        if any(item not in _MODIFIER_NAMES for item in normalized_modifiers):
            raise MappingConfigError("宏步骤修饰键只支持 ALT、CTRL、SHIFT、WIN")
        if len(set(normalized_modifiers)) != len(normalized_modifiers):
            raise MappingConfigError("宏步骤修饰键不能重复")
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "key", self.key.strip().upper())
        object.__setattr__(self, "modifiers", normalized_modifiers)

    @classmethod
    def from_dict(cls, raw: object) -> "MacroStep":
        """从 JSON 对象读取一个宏步骤。"""

        if not isinstance(raw, dict):
            raise MappingConfigError("宏 steps 中的每一项必须是对象")
        kind = raw.get("type", "key")
        key = raw.get("key")
        modifiers = raw.get("modifiers", ())
        delay_ms = raw.get("delay_ms", 0)
        if not isinstance(kind, str) or not isinstance(key, str):
            raise MappingConfigError("宏步骤 type 和 key 必须是字符串")
        if not isinstance(modifiers, (list, tuple)) or any(
            not isinstance(item, str) for item in modifiers
        ):
            raise MappingConfigError("宏步骤 modifiers 必须是字符串数组")
        return cls(kind, key, tuple(modifiers), delay_ms)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 对象。"""

        data: dict[str, Any] = {"type": self.kind, "key": self.key}
        if self.modifiers:
            data["modifiers"] = list(self.modifiers)
        if self.delay_ms:
            data["delay_ms"] = self.delay_ms
        return data


@dataclass(frozen=True)
class KeyAction:
    """一个按键对应的输出动作。"""

    kind: str
    key: str | None = None
    modifiers: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    trigger: TriggerConfig = TriggerConfig()
    macro: tuple[MacroStep, ...] = ()
    text: str = ""
    append_enter: bool = False

    def __post_init__(self) -> None:
        kind = self.kind.lower()
        if kind not in _ACTION_KINDS:
            raise MappingConfigError(f"不支持的动作类型：{self.kind}")
        if not isinstance(self.trigger, TriggerConfig):
            raise MappingConfigError("trigger 必须是 TriggerConfig")
        if not isinstance(self.append_enter, bool):
            raise MappingConfigError("append_enter 必须是布尔值")
        object.__setattr__(self, "kind", kind)
        if kind == "none":
            if (
                self.key is not None
                or self.modifiers
                or self.argv
                or self.macro
                or self.text
                or self.append_enter
            ):
                raise MappingConfigError(
                    "none 动作不能包含 key、modifiers、argv、macro 或 text"
                )
            return
        if kind == "command":
            if (
                self.key is not None
                or self.modifiers
                or self.macro
                or self.text
                or self.append_enter
            ):
                raise MappingConfigError("command 动作只能包含 argv")
            normalized_argv = tuple(self.argv)
            if not normalized_argv or any(
                not isinstance(item, str) or not item.strip()
                for item in normalized_argv
            ):
                raise MappingConfigError("command 动作必须包含非空 argv 字符串数组")
            object.__setattr__(self, "argv", normalized_argv)
            return
        if kind == "macro":
            if (
                self.key is not None
                or self.modifiers
                or self.argv
                or self.text
                or self.append_enter
            ):
                raise MappingConfigError("macro 动作只能包含 steps")
            if not self.macro or any(not isinstance(step, MacroStep) for step in self.macro):
                raise MappingConfigError("macro 动作至少需要一个有效步骤")
            if self.trigger.kind == "hold_repeat":
                raise MappingConfigError("macro 暂不支持按住重复触发")
            return
        if kind == "text":
            if self.key is not None or self.modifiers or self.argv or self.macro:
                raise MappingConfigError("text 动作只能包含 text 和 append_enter")
            if not isinstance(self.text, str) or not self.text:
                raise MappingConfigError("输入文字动作必须包含非空 text")
            if len(self.text) > 100:
                raise MappingConfigError("输入文字最多支持 100 个字符")
            if self.trigger.kind == "hold_repeat":
                raise MappingConfigError("输入文字暂不支持按住重复触发")
            return
        if not isinstance(self.key, str) or not self.key.strip():
            raise MappingConfigError(f"{kind} 动作必须包含非空 key")
        if self.text or self.append_enter:
            raise MappingConfigError(f"{kind} 动作不能包含 text 或 append_enter")
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
        macro = raw.get("steps", ())
        text = raw.get("text", "")
        append_enter = raw.get("append_enter", False)
        trigger = TriggerConfig.from_dict(raw.get("trigger"))
        if not isinstance(modifiers, (list, tuple)) or any(
            not isinstance(item, str) for item in modifiers
        ):
            raise MappingConfigError("modifiers 必须是字符串数组")
        if not isinstance(argv, (list, tuple)) or any(
            not isinstance(item, str) for item in argv
        ):
            raise MappingConfigError("argv 必须是字符串数组")
        if not isinstance(macro, (list, tuple)):
            raise MappingConfigError("steps 必须是数组")
        if not isinstance(text, str):
            raise MappingConfigError("text 必须是字符串")
        if not isinstance(append_enter, bool):
            raise MappingConfigError("append_enter 必须是布尔值")
        return cls(
            kind,
            key if isinstance(key, str) else key,
            tuple(modifiers),
            tuple(argv),
            trigger,
            tuple(MacroStep.from_dict(item) for item in macro),
            text,
            append_enter,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 对象。"""

        if self.kind == "none":
            return {"type": "none"}
        if self.kind == "command":
            data: dict[str, Any] = {"type": "command", "argv": list(self.argv)}
            if self.trigger.kind != "press":
                data["trigger"] = self.trigger.to_dict()
            return data
        if self.kind == "macro":
            data = {
                "type": "macro",
                "steps": [step.to_dict() for step in self.macro],
            }
            if self.trigger.kind != "press":
                data["trigger"] = self.trigger.to_dict()
            return data
        if self.kind == "text":
            data = {"type": "text", "text": self.text}
            if self.append_enter:
                data["append_enter"] = True
            if self.trigger.kind != "press":
                data["trigger"] = self.trigger.to_dict()
            return data
        data: dict[str, Any] = {"type": self.kind, "key": self.key}
        if self.modifiers:
            data["modifiers"] = list(self.modifiers)
        if self.trigger.kind != "press":
            data["trigger"] = self.trigger.to_dict()
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
            if button not in MAPPABLE_REMOTE_BUTTONS:
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
            if button == "Air Mouse":
                continue
            result[button] = KeyAction.from_dict(action)
        return cls(mappings=result, version=version)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 对象。"""

        return {
            "version": self.version,
            "mappings": {
                button: self.mappings[button].to_dict()
                for button in MAPPABLE_REMOTE_BUTTONS
                if button in self.mappings
            },
        }


@dataclass(frozen=True)
class MappingEvent:
    """映射状态机产生的一次输出动作事件。"""

    button: str
    state: str
    action: KeyAction


@dataclass
class _ActiveMapping:
    """状态机内部保存的一次活动按键。"""

    action: KeyAction
    emitted: bool
    deadline: float | None = None


@dataclass
class _PendingDoubleClick:
    """等待第二次按下的双击候选。"""

    action: KeyAction
    expires_at: float


class MappingEngine:
    """处理按键状态、长按、双击和按住重复。"""

    def __init__(self, config: MappingConfig | None = None) -> None:
        self._config = config or MappingConfig.default()
        self._active_buttons: dict[str, _ActiveMapping] = {}
        self._pending_double_clicks: dict[str, _PendingDoubleClick] = {}

    @property
    def config(self) -> MappingConfig:
        """返回当前生效配置。"""

        return self._config

    def handle(
        self,
        event: ButtonEvent,
        *,
        now: float | None = None,
    ) -> tuple[MappingEvent, ...]:
        """处理语义按键事件，未知或未绑定动作直接忽略。"""

        if event.button is None or event.state not in ("down", "up"):
            return ()
        current_time = time.monotonic() if now is None else now
        pending_events = list(self._expire(current_time))
        action = self._config.mappings.get(event.button)
        if action is None or action.kind == "none":
            return tuple(pending_events)
        if event.state == "down":
            pending_events.extend(self._handle_down(event.button, action, current_time))
        else:
            pending_events.extend(self._handle_up(event.button, action, current_time))
        return tuple(pending_events)

    def tick(self, *, now: float | None = None) -> tuple[MappingEvent, ...]:
        """推进时间触发长按和按住重复事件。"""

        current_time = time.monotonic() if now is None else now
        return self._expire(current_time)

    def _handle_down(
        self,
        button: str,
        action: KeyAction,
        now: float,
    ) -> tuple[MappingEvent, ...]:
        if button in self._active_buttons:
            return ()
        trigger = action.trigger
        pending = self._pending_double_clicks.get(button)
        if trigger.kind != "double_click":
            self._pending_double_clicks.pop(button, None)
            pending = None
        if trigger.kind == "double_click" and pending is not None:
            if now <= pending.expires_at:
                self._pending_double_clicks.pop(button, None)
                self._active_buttons[button] = _ActiveMapping(
                    action=pending.action,
                    emitted=True,
                )
                return (MappingEvent(button, "down", pending.action),)
            self._pending_double_clicks.pop(button, None)

        if trigger.kind == "double_click":
            self._active_buttons[button] = _ActiveMapping(
                action=action,
                emitted=False,
            )
            return ()

        if trigger.kind == "long_press":
            self._active_buttons[button] = _ActiveMapping(
                action=action,
                emitted=False,
                deadline=now + trigger.threshold_ms / 1000,
            )
            return ()
        self._active_buttons[button] = _ActiveMapping(
            action=action,
            emitted=True,
            deadline=(
                now + trigger.interval_ms / 1000
                if trigger.kind == "hold_repeat"
                else None
            ),
        )
        return (MappingEvent(button, "down", action),)

    def _handle_up(
        self,
        button: str,
        action: KeyAction,
        now: float,
    ) -> tuple[MappingEvent, ...]:
        active = self._active_buttons.pop(button, None)
        if active is None:
            return ()
        if active.action.trigger.kind == "double_click":
            if not active.emitted:
                self._pending_double_clicks[button] = _PendingDoubleClick(
                    action=active.action,
                    expires_at=now + active.action.trigger.window_ms / 1000,
                )
                return ()
            return (MappingEvent(button, "up", active.action),)
        if not active.emitted:
            # 长按未达到阈值，短按不产生动作。
            return ()
        return (MappingEvent(button, "up", active.action),)

    def _expire(self, now: float) -> tuple[MappingEvent, ...]:
        """处理已到期的长按、重复和双击窗口。"""

        events: list[MappingEvent] = []
        for button, pending in tuple(self._pending_double_clicks.items()):
            if now > pending.expires_at:
                self._pending_double_clicks.pop(button, None)
        for button, active in tuple(self._active_buttons.items()):
            trigger = active.action.trigger
            if active.deadline is None or now < active.deadline:
                continue
            if trigger.kind == "long_press" and not active.emitted:
                active.emitted = True
                active.deadline = None
                events.append(MappingEvent(button, "down", active.action))
            elif trigger.kind == "hold_repeat":
                # 一次 poll 最多生成一个重复 down，避免应用恢复后瞬间积压大量输入。
                active.deadline = now + trigger.interval_ms / 1000
                events.append(MappingEvent(button, "down", active.action))
        return tuple(events)

    def reload(self, config: MappingConfig) -> tuple[MappingEvent, ...]:
        """切换配置并返回旧动作的抬起事件，避免留下粘键。"""

        releases = tuple(
            MappingEvent(button, "up", active.action)
            for button, active in sorted(self._active_buttons.items())
            if active.emitted
        )
        self._active_buttons.clear()
        self._pending_double_clicks.clear()
        self._config = config
        return releases

    def reset(self) -> tuple[MappingEvent, ...]:
        """设备断开或应用退出时释放当前活动动作。"""

        releases = tuple(
            MappingEvent(button, "up", active.action)
            for button, active in sorted(self._active_buttons.items())
            if active.emitted
        )
        self._active_buttons.clear()
        self._pending_double_clicks.clear()
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
    "MacroStep",
    "MappingConfig",
    "MappingConfigError",
    "MappingEngine",
    "MappingEvent",
    "TriggerConfig",
    "load_mapping_config",
    "save_mapping_config",
]
