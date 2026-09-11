"""程序说明：提供 T1 Remote 按键映射配置前台。

界面只编辑版本化 JSON 配置，不直接启动映射运行时。保存前会执行动作校验，
预览命令行时只展示 argv，不执行外部程序。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from t1remote.core.capture_scope import MAPPABLE_REMOTE_BUTTONS
from t1remote.core.input_mapping import button_input_kind
from t1remote.core.key_mapping import (
    KeyAction,
    MacroStep,
    MappingConfig,
    MappingConfigError,
    load_mapping_config,
    save_mapping_config,
)
from t1remote.core.mapping_editor import (
    ACTION_TYPE_LABELS,
    FORM_MODIFIERS,
    TRIGGER_TYPE_LABELS,
    action_to_form,
    build_action_from_form,
    format_action_summary,
)
from t1remote.windows.send_input import (
    KEY_VIRTUAL_KEY_NAMES,
    MOUSE_ACTION_LABELS,
    MOUSE_ACTION_NAMES,
    SPECIAL_HID_KEY_NAMES,
    binding_from_action,
)
from t1remote.windows.single_instance import SingleInstanceGuard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "t1-key-mapping.json"
BUTTON_DISPLAY_NAMES = {"Volume Plus": "Volume +", "Volume Minus": "Volume -"}
_UI_KIND_BY_ACTION_KIND = {"media": "special", "shortcut": "combo"}
_ALL_ACTION_KINDS = frozenset(ACTION_TYPE_LABELS)
_SUPPORTED_ACTION_KINDS = {
    "keyboard": _ALL_ACTION_KINDS,
    "hid": _ALL_ACTION_KINDS,
    "mouse": frozenset({"none"}),
    "unknown": frozenset({"none"}),
}
_INPUT_KIND_LABELS = {
    "keyboard": "普通 key（COL01）；当前按键支持全部 mapping",
    "hid": "HID（COL02/COL03）；当前按键支持全部 mapping",
    "mouse": "鼠标/HID（COL04）；当前仅支持未映射",
    "unknown": "未知输入类型；当前仅支持未映射",
}
UI_BACKGROUND = "#F4F7FB"
UI_SURFACE = "#FFFFFF"
UI_TEXT = "#172033"
UI_MUTED = "#667085"
UI_BORDER = "#D8E0EB"
UI_ACCENT = "#2563EB"
UI_ACCENT_HOVER = "#1D4ED8"
UI_DANGER = "#B42318"


class MappingEditorWindow:
    """管理映射编辑器窗口状态和交互。"""

    def __init__(
        self,
        root: tk.Tk,
        config_path: Path,
        config: MappingConfig,
        *,
        container: tk.Misc | None = None,
    ) -> None:
        self.root = root
        self.container = container or root
        self.embedded = container is not None
        self.config_path = config_path
        self.profile_dir = config_path.parent / "profiles"
        self._working_actions: dict[str, KeyAction] = dict(config.mappings)
        self._selected_button: str | None = None
        self._macro_steps: list[MacroStep] = []
        self.kind_radios: dict[str, ttk.Radiobutton] = {}
        self.profile_var = tk.StringVar(value="当前配置文件")
        self.input_kind_var = tk.StringVar(value="当前输入类型：")

        if not self.embedded:
            self.root.title("T1 Remote 按键映射")
            self.root.geometry("1280x820")
            self.root.minsize(1080, 720)
        self._configure_styles()
        if not self.embedded:
            self.container.configure(background=UI_BACKGROUND)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(3, weight=1)

        self.status_var = tk.StringVar(value=f"配置文件：{self.config_path}")
        self.kind_var = tk.StringVar()
        self.trigger_kind_var = tk.StringVar()
        self.threshold_var = tk.StringVar(value="500")
        self.window_var = tk.StringVar(value="300")
        self.interval_var = tk.StringVar(value="100")
        self.key_var = tk.StringVar()
        self.program_var = tk.StringVar()
        self.modifier_vars = {
            modifier: tk.BooleanVar(value=False) for modifier in FORM_MODIFIERS
        }

        self._build_layout()
        self._refresh_profile_list()
        self._refresh_button_table()
        if MAPPABLE_REMOTE_BUTTONS:
            self._select_button(MAPPABLE_REMOTE_BUTTONS[0])

    def _configure_styles(self) -> None:
        """配置统一的浅色主题和交互状态。"""

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=UI_SURFACE)
        style.configure("Root.TFrame", background=UI_BACKGROUND)
        style.configure(
            "TLabel",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Root.TLabel",
            background=UI_BACKGROUND,
            foreground=UI_TEXT,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Title.TLabel",
            background=UI_BACKGROUND,
            foreground=UI_TEXT,
            font=("Segoe UI", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=UI_BACKGROUND,
            foreground=UI_MUTED,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Card.TLabelframe",
            background=UI_SURFACE,
            bordercolor=UI_BORDER,
            relief="solid",
            borderwidth=1,
            padding=10,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "TButton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_SURFACE,
            darkcolor=UI_BORDER,
            padding=(14, 8),
            font=("Segoe UI", 10),
        )
        style.map(
            "TButton",
            background=[("active", "#EEF4FF"), ("pressed", "#E0EAFF")],
            bordercolor=[("active", "#9DBAF8")],
        )
        style.configure(
            "Accent.TButton",
            background=UI_ACCENT,
            foreground="#FFFFFF",
            bordercolor=UI_ACCENT,
            lightcolor=UI_ACCENT,
            darkcolor=UI_ACCENT_HOVER,
            padding=(16, 9),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", UI_ACCENT_HOVER), ("pressed", UI_ACCENT_HOVER)],
            foreground=[("disabled", "#B8C0CC")],
        )
        style.configure(
            "Danger.TButton",
            foreground=UI_DANGER,
            padding=(14, 8),
            font=("Segoe UI", 10),
        )
        style.configure(
            "Muted.TLabel",
            background=UI_SURFACE,
            foreground=UI_MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Hint.TLabel",
            background=UI_SURFACE,
            foreground="#7B8794",
            font=("Segoe UI", 9),
        )
        style.configure(
            "TEntry",
            fieldbackground="#FBFCFE",
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            padding=7,
            font=("Segoe UI", 11),
        )
        style.configure(
            "TCombobox",
            fieldbackground="#FBFCFE",
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            padding=6,
            font=("Segoe UI", 11),
        )
        style.configure(
            "TCheckbutton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            padding=3,
            font=("Segoe UI", 10),
        )
        style.configure(
            "TRadiobutton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            padding=4,
            font=("Segoe UI", 10),
        )
        style.map(
            "TRadiobutton",
            foreground=[("disabled", "#98A2B3")],
            background=[("active", "#F7FAFF")],
        )
        style.configure(
            "Treeview",
            background=UI_SURFACE,
            fieldbackground=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            borderwidth=1,
            rowheight=36,
            font=("Segoe UI", 10),
        )
        style.map(
            "Treeview",
            background=[("selected", "#DCE9FF")],
            foreground=[("selected", UI_TEXT)],
        )
        style.configure(
            "Treeview.Heading",
            background="#EEF2F7",
            foreground="#475467",
            relief="flat",
            padding=(8, 9),
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", "#E5EBF4")])
        style.configure("TSeparator", background=UI_BORDER)

    def _build_layout(self) -> None:
        """创建三栏编辑布局和底部操作栏。"""

        ttk.Label(
            self.container,
            text="T1 Remote 按键映射",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(20, 3))
        ttk.Label(
            self.container,
            text="为遥控器按键配置输出动作。先选左侧按键，再在中间选择 mapping 类型。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 14))

        self._build_profile_toolbar()

        content = ttk.Frame(self.container, style="Root.TFrame")
        content.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 12))
        content.columnconfigure(0, weight=1, minsize=260)
        content.columnconfigure(1, weight=1, minsize=230)
        content.columnconfigure(2, weight=2, minsize=420)
        content.rowconfigure(0, weight=1)

        self._build_button_panel(content)
        self._build_kind_panel(content)
        self._build_detail_panel(content)
        self._build_footer()

    def _build_profile_toolbar(self) -> None:
        """创建独立配置存档的加载和保存操作栏。"""

        frame = ttk.LabelFrame(
            self.container,
            text="配置存档",
            style="Card.TLabelframe",
            padding=(12, 8),
        )
        frame.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="当前存档：").grid(row=0, column=0, padx=(10, 6), pady=8)
        self.profile_combo = ttk.Combobox(
            frame,
            textvariable=self.profile_var,
            state="readonly",
            width=36,
        )
        self.profile_combo.grid(row=0, column=1, sticky="ew", pady=8)
        ttk.Button(frame, text="加载", command=self._load_profile).grid(
            row=0, column=2, padx=(8, 4), pady=8
        )
        ttk.Button(frame, text="保存", command=self._save_profile, style="Accent.TButton").grid(
            row=0, column=3, padx=4, pady=8
        )
        ttk.Button(frame, text="另存为…", command=self._save_profile_as).grid(
            row=0, column=4, padx=(4, 10), pady=8
        )
        ttk.Label(
            frame,
            text="每个存档是一个 JSON 文件；加载后修改，点击保存即可覆盖当前存档。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=5, sticky="w", padx=10, pady=(0, 8))

    def _build_button_panel(self, parent: ttk.Frame) -> None:
        """创建物理按键列表。"""

        frame = ttk.LabelFrame(parent, text="物理按键", style="Card.TLabelframe")
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.button_tree = ttk.Treeview(
            frame,
            columns=("button", "action"),
            show="headings",
            selectmode="browse",
        )
        self.button_tree.heading("button", text="按键")
        self.button_tree.heading("action", text="当前动作")
        self.button_tree.column("button", width=115, anchor="w")
        self.button_tree.column("action", width=145, anchor="w")
        self.button_tree.tag_configure("stripe", background="#F8FAFC")
        self.button_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.button_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.button_tree.configure(yscrollcommand=scrollbar.set)
        self.button_tree.bind("<<TreeviewSelect>>", self._on_button_selected)

    def _build_kind_panel(self, parent: ttk.Frame) -> None:
        """创建动作类型选择和配置边界说明。"""

        frame = ttk.LabelFrame(parent, text="动作类型", style="Card.TLabelframe")
        frame.grid(row=0, column=1, sticky="nsew", padx=8)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, textvariable=self.input_kind_var, foreground="#52606d").grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        normal_frame = ttk.LabelFrame(
            frame, text="普通键位", style="Card.TLabelframe", padding=(8, 4)
        )
        normal_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        for row, kind in enumerate(("none", "key", "combo", "macro", "mouse")):
            radio = ttk.Radiobutton(
                normal_frame,
                text=ACTION_TYPE_LABELS[kind],
                value=kind,
                variable=self.kind_var,
                command=self._refresh_form,
            )
            self.kind_radios[kind] = radio
            radio.grid(row=row // 2, column=row % 2, sticky="w", padx=8, pady=2)

        special_frame = ttk.LabelFrame(
            frame, text="媒体与系统功能", style="Card.TLabelframe", padding=(8, 4)
        )
        special_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        special_radio = ttk.Radiobutton(
            special_frame,
            text="媒体/系统键",
            value="special",
            variable=self.kind_var,
            command=self._refresh_form,
        )
        self.kind_radios["special"] = special_radio
        special_radio.grid(row=0, column=0, sticky="w", padx=8, pady=2)

        command_frame = ttk.LabelFrame(
            frame, text="自动化", style="Card.TLabelframe", padding=(8, 4)
        )
        command_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        for column, kind in enumerate(("command", "text")):
            radio = ttk.Radiobutton(
                command_frame,
                text=ACTION_TYPE_LABELS[kind],
                value=kind,
                variable=self.kind_var,
                command=self._refresh_form,
            )
            self.kind_radios[kind] = radio
            radio.grid(row=0, column=column, sticky="w", padx=8, pady=2)

        ttk.Label(frame, text="触发方式：").grid(
            row=4, column=0, sticky="w", padx=12, pady=(0, 4)
        )
        self.trigger_combo = ttk.Combobox(
            frame,
            textvariable=self.trigger_kind_var,
            values=tuple(TRIGGER_TYPE_LABELS.values()),
            state="readonly",
        )
        self.trigger_combo.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.trigger_combo.bind("<<ComboboxSelected>>", self._on_trigger_changed)

        ttk.Separator(frame).grid(row=6, column=0, sticky="ew", padx=12, pady=2)
        ttk.Label(
            frame,
            text=(
                "未映射不会产生系统输出。\n"
                "宏只发送键位和间隔，不执行命令。\n"
                "命令行只在真实运行时启动，预览不会执行。\n"
                "COL01 仍可能保留原始键盘输入。"
            ),
            justify="left",
            style="Hint.TLabel",
            wraplength=220,
        ).grid(row=7, column=0, sticky="nw", padx=12, pady=(8, 4))

    def _build_detail_panel(self, parent: ttk.Frame) -> None:
        """创建单键、组合键、特殊键和命令行参数面板。"""

        frame = ttk.LabelFrame(parent, text="动作参数", style="Card.TLabelframe")
        frame.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        self.detail_frame = frame

        self.key_frame = ttk.Frame(frame)
        self.key_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(14, 6))
        self.key_frame.columnconfigure(1, weight=1)
        ttk.Label(self.key_frame, text="主键：").grid(row=0, column=0, sticky="w")
        self.key_combo = ttk.Combobox(
            self.key_frame,
            textvariable=self.key_var,
            state="readonly",
        )
        self.key_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.modifier_frame = ttk.Frame(frame)
        self.modifier_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(self.modifier_frame, text="修饰键：").pack(side="left")
        for modifier in FORM_MODIFIERS:
            ttk.Checkbutton(
                self.modifier_frame,
                text=modifier,
                variable=self.modifier_vars[modifier],
            ).pack(side="left", padx=(8, 0))

        self.special_hint = ttk.Label(
            frame,
            text="特殊功能使用 Windows HID 预置虚拟键。",
            foreground="#7b8794",
        )
        self.special_hint.grid(row=2, column=0, sticky="w", padx=12, pady=4)

        self.command_frame = ttk.Frame(frame)
        self.command_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)
        self.command_frame.columnconfigure(1, weight=1)
        self.command_frame.rowconfigure(1, weight=1)
        ttk.Label(self.command_frame, text="程序路径：").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Entry(self.command_frame, textvariable=self.program_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8)
        )
        ttk.Label(self.command_frame, text="参数（每行一个）：").grid(
            row=1, column=0, sticky="nw"
        )
        self.arguments_text = tk.Text(self.command_frame, height=8, width=42)
        self.arguments_text.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        self.text_frame = ttk.Frame(frame)
        self.text_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)
        self.text_frame.columnconfigure(1, weight=1)
        ttk.Label(self.text_frame, text="文字（最多100字符）：").grid(
            row=0, column=0, sticky="w"
        )
        self.text_var = tk.StringVar()
        self.text_entry = ttk.Entry(self.text_frame, textvariable=self.text_var)
        self.text_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.text_entry.configure(
            validate="key",
            validatecommand=(self.root.register(self._validate_text_length), "%P"),
        )
        self.text_count_label = ttk.Label(
            self.text_frame,
            text="0/100",
            foreground="#7b8794",
        )
        self.text_count_label.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        self.append_enter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.text_frame,
            text="输入完成后发送回车",
            variable=self.append_enter_var,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        self.text_var.trace_add("write", self._refresh_text_counter)

        self.macro_frame = ttk.Frame(frame)
        self.macro_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)
        self.macro_frame.columnconfigure(0, weight=1)
        self.macro_frame.rowconfigure(0, weight=1)
        self.macro_tree = ttk.Treeview(
            self.macro_frame,
            columns=("step", "delay"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.macro_tree.heading("step", text="宏步骤")
        self.macro_tree.heading("delay", text="本步后等待（毫秒）")
        self.macro_tree.column("step", width=220, anchor="w")
        self.macro_tree.column("delay", width=150, anchor="center")
        self.macro_tree.tag_configure("stripe", background="#F8FAFC")
        self.macro_tree.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.macro_tree.bind("<<TreeviewSelect>>", self._on_macro_step_selected)

        macro_form = ttk.Frame(self.macro_frame)
        macro_form.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        macro_form.columnconfigure(1, weight=1)
        ttk.Label(macro_form, text="步骤类型：").grid(row=0, column=0, sticky="w")
        self.macro_kind_var = tk.StringVar(value="普通键")
        self.macro_kind_combo = ttk.Combobox(
            macro_form,
            textvariable=self.macro_kind_var,
            values=("普通键", "媒体/系统键"),
            state="readonly",
            width=14,
        )
        self.macro_kind_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.macro_kind_combo.bind("<<ComboboxSelected>>", self._on_macro_kind_changed)
        ttk.Label(macro_form, text="键位：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.macro_key_var = tk.StringVar()
        self.macro_key_combo = ttk.Combobox(
            macro_form,
            textvariable=self.macro_key_var,
            state="readonly",
            width=20,
        )
        self.macro_key_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(macro_form, text="修饰键：").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.macro_modifier_vars = {
            modifier: tk.BooleanVar(value=False) for modifier in FORM_MODIFIERS
        }
        modifier_box = ttk.Frame(macro_form)
        modifier_box.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        for modifier in FORM_MODIFIERS:
            ttk.Checkbutton(
                modifier_box,
                text=modifier,
                variable=self.macro_modifier_vars[modifier],
            ).pack(side="left", padx=(0, 8))
        ttk.Label(macro_form, text="本步后等待：").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.macro_delay_var = tk.StringVar(value="100")
        ttk.Entry(macro_form, textvariable=self.macro_delay_var, width=10).grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0)
        )
        macro_buttons = ttk.Frame(self.macro_frame)
        macro_buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for text, command in (
            ("添加为下一步", self._add_macro_step),
            ("更新所选", self._update_macro_step),
            ("删除所选", self._delete_macro_step),
            ("上移", lambda: self._move_macro_step(-1)),
            ("下移", lambda: self._move_macro_step(1)),
        ):
            ttk.Button(macro_buttons, text=text, command=command).pack(
                side="left", padx=(0, 6)
            )
        ttk.Label(
            self.macro_frame,
            text="每一步都会按下再抬起；本步完成后等待指定毫秒。示例：Ctrl+C → 等待120ms → Ctrl+V。",
            foreground="#7b8794",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.macro_empty_hint = ttk.Label(
            self.macro_frame,
            text="还没有步骤：先选择键位和修饰键，再点击“添加为下一步”。",
            foreground="#7b8794",
        )
        self.macro_empty_hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(
            frame,
            text="保存前会执行字段校验；点击预览可查看最终动作。",
            foreground="#52606d",
        ).grid(row=4, column=0, sticky="w", padx=12, pady=(12, 6))

        self.trigger_frame = ttk.Frame(frame)
        self.trigger_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(6, 12))
        self.trigger_frame.columnconfigure(1, weight=1)
        self.trigger_value_label = ttk.Label(self.trigger_frame, text="")
        self.trigger_value_label.grid(row=0, column=0, sticky="w")
        self.trigger_value_entry = ttk.Entry(
            self.trigger_frame,
            textvariable=self.threshold_var,
            width=12,
        )
        self.trigger_value_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

    def _build_footer(self) -> None:
        """创建保存、预览和重载操作。"""

        footer = ttk.Frame(self.container, style="Root.TFrame")
        footer.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 18))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Root.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            footer, text="应用到当前按键", command=self._apply_current, style="Accent.TButton"
        ).grid(row=0, column=1, padx=4)
        ttk.Button(footer, text="预览动作", command=self._preview_current).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(
            footer,
            text="恢复当前键默认",
            command=self._restore_current_default,
            style="Danger.TButton",
        ).grid(row=0, column=3, padx=4)
        ttk.Button(
            footer,
            text="恢复全部默认",
            command=self._restore_default,
            style="Danger.TButton",
        ).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(footer, text="重新加载", command=self._reload_config).grid(
            row=0, column=5, padx=4
        )
        ttk.Button(footer, text="导入", command=self._import_config).grid(
            row=0, column=6, padx=4
        )
        ttk.Button(footer, text="导出", command=self._export_config).grid(
            row=0, column=7, padx=4
        )
        ttk.Button(footer, text="保存配置", command=self._save_config, style="Accent.TButton").grid(
            row=0, column=8, padx=4
        )

    def _profile_label_for_path(self, path: Path) -> str:
        """把配置路径转换为存档下拉框中的显示名称。"""

        try:
            if path.resolve().parent == self.profile_dir.resolve():
                return path.name
        except OSError:
            pass
        return "当前配置文件"

    def _refresh_profile_list(self) -> None:
        """刷新配置存档列表，不创建目录或修改文件。"""

        try:
            profile_names = sorted(
                path.name
                for path in self.profile_dir.glob("*.json")
                if path.is_file()
            )
        except OSError:
            profile_names = []
        values = ("当前配置文件", *profile_names)
        self.profile_combo.configure(values=values)
        label = self._profile_label_for_path(self.config_path)
        self.profile_var.set(label if label in values else "当前配置文件")

    def _selected_profile_path(self) -> Path:
        """解析用户在存档下拉框中选择的安全路径。"""

        label = self.profile_var.get()
        if label == "当前配置文件":
            return self.config_path
        if label not in tuple(self.profile_combo.cget("values")):
            raise MappingConfigError("所选配置存档无效")
        return self.profile_dir / label

    def _load_profile(self) -> None:
        """加载选中的配置存档，后续保存会覆盖该存档文件。"""

        try:
            selected_path = self._selected_profile_path()
        except MappingConfigError as error:
            messagebox.showerror("存档选择无效", str(error), parent=self.root)
            return
        if not selected_path.is_file():
            messagebox.showerror("存档不存在", f"找不到配置文件：{selected_path}", parent=self.root)
            return
        if not messagebox.askyesno(
            "加载配置存档",
            f"放弃当前未保存修改并加载此存档？\n\n{selected_path.name}",
            parent=self.root,
        ):
            return
        try:
            config = load_mapping_config(selected_path)
        except MappingConfigError as error:
            messagebox.showerror("存档加载失败", str(error), parent=self.root)
            return
        self.config_path = selected_path
        self._working_actions = dict(config.mappings)
        self._refresh_profile_list()
        self._refresh_button_table()
        self._select_button(self._selected_button or MAPPABLE_REMOTE_BUTTONS[0])
        self.status_var.set(f"已加载存档：{selected_path}")

    def _save_profile(self) -> None:
        """保存当前编辑内容到当前活动配置文件。"""

        config = self._build_working_config()
        if config is None:
            return
        try:
            save_mapping_config(self.config_path, config)
        except MappingConfigError as error:
            messagebox.showerror("存档保存失败", str(error), parent=self.root)
            return
        self._refresh_profile_list()
        self.status_var.set(f"已保存存档：{self.config_path}")

    def _save_profile_as(self) -> None:
        """把当前编辑内容另存为一个独立 JSON 存档。"""

        config = self._build_working_config()
        if config is None:
            return
        selected_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="另存为配置存档",
            initialdir=str(self.profile_dir),
            initialfile="t1-key-mapping-profile.json",
            defaultextension=".json",
            filetypes=(("JSON 配置存档", "*.json"), ("所有文件", "*.*")),
        )
        if not selected_path:
            return
        try:
            save_mapping_config(selected_path, config)
        except MappingConfigError as error:
            messagebox.showerror("存档保存失败", str(error), parent=self.root)
            return
        self.config_path = Path(selected_path)
        self._refresh_profile_list()
        self.status_var.set(f"已另存为：{self.config_path}")

    def _current_kind(self) -> str:
        """返回当前界面选择的内部动作类型。"""

        kind = self.kind_var.get().strip().lower()
        return kind if kind in ACTION_TYPE_LABELS else "none"

    def _refresh_button_table(self) -> None:
        """刷新物理按键和动作摘要。"""

        selected = self._selected_button
        for item in self.button_tree.get_children():
            self.button_tree.delete(item)
        for button in MAPPABLE_REMOTE_BUTTONS:
            action = self._working_actions.get(button, KeyAction("none"))
            summary = format_action_summary(action)
            self.button_tree.insert(
                "",
                "end",
                iid=button,
                values=(BUTTON_DISPLAY_NAMES.get(button, button), summary),
                tags=("stripe",) if len(self.button_tree.get_children()) % 2 else (),
            )
        if selected in MAPPABLE_REMOTE_BUTTONS:
            self.button_tree.selection_set(selected)
            self.button_tree.see(selected)

    def _on_button_selected(self, _event: tk.Event[tk.Misc]) -> None:
        """切换物理按键前校验当前表单，防止无效字段丢失。"""

        selected_items = self.button_tree.selection()
        if not selected_items:
            return
        next_button = selected_items[0]
        if next_button == self._selected_button:
            return
        if not self._apply_current(show_error=True):
            if self._selected_button:
                self.button_tree.selection_set(self._selected_button)
            return
        self._select_button(next_button)

    def _select_button(self, button: str) -> None:
        """加载一个物理按键的动作到编辑表单。"""

        self._selected_button = button
        action = self._working_actions.get(button, KeyAction("none"))
        fields = action_to_form(action)
        ui_kind = _UI_KIND_BY_ACTION_KIND.get(fields["kind"], fields["kind"])
        self.kind_var.set(ui_kind if ui_kind in ACTION_TYPE_LABELS else "none")
        self.trigger_kind_var.set(
            TRIGGER_TYPE_LABELS.get(fields["trigger_kind"], TRIGGER_TYPE_LABELS["press"])
        )
        self.threshold_var.set(str(fields["threshold_ms"]))
        self.window_var.set(str(fields["window_ms"]))
        self.interval_var.set(str(fields["interval_ms"]))
        self.key_var.set(fields["key"])
        for modifier in FORM_MODIFIERS:
            self.modifier_vars[modifier].set(modifier in fields["modifiers"])
        self.program_var.set(fields["program"])
        self.text_var.set(fields["text"])
        self.append_enter_var.set(fields["append_enter"])
        self.arguments_text.delete("1.0", "end")
        self.arguments_text.insert("1.0", "\n".join(fields["argument_lines"]))
        self._macro_steps = list(fields["macro_steps"])
        self._refresh_macro_tree()
        self._refresh_kind_options()
        self._refresh_form()
        self.button_tree.selection_set(button)
        self.button_tree.see(button)
        self.status_var.set(f"正在编辑：{BUTTON_DISPLAY_NAMES.get(button, button)}")

    def _on_kind_changed(self, _event: tk.Event[tk.Misc]) -> None:
        """根据动作类型切换可见字段。"""

        self._refresh_form()

    def _refresh_kind_options(self) -> None:
        """按当前物理按键的输入类型启用或禁用动作类型。"""

        input_kind = button_input_kind(self._selected_button or "")
        supported = _SUPPORTED_ACTION_KINDS.get(input_kind, frozenset({"none"}))
        self.input_kind_var.set(
            f"当前输入：{_INPUT_KIND_LABELS.get(input_kind, _INPUT_KIND_LABELS['unknown'])}"
        )
        for kind, radio in self.kind_radios.items():
            radio.configure(state="normal" if kind in supported else "disabled")
        if self._current_kind() not in supported:
            self.kind_var.set("none")

    @staticmethod
    def _validate_text_length(value: str) -> bool:
        """限制输入文字动作不超过 100 个 Unicode 字符。"""

        return len(value) <= 100

    def _refresh_text_counter(self, *_args: object) -> None:
        """刷新输入文字的字符计数。"""

        if hasattr(self, "text_count_label"):
            self.text_count_label.configure(text=f"{len(self.text_var.get())}/100")

    def _on_trigger_changed(self, _event: tk.Event[tk.Misc]) -> None:
        """根据触发方式切换时间参数字段。"""

        self._refresh_trigger_form()

    def _current_trigger_kind(self) -> str:
        """返回当前界面选择的内部触发类型。"""

        label = self.trigger_kind_var.get()
        for kind, display_name in TRIGGER_TYPE_LABELS.items():
            if display_name == label:
                return kind
        return "press"

    def _refresh_trigger_form(self) -> None:
        """显示长按、双击或按住重复所需的时间参数。"""

        trigger_kind = self._current_trigger_kind()
        if trigger_kind == "press" or self._current_kind() == "none":
            self.trigger_frame.grid_remove()
            return
        self.trigger_frame.grid()
        if trigger_kind == "long_press":
            self.trigger_value_label.configure(text="长按阈值（毫秒）：")
            self.trigger_value_entry.configure(textvariable=self.threshold_var)
        elif trigger_kind == "double_click":
            self.trigger_value_label.configure(text="双击间隔（毫秒）：")
            self.trigger_value_entry.configure(textvariable=self.window_var)
        else:
            self.trigger_value_label.configure(text="重复间隔（毫秒）：")
            self.trigger_value_entry.configure(textvariable=self.interval_var)

    def _refresh_form(self) -> None:
        """显示当前动作类型需要的参数控件。"""

        kind = self._current_kind()
        self.trigger_combo.configure(state="disabled" if kind == "none" else "readonly")
        for widget in (
            self.key_frame,
            self.modifier_frame,
            self.special_hint,
            self.command_frame,
            self.text_frame,
            self.macro_frame,
        ):
            widget.grid_remove()
        if kind in {"key", "combo"}:
            self.key_combo.configure(values=tuple(KEY_VIRTUAL_KEY_NAMES))
            self.key_frame.grid()
            if kind == "combo":
                self.modifier_frame.grid()
        elif kind == "special":
            self.key_combo.configure(values=tuple(SPECIAL_HID_KEY_NAMES))
            self.key_frame.grid()
            self.special_hint.grid()
        elif kind == "mouse":
            self.key_combo.configure(values=tuple(MOUSE_ACTION_NAMES))
            self.key_frame.grid()
        elif kind == "command":
            self.command_frame.grid(sticky="nsew")
        elif kind == "text":
            self.text_frame.grid(sticky="nsew")
        elif kind == "macro":
            self.macro_frame.grid(sticky="nsew")
            self._on_macro_kind_changed()
            self._refresh_macro_tree()
        trigger_values = tuple(TRIGGER_TYPE_LABELS.values())
        if kind in {"macro", "text"}:
            trigger_values = tuple(
                TRIGGER_TYPE_LABELS[item]
                for item in ("press", "long_press", "double_click")
            )
            if self._current_trigger_kind() == "hold_repeat":
                self.trigger_kind_var.set(TRIGGER_TYPE_LABELS["press"])
        self.trigger_combo.configure(values=trigger_values)
        self._refresh_trigger_form()

    def _macro_kind(self) -> str:
        """返回宏步骤的内部类型。"""

        return "special" if self.macro_kind_var.get() == "媒体/系统键" else "key"

    def _macro_step_summary(self, step: MacroStep) -> str:
        """生成人类可读的宏步骤摘要。"""

        prefix = "+".join(step.modifiers)
        return f"{prefix}+{step.key}" if prefix else step.key

    def _refresh_macro_tree(self) -> None:
        """刷新宏步骤列表，不执行任何输出。"""

        if not hasattr(self, "macro_tree"):
            return
        for item in self.macro_tree.get_children():
            self.macro_tree.delete(item)
        for index, step in enumerate(self._macro_steps):
            self.macro_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(self._macro_step_summary(step), step.delay_ms),
                tags=("stripe",) if index % 2 else (),
            )
        if hasattr(self, "macro_empty_hint"):
            self.macro_empty_hint.configure(
                text=(
                    "还没有步骤：先选择键位和修饰键，再点击“添加为下一步”。"
                    if not self._macro_steps
                    else "可选中步骤进行更新、删除或调整顺序。"
                )
            )

    def _on_macro_kind_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        """切换宏步骤的普通键/媒体系统键列表。"""

        if not hasattr(self, "macro_key_combo"):
            return
        values = SPECIAL_HID_KEY_NAMES if self._macro_kind() == "special" else KEY_VIRTUAL_KEY_NAMES
        self.macro_key_combo.configure(values=tuple(values))
        if self.macro_key_var.get() not in values:
            self.macro_key_var.set(values[0] if values else "")

    def _on_macro_step_selected(self, _event: tk.Event[tk.Misc]) -> None:
        """把选中的宏步骤加载到编辑控件。"""

        selected = self.macro_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if not 0 <= index < len(self._macro_steps):
            return
        step = self._macro_steps[index]
        self.macro_kind_var.set("媒体/系统键" if step.kind == "special" else "普通键")
        self._on_macro_kind_changed()
        self.macro_key_var.set(step.key)
        for modifier in FORM_MODIFIERS:
            self.macro_modifier_vars[modifier].set(modifier in step.modifiers)
        self.macro_delay_var.set(str(step.delay_ms))

    def _build_macro_step(self) -> MacroStep:
        """校验宏步骤编辑控件并构造一个步骤。"""

        modifiers = tuple(
            modifier
            for modifier in FORM_MODIFIERS
            if self.macro_modifier_vars[modifier].get()
        )
        try:
            delay_ms = int(self.macro_delay_var.get())
        except ValueError as error:
            raise MappingConfigError("本步后等待必须是整数") from error
        return MacroStep(
            kind=self._macro_kind(),
            key=self.macro_key_var.get(),
            modifiers=modifiers,
            delay_ms=delay_ms,
        )

    def _add_macro_step(self) -> None:
        """把当前宏步骤追加到列表。"""

        try:
            step = self._build_macro_step()
        except MappingConfigError as error:
            messagebox.showerror("宏步骤无效", str(error), parent=self.root)
            return
        self._macro_steps.append(step)
        self._refresh_macro_tree()
        self.macro_tree.selection_set(str(len(self._macro_steps) - 1))

    def _update_macro_step(self) -> None:
        """替换当前选中的宏步骤。"""

        selected = self.macro_tree.selection()
        if not selected:
            return
        try:
            step = self._build_macro_step()
        except MappingConfigError as error:
            messagebox.showerror("宏步骤无效", str(error), parent=self.root)
            return
        index = int(selected[0])
        if 0 <= index < len(self._macro_steps):
            self._macro_steps[index] = step
            self._refresh_macro_tree()
            self.macro_tree.selection_set(str(index))

    def _delete_macro_step(self) -> None:
        """删除当前选中的宏步骤。"""

        selected = self.macro_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if 0 <= index < len(self._macro_steps):
            self._macro_steps.pop(index)
            self._refresh_macro_tree()

    def _move_macro_step(self, offset: int) -> None:
        """在宏步骤列表中移动当前步骤。"""

        selected = self.macro_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        target = index + offset
        if not (0 <= index < len(self._macro_steps) and 0 <= target < len(self._macro_steps)):
            return
        self._macro_steps[index], self._macro_steps[target] = (
            self._macro_steps[target],
            self._macro_steps[index],
        )
        self._refresh_macro_tree()
        self.macro_tree.selection_set(str(target))

    def _build_current_action(self) -> KeyAction:
        """从当前控件读取并校验一个动作。"""

        argument_lines = self.arguments_text.get("1.0", "end-1c").splitlines()
        modifiers = tuple(
            modifier
            for modifier in FORM_MODIFIERS
            if self.modifier_vars[modifier].get()
        )
        return build_action_from_form(
            self._current_kind(),
            key=self.key_var.get(),
            modifiers=modifiers,
            program=self.program_var.get(),
            argument_lines=argument_lines,
            text=self.text_var.get(),
            append_enter=self.append_enter_var.get(),
            macro_steps=tuple(self._macro_steps)
            if self._current_kind() == "macro"
            else (),
            trigger_kind=self._current_trigger_kind(),
            threshold_ms=self.threshold_var.get(),
            window_ms=self.window_var.get(),
            interval_ms=self.interval_var.get(),
        )

    def _apply_current(self, show_error: bool = True) -> bool:
        """校验并暂存当前按键的动作。"""

        if not self._selected_button:
            return True
        try:
            action = self._build_current_action()
        except MappingConfigError as error:
            if show_error:
                messagebox.showerror("映射参数无效", str(error), parent=self.root)
            return False
        self._working_actions[self._selected_button] = action
        self._refresh_button_table()
        self.status_var.set(
            f"已应用到当前按键：{BUTTON_DISPLAY_NAMES.get(self._selected_button, self._selected_button)}"
        )
        return True

    def _preview_current(self) -> None:
        """显示当前动作的无副作用预览。"""

        if not self._apply_current(show_error=True):
            return
        action = self._working_actions[
            self._selected_button or MAPPABLE_REMOTE_BUTTONS[0]
        ]
        if action.kind == "none":
            preview = "当前按键未映射，不会产生系统输出。"
        elif action.kind == "text":
            suffix = "是" if action.append_enter else "否"
            preview = f"输入文字：{action.text}\n执行后发送回车：{suffix}"
        elif action.kind == "command":
            preview = f"命令 argv：{action.argv}\n\n这里只预览，不会启动程序。"
        elif action.kind == "mouse":
            preview = f"鼠标动作：{MOUSE_ACTION_LABELS.get(action.key or '', action.key)}"
        elif action.kind == "macro":
            lines = [
                f"{index}. {self._macro_step_summary(step)}，本步后等待 {step.delay_ms}ms"
                for index, step in enumerate(action.macro, start=1)
            ]
            preview = "宏步骤：\n" + "\n".join(lines)
        else:
            try:
                binding = binding_from_action(action)
            except ValueError as error:
                messagebox.showerror("动作无法预览", str(error), parent=self.root)
                return
            if binding is None:
                preview = "当前动作不会产生键盘输出。"
            else:
                preview = (
                    f"动作：{format_action_summary(action)}\n"
                    f"虚拟键码：0x{binding.virtual_key:02X}\n"
                    f"扩展键：{'是' if binding.extended else '否'}"
                )
        messagebox.showinfo("动作预览", preview, parent=self.root)

    def _restore_default(self) -> None:
        """把内存中的编辑内容恢复为安全默认配置。"""

        if not messagebox.askyesno(
            "确认恢复全部默认",
            "放弃当前未保存修改并恢复全部默认映射？",
            parent=self.root,
        ):
            return
        self._working_actions = dict(MappingConfig.default().mappings)
        self._refresh_button_table()
        self._select_button(self._selected_button or MAPPABLE_REMOTE_BUTTONS[0])
        self.status_var.set("已恢复全部默认映射，点击保存配置后才会写入文件")

    def _restore_current_default(self) -> None:
        """只把当前选中的物理按键恢复为首版默认动作。"""

        button = self._selected_button
        if not button:
            self.status_var.set("请先选择一个物理按键")
            return
        display_name = BUTTON_DISPLAY_NAMES.get(button, button)
        if not messagebox.askyesno(
            "确认恢复当前键",
            f"放弃“{display_name}”的当前修改并恢复该按键默认动作？",
            parent=self.root,
        ):
            return
        default_action = MappingConfig.default().mappings.get(button, KeyAction("none"))
        self._working_actions[button] = default_action
        self._refresh_button_table()
        self._select_button(button)
        self.status_var.set(f"已恢复当前按键默认：{display_name}，点击保存配置后才会写入文件")

    def _reload_config(self) -> None:
        """从磁盘重新读取配置并放弃未保存编辑。"""

        if not messagebox.askyesno("确认重新加载", "放弃当前未保存修改并重新加载文件？", parent=self.root):
            return
        try:
            config = (
                load_mapping_config(self.config_path)
                if self.config_path.exists()
                else MappingConfig.default()
            )
        except MappingConfigError as error:
            messagebox.showerror("配置加载失败", str(error), parent=self.root)
            return
        self._working_actions = dict(config.mappings)
        self._refresh_button_table()
        self._select_button(self._selected_button or MAPPABLE_REMOTE_BUTTONS[0])
        self.status_var.set(f"已重新加载：{self.config_path}")

    def _save_config(self) -> None:
        """校验全部编辑内容并安全保存配置文件。"""

        self._save_profile()

    def _build_working_config(self) -> MappingConfig | None:
        """校验当前表单并构造待写入配置。"""

        if not self._apply_current(show_error=True):
            return None
        try:
            return MappingConfig(mappings=dict(self._working_actions))
        except MappingConfigError as error:
            messagebox.showerror("配置校验失败", str(error), parent=self.root)
            return None

    def _import_config(self) -> None:
        """从用户选择的 JSON 文件导入到当前编辑会话。"""

        selected_path = filedialog.askopenfilename(
            parent=self.root,
            title="导入 T1 映射配置",
            initialdir=str(self.config_path.parent),
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not selected_path:
            return
        try:
            config = load_mapping_config(selected_path)
        except MappingConfigError as error:
            messagebox.showerror("配置导入失败", str(error), parent=self.root)
            return
        self._working_actions = dict(config.mappings)
        self._refresh_button_table()
        self._select_button(self._selected_button or MAPPABLE_REMOTE_BUTTONS[0])
        self.status_var.set(f"已导入（尚未覆盖当前文件）：{selected_path}")

    def _export_config(self) -> None:
        """把当前编辑会话导出到用户选择的 JSON 文件。"""

        config = self._build_working_config()
        if config is None:
            return
        selected_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出 T1 映射配置",
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
            defaultextension=".json",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not selected_path:
            return
        try:
            save_mapping_config(selected_path, config)
        except MappingConfigError as error:
            messagebox.showerror("配置导出失败", str(error), parent=self.root)
            return
        self.status_var.set(f"已导出：{selected_path}")


def _load_startup_config(config_path: Path) -> MappingConfig:
    """读取启动配置；文件不存在时返回安全默认配置。"""

    if not config_path.exists():
        return MappingConfig.default()
    return load_mapping_config(config_path)


def create_mapping_editor_tab(
    parent: tk.Misc,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> MappingEditorWindow | None:
    """在现有 Tk 窗口中创建 Mapping 设置页。"""

    root = parent.winfo_toplevel()
    try:
        config = _load_startup_config(config_path)
    except MappingConfigError as error:
        messagebox.showerror("配置加载失败", str(error), parent=root)
        return None
    return MappingEditorWindow(
        root,
        config_path,
        config,
        container=parent,
    )


def run_mapping_gui(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """创建并运行映射编辑器窗口。"""

    root = tk.Tk()
    instance_guard = SingleInstanceGuard("Local\\T1Remote.MappingEditor")
    if not instance_guard.acquire():
        messagebox.showerror("映射编辑器已运行", "已有一个映射编辑器窗口正在运行。", parent=root)
        root.destroy()
        return 1
    try:
        config = _load_startup_config(config_path)
    except MappingConfigError as error:
        messagebox.showerror("配置加载失败", str(error), parent=root)
        root.destroy()
        return 1
    try:
        MappingEditorWindow(root, config_path, config)
        root.mainloop()
        return 0
    finally:
        instance_guard.release()


def main() -> int:
    """解析命令行参数并启动配置前台。"""

    parser = argparse.ArgumentParser(description="编辑 T1 Remote Key Mapping 配置")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="映射 JSON 配置路径"
    )
    args = parser.parse_args()
    return run_mapping_gui(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
