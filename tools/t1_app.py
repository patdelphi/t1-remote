"""程序说明：提供 T1 Remote Mapping 的启动、状态和诊断前台。

主窗口不直接处理 HID 报文，设备生命周期交给 T1MappingSession。界面负责
承载捕获、Mapping 设置、Mapping 服务和语音测试页面，避免多个 Tk 主循环共享同一个窗口。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# 允许用户直接双击或执行 tools\\t1_app.py；模块入口仍保持原有行为。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.t1_inspector_gui import build_capture_tab
from tools.t1_mapping_gui import create_mapping_editor_tab
from t1remote.core.key_mapping import MappingConfig, MappingConfigError, load_mapping_config
from t1remote.core.mapping_diagnostics import diagnostic_records_to_csv
from t1remote.windows.gatt import BleDeviceInfo, discover_ble_devices
from t1remote.windows.mapping_session import T1MappingSession
from t1remote.windows.app_icon import (
    APP_USER_MODEL_ID,
    destroy_icon_handles,
    load_icon_handles,
    set_process_app_user_model_id,
    set_window_icons,
)
from t1remote.windows.single_instance import SingleInstanceGuard, activate_window_by_title
from t1remote.windows.tray import TrayIcon
from t1remote.windows.voice_session import (
    VoiceSessionController,
    VoiceSessionError,
    VoiceSessionStatus,
)
from t1remote.core.voice_replay import play_wav_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "t1-key-mapping.json"
DEFAULT_CAPTURE_PATH = PROJECT_ROOT / "captures" / "t1-remote-control.json"
APP_ICON_PATH = PROJECT_ROOT / "assets" / "t1-remote-icon.ico"
APP_ICON_RED_PATH = PROJECT_ROOT / "assets" / "t1-remote-icon-red.ico"
MAIN_TAB_LABELS = ("捕获", "Mapping 设置", "Mapping 服务", "语音测试")
MAIN_WINDOW_TITLE = "T1 Remote Mapping"


def _newest_first_records(records: object) -> tuple[object, ...]:
    """把诊断记录按最新到最旧排列，供表格和无界面测试复用。"""

    return tuple(reversed(tuple(records)))


class MappingMonitorApp:
    """T1 Mapping 会话监视窗口。"""

    def __init__(
        self,
        root: tk.Tk,
        config_path: Path,
        *,
        auto_start_mapping: bool = False,
    ) -> None:
        self.root = root
        self.config_path = config_path
        self._session: T1MappingSession | None = None
        self.tray: TrayIcon | None = None
        self._session_lock = threading.Lock()
        self._closed = False
        self._pending_after_ids: set[str] = set()
        self._capture_controller = None
        self._mapping_editor = None
        self._main_notebook: ttk.Notebook | None = None
        self._tab_by_name: dict[str, ttk.Frame] = {}
        self._native_icon_handles: tuple[int, ...] = ()
        self._native_icon_handles_by_state: dict[str, tuple[int, ...]] = {}
        self._mapping_icon_active = False
        self._voice_session = VoiceSessionController(on_status=self._queue_voice_status)
        self._voice_address_var = tk.StringVar(value="")
        self._voice_ble_choice_var = tk.StringVar(value="")
        self._voice_ble_discovery_var = tk.StringVar(value="BLE 设备：尚未扫描")
        self._voice_duration_var = tk.StringVar(value="10")
        self._voice_device_var = tk.StringVar(value="")
        self._voice_status_var = tk.StringVar(value="状态：未启动")
        self._voice_stats_var = tk.StringVar(value="PCM 输出：暂无数据")
        self._voice_recording_var = tk.StringVar(value="录音：暂无录音")
        self._voice_start_button: ttk.Button | None = None
        self._voice_stop_button: ttk.Button | None = None
        self._voice_replay_button: ttk.Button | None = None
        self._voice_scan_button: ttk.Button | None = None
        self._voice_ble_combo: ttk.Combobox | None = None
        self._voice_ble_devices: dict[str, str] = {}
        self._voice_last_recording_path: Path | None = None
        self._voice_replay_in_progress = False
        self._voice_waveform_canvas: tk.Canvas | None = None
        self._voice_waveform_points: tuple[float, ...] = ()
        # Dry-run 只用于测试，正式启动时默认关闭，避免误以为已经执行真实映射。
        self._dry_run_var = tk.BooleanVar(value=False)
        self._status_var = tk.StringVar(value="状态：未启动")
        self._driver_var = tk.StringVar(value="驱动：未连接")
        self._lease_var = tk.StringVar(value="租约：无")
        self._config_var = tk.StringVar(value=f"配置：{config_path}")
        self._counter_var = tk.StringVar(value="输入 0 | 忽略 0 | 映射 0 | 输出 0 | 错误 0")

        self.root.title(MAIN_WINDOW_TITLE)
        try:
            self.root.iconbitmap(str(APP_ICON_PATH))
            self._native_icon_handles = load_icon_handles(APP_ICON_PATH)
            self._native_icon_handles_by_state["idle"] = self._native_icon_handles
            set_window_icons(self.root.winfo_id(), self._native_icon_handles)
        except (OSError, tk.TclError):
            # 图标文件缺失或当前 Tk 不支持 ICO 时，保留默认窗口图标，不影响主程序。
            pass
        self.root.geometry("1520x900")
        self.root.minsize(1280, 760)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._configure_app_styles()
        self._build_layout()
        self._refresh_status()
        if auto_start_mapping:
            # 主前台启动后自动接管 Mapping，避免“窗口已打开但映射未运行”。
            self._schedule_ui(200, self.start_session)

    def _configure_app_styles(self) -> None:
        """配置主页面共享的浅色卡片样式和较大字号。"""

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("Root.TFrame", background="#F4F7FB")
        style.configure(
            "TLabel",
            background="#FFFFFF",
            foreground="#172033",
            font=("Segoe UI", 11),
        )
        style.configure(
            "Root.TLabel",
            background="#F4F7FB",
            foreground="#172033",
            font=("Segoe UI", 11),
        )
        style.configure(
            "TButton",
            background="#FFFFFF",
            foreground="#172033",
            bordercolor="#D8E0EB",
            padding=(14, 8),
            font=("Segoe UI", 10),
        )
        style.map(
            "TButton",
            background=[("active", "#EEF4FF"), ("pressed", "#E0EAFF")],
        )
        style.configure(
            "TCheckbutton",
            background="#FFFFFF",
            foreground="#172033",
            padding=4,
            font=("Segoe UI", 10),
        )
        style.configure(
            "TRadiobutton",
            background="#FFFFFF",
            foreground="#172033",
            padding=4,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Card.TLabelframe",
            background="#FFFFFF",
            bordercolor="#D8E0EB",
            relief="solid",
            borderwidth=1,
            padding=10,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background="#FFFFFF",
            foreground="#172033",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#172033",
            rowheight=36,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#EEF2F7",
            foreground="#475467",
            padding=(8, 9),
            font=("Segoe UI", 10, "bold"),
        )

    def _build_layout(self) -> None:
        """创建捕获、Mapping 设置、Mapping 服务和语音测试页面。"""

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self._main_notebook = notebook

        capture_tab = ttk.Frame(notebook, style="Root.TFrame")
        mapping_tab = ttk.Frame(notebook, style="Root.TFrame")
        service_tab = ttk.Frame(notebook, style="Root.TFrame")
        voice_tab = ttk.Frame(notebook, style="Root.TFrame")
        tabs = (capture_tab, mapping_tab, service_tab, voice_tab)
        self._tab_by_name = dict(zip(MAIN_TAB_LABELS, tabs))
        for label, tab in zip(MAIN_TAB_LABELS, tabs):
            notebook.add(tab, text=label)

        self._build_service_layout(service_tab)
        try:
            self._capture_controller = build_capture_tab(
                capture_tab,
                DEFAULT_CAPTURE_PATH,
            )
        except Exception as error:
            self._append_log(f"捕获页启动失败：{error}")
            ttk.Label(
                capture_tab,
                text=f"捕获页启动失败：{error}",
                foreground="#B42318",
            ).pack(anchor="w", padx=20, pady=20)

        self._mapping_editor = create_mapping_editor_tab(
            mapping_tab,
            self.config_path,
        )
        if self._mapping_editor is None:
            ttk.Label(
                mapping_tab,
                text="Mapping 设置页加载失败，请查看 Mapping 服务页日志。",
                foreground="#B42318",
            ).pack(anchor="w", padx=20, pady=20)
        self._build_voice_layout(voice_tab)
        # 扫描放到 Tk 事件循环后执行，避免阻塞窗口首次绘制。
        self._schedule_ui(100, self.scan_voice_devices)

    def _schedule_ui(self, delay_ms: int, callback, *args: object) -> None:
        """登记 Tk 回调，关闭窗口时统一取消，避免后台线程回调悬空。"""

        if self._closed:
            return
        callback_id: list[str] = []

        def invoke() -> None:
            if callback_id:
                self._pending_after_ids.discard(callback_id[0])
            if not self._closed:
                callback(*args)

        try:
            after_id = str(self.root.after(delay_ms, invoke))
        except RuntimeError:
            return
        callback_id.append(after_id)
        self._pending_after_ids.add(after_id)

    def _build_voice_layout(self, parent: ttk.Frame) -> None:
        """创建已验证 T1 ATVV 语音链路的最小可用控制页。"""

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        ttk.Label(parent, text="T1 语音输入", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 2)
        )
        ttk.Label(
            parent,
            text="连接 T1 的 ATVV GATT 音频，并将 PCM 写入已安装的 CABLE Input。",
            foreground="#52606d",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))

        form = ttk.LabelFrame(parent, text="会话参数", style="Card.TLabelframe")
        form.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="BLE 地址或设备标识").grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        ttk.Entry(form, textvariable=self._voice_address_var, width=36).grid(
            row=0, column=1, sticky="ew", padx=(8, 4), pady=8
        )
        self._voice_scan_button = ttk.Button(
            form, text="扫描并预填", command=self.scan_voice_devices
        )
        self._voice_scan_button.grid(row=0, column=2, sticky="w", padx=(4, 8), pady=8)
        ttk.Label(form, text="发现的 BLE 设备").grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        self._voice_ble_combo = ttk.Combobox(
            form,
            textvariable=self._voice_ble_choice_var,
            state="readonly",
            width=42,
        )
        self._voice_ble_combo.grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=8
        )
        self._voice_ble_combo.bind("<<ComboboxSelected>>", self._on_voice_ble_device_selected)
        ttk.Label(form, textvariable=self._voice_ble_discovery_var, foreground="#52606d").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8)
        )
        ttk.Label(form, text="时长（秒）").grid(row=3, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(form, textvariable=self._voice_duration_var, width=12).grid(
            row=3, column=1, sticky="w", padx=8, pady=8
        )
        ttk.Label(form, text="输出设备编号（可选）").grid(
            row=4, column=0, sticky="w", padx=8, pady=8
        )
        ttk.Entry(form, textvariable=self._voice_device_var, width=12).grid(
            row=4, column=1, sticky="w", padx=8, pady=8
        )
        ttk.Label(
            form,
            text="留空时自动选择 Windows WASAPI 的 CABLE Input；目标应用麦克风请选择 CABLE Output。",
            foreground="#52606d",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="new", padx=16, pady=8)
        self._voice_start_button = ttk.Button(
            actions, text="启动语音测试", command=self.start_voice_session
        )
        self._voice_start_button.pack(side="left", padx=(0, 8))
        self._voice_stop_button = ttk.Button(
            actions, text="停止语音测试", command=self.stop_voice_session, state="disabled"
        )
        self._voice_stop_button.pack(side="left")
        self._voice_replay_button = ttk.Button(
            actions,
            text="播放最近录音",
            command=self.play_last_voice_recording,
            state="disabled",
        )
        self._voice_replay_button.pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self._voice_status_var).pack(side="left", padx=18)
        waveform_frame = ttk.LabelFrame(parent, text="实时波形", style="Card.TLabelframe")
        waveform_frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(8, 4))
        waveform_frame.columnconfigure(0, weight=1)
        waveform_frame.rowconfigure(0, weight=1)
        self._voice_waveform_canvas = tk.Canvas(
            waveform_frame,
            height=180,
            background="#101828",
            highlightthickness=0,
        )
        self._voice_waveform_canvas.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._draw_voice_waveform()
        ttk.Label(parent, textvariable=self._voice_stats_var).grid(
            row=5, column=0, sticky="nw", padx=24, pady=(8, 0)
        )
        ttk.Label(parent, textvariable=self._voice_recording_var).grid(
            row=6, column=0, sticky="nw", padx=24, pady=(4, 0)
        )

    def _build_service_layout(self, parent: ttk.Frame) -> None:
        """创建 Mapping 服务页的状态栏、操作栏和诊断/日志页。"""

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)

        ttk.Label(
            parent,
            text="T1 Remote Mapping",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        ttk.Label(parent, textvariable=self._config_var, foreground="#52606d").grid(
            row=1, column=0, sticky="w", padx=16, pady=(0, 8)
        )

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        ttk.Button(toolbar, text="启动 Mapping", command=self.start_session).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(toolbar, text="停止 Mapping", command=self.stop_session).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="重新加载配置", command=self.reload_config).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="退出应用", command=self.request_exit).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(
            toolbar,
            text="Dry-run（不调用 SendInput/命令）",
            variable=self._dry_run_var,
        ).pack(side="left", padx=12)
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        ttk.Label(status_frame, textvariable=self._status_var).pack(side="left")
        ttk.Label(status_frame, textvariable=self._driver_var).pack(side="left", padx=18)
        ttk.Label(status_frame, textvariable=self._lease_var).pack(side="left")

        notebook = ttk.Notebook(parent)
        notebook.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 14))
        parent.rowconfigure(4, weight=1)
        diagnostics_tab = ttk.Frame(notebook)
        log_tab = ttk.Frame(notebook)
        notebook.add(diagnostics_tab, text="诊断")
        notebook.add(log_tab, text="日志")

        diagnostics_tab.columnconfigure(0, weight=1)
        diagnostics_tab.rowconfigure(1, weight=1)
        diagnostics_toolbar = ttk.Frame(diagnostics_tab)
        diagnostics_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Label(diagnostics_toolbar, textvariable=self._counter_var).pack(side="left")
        ttk.Button(
            diagnostics_toolbar,
            text="复制最新30条",
            command=self._copy_diagnostics,
        ).pack(side="right")
        self.recent_tree = ttk.Treeview(
            diagnostics_tab,
            columns=("time", "result", "button", "state", "action", "detail"),
            show="headings",
        )
        for column, heading, width in (
            ("time", "时间", 180),
            ("result", "结果", 80),
            ("button", "按键", 110),
            ("state", "状态", 105),
            ("action", "动作", 90),
            ("detail", "说明", 220),
        ):
            self.recent_tree.heading(column, text=heading)
            self.recent_tree.column(column, width=width, anchor="w")
        self.recent_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        recent_scrollbar = ttk.Scrollbar(
            diagnostics_tab, orient="vertical", command=self.recent_tree.yview
        )
        recent_scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        self.recent_tree.configure(yscrollcommand=recent_scrollbar.set)

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_tab, state="disabled", wrap="none")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        log_scrollbar = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

    def _select_tab(self, name: str) -> None:
        """切换到指定主功能页。"""

        if self._main_notebook is None:
            return
        tab = self._tab_by_name.get(name)
        if tab is not None:
            self._main_notebook.select(tab)

    def _copy_diagnostics(self) -> None:
        """复制当前诊断快照中最新 30 条记录。"""

        with self._session_lock:
            session = self._session
        records = ()
        if session is not None:
            records = session.status().diagnostics.recent_events
        text = diagnostic_records_to_csv(records)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            # 保持 Tk 对剪贴板的所有权，避免按钮返回后文本立即消失。
            self.root.update()
        except (RuntimeError, tk.TclError) as error:
            self._append_log(f"复制诊断失败：{error}")
            return
        self._append_log(f"已复制最新 {min(30, len(records))} 条诊断记录")

    def start_session(self) -> None:
        """在后台线程启动设备会话，避免阻塞 Tk 主循环。"""

        with self._session_lock:
            if self._session and self._session.status().state in {"starting", "running"}:
                return
            try:
                # 点击启动时明确读取一次磁盘配置，随后把同一份配置交给会话。
                config = load_mapping_config(self.config_path)
            except MappingConfigError as error:
                self._queue_error(error)
                self._set_mapping_icon(False)
                return
            session = T1MappingSession(
                self.config_path,
                dry_run=self._dry_run_var.get(),
                on_log=self._queue_log,
                on_error=self._queue_error,
            )
            self._session = session
        if self._capture_controller is not None:
            self._capture_controller.set_mapping_active(True)
        threading.Thread(
            target=self._start_worker,
            args=(session, config),
            name="t1-mapping-start",
            daemon=True,
        ).start()

    def _start_worker(self, session: T1MappingSession, config: MappingConfig) -> None:
        try:
            session.start(config)
            # 只有会话真正进入启动流程后才显示运行态图标。
            self._queue_mapping_icon(True)
        except Exception as error:
            if self._capture_controller is not None:
                self._capture_controller.set_mapping_active(False)
            self._queue_mapping_icon(False)
            self._queue_error(error)

    def stop_session(self) -> None:
        """在后台线程停止会话并释放设备输入。"""

        with self._session_lock:
            session = self._session
        if not session:
            return
        threading.Thread(
            target=self._stop_worker,
            args=(session,),
            name="t1-mapping-stop",
            daemon=True,
        ).start()

    def _stop_worker(self, session: T1MappingSession) -> None:
        """停止 Mapping 后恢复捕获页的按需 HID 直读。"""

        try:
            session.stop()
        finally:
            if self._capture_controller is not None:
                self._capture_controller.set_mapping_active(False)
            self._queue_mapping_icon(False)

    def _queue_mapping_icon(self, active: bool) -> None:
        """把后台线程的图标更新请求投递到 Tk 主线程。"""

        try:
            self._schedule_ui(0, self._set_mapping_icon, active)
        except RuntimeError:
            pass

    def _set_mapping_icon(self, active: bool) -> None:
        """切换 Mapping 运行态图标；失败时静默保留默认图标。"""

        if active == self._mapping_icon_active:
            return
        target = APP_ICON_RED_PATH if active else APP_ICON_PATH
        if active and not target.is_file():
            return
        try:
            handles = self._native_icon_handles_by_state.get("active" if active else "idle")
            if not handles:
                handles = load_icon_handles(target)
                self._native_icon_handles_by_state["active" if active else "idle"] = handles
            self.root.iconbitmap(str(target))
            set_window_icons(self.root.winfo_id(), handles)
            self._native_icon_handles = handles
            self._mapping_icon_active = active
            if self.tray is not None:
                self.tray.set_icon(target)
        except (OSError, tk.TclError):
            pass

    def reload_config(self) -> None:
        """在后台线程手动重新加载当前映射配置。"""

        with self._session_lock:
            session = self._session
        if not session:
            self._append_log("重新加载失败：Mapping 会话尚未启动")
            return
        if session.status().state != "running":
            self._append_log("重新加载失败：Mapping 会话当前未运行")
            return
        threading.Thread(
            target=self._reload_config_worker,
            args=(session,),
            name="t1-mapping-reload",
            daemon=True,
        ).start()

    def _reload_config_worker(self, session: T1MappingSession) -> None:
        """执行配置重载并把异常投递到 Tk 主线程。"""

        try:
            session.reload_config()
        except Exception as error:
            self._queue_error(error)

    def start_voice_session(self) -> None:
        """从表单读取参数并在后台启动真实语音会话。"""

        address = self._voice_address_var.get().strip()
        if not address:
            self._append_log("语音测试失败：请填写 BLE 地址或设备标识")
            return
        try:
            duration = float(self._voice_duration_var.get().strip() or "10")
            if duration < 0:
                raise ValueError("时长不能为负数")
            device_text = self._voice_device_var.get().strip()
            device_index = int(device_text) if device_text else None
        except ValueError as error:
            self._append_log(f"语音测试参数无效：{error}")
            return
        try:
            self._voice_session.start(
                address,
                duration_seconds=duration,
                device_index=device_index,
            )
        except (ValueError, VoiceSessionError) as error:
            self._append_log(f"语音测试启动失败：{error}")

    def scan_voice_devices(self) -> None:
        """后台扫描 BLE 设备，并优先预填名称包含 T1 的设备地址。"""

        if self._voice_scan_button is not None:
            if str(self._voice_scan_button.cget("state")) == "disabled":
                return
            self._voice_scan_button.configure(state="disabled")
        self._voice_ble_discovery_var.set("BLE 设备：正在扫描……")
        threading.Thread(
            target=self._scan_voice_devices_worker,
            name="t1-voice-ble-scan",
            daemon=True,
        ).start()

    def _scan_voice_devices_worker(self) -> None:
        """执行不阻塞 Tk 的 BLE 扫描。"""

        try:
            devices = discover_ble_devices()
            error: Exception | None = None
        except Exception as scan_error:
            devices = ()
            error = scan_error
        try:
            self._schedule_ui(0, self._apply_voice_devices, devices, error)
        except RuntimeError:
            pass

    def _apply_voice_devices(
        self,
        devices: tuple[BleDeviceInfo, ...],
        error: Exception | None,
    ) -> None:
        """在 Tk 主线程更新设备列表和自动填充地址。"""

        if self._voice_scan_button is not None:
            self._voice_scan_button.configure(state="normal")
        if error is not None:
            self._voice_ble_discovery_var.set(f"BLE 设备：扫描失败（{error}）")
            return
        self._voice_ble_devices = {
            self._voice_device_label(device): device.address for device in devices
        }
        values = tuple(self._voice_ble_devices)
        if self._voice_ble_combo is not None:
            self._voice_ble_combo.configure(values=values)
        if not devices:
            self._voice_ble_discovery_var.set(
                "BLE 设备：未发现设备，请确认 T1 已开机、靠近电脑并已完成蓝牙配对"
            )
            return
        current_address = self._voice_address_var.get().strip()
        preferred = tuple(
            device
            for device in devices
            if device.is_t1_candidate
            or "t1" in device.name.casefold()
            or "remote" in device.name.casefold()
        )
        selected = preferred[0] if preferred else devices[0] if len(devices) == 1 else None
        if not current_address and selected is not None:
            self._voice_address_var.set(selected.address)
            self._voice_ble_choice_var.set(self._voice_device_label(selected))
        t1_count = sum(device.is_t1_candidate for device in devices)
        self._voice_ble_discovery_var.set(
            f"BLE 设备：发现 {len(devices)} 个，识别到 {t1_count} 个 T1/Remote 设备；已自动选择"
            if selected
            else "BLE 设备：未识别到 T1 服务，请唤醒 T1 后重新扫描，或手动选择设备"
        )

    @staticmethod
    def _voice_device_label(device: BleDeviceInfo) -> str:
        """把设备名称、地址和 T1 服务识别结果组合成可读标签。"""

        if device.is_t1_service_candidate:
            name = "T1 Remote（ATVV）"
        elif device.is_probable_t1 and device.paired:
            name = f"{device.name}（系统已缓存）"
        else:
            name = device.name
        return f"{name}（{device.address}）"

    def _on_voice_ble_device_selected(self, _event: tk.Event[tk.Misc]) -> None:
        """把下拉列表选中的 BLE 地址写入语音会话输入框。"""

        address = self._voice_ble_devices.get(self._voice_ble_choice_var.get())
        if address:
            self._voice_address_var.set(address)

    def stop_voice_session(self) -> None:
        """请求停止语音会话，等待 BLE 和音频输出安全关闭。"""

        try:
            self._voice_session.stop(wait=False)
        except VoiceSessionError as error:
            self._append_log(f"语音测试停止失败：{error}")

    def play_last_voice_recording(self) -> None:
        """在后台线程播放最近一次语音测试录音，避免阻塞 Tk。"""

        path = self._voice_last_recording_path
        if self._voice_replay_in_progress:
            return
        if path is None or not path.is_file():
            self._append_log("语音回放失败：最近录音文件不存在")
            return
        self._voice_replay_in_progress = True
        if self._voice_replay_button is not None:
            self._voice_replay_button.configure(state="disabled")
        self._voice_recording_var.set(f"录音：正在播放 {path}")
        threading.Thread(
            target=self._play_voice_recording_worker,
            args=(path,),
            name="t1-voice-replay",
            daemon=True,
        ).start()

    def _play_voice_recording_worker(self, path: Path) -> None:
        """执行 WAV 回放并把结果投递回 Tk 主线程。"""

        error: Exception | None = None
        try:
            play_wav_file(path)
        except Exception as replay_error:
            error = replay_error
        try:
            self._schedule_ui(0, self._finish_voice_replay, path, error)
        except RuntimeError:
            pass

    def _finish_voice_replay(self, path: Path, error: Exception | None) -> None:
        """恢复回放按钮并显示回放结果。"""

        self._voice_replay_in_progress = False
        if self._voice_replay_button is not None:
            self._voice_replay_button.configure(
                state="normal" if path.is_file() else "disabled"
            )
        if error is not None:
            self._voice_recording_var.set(f"录音：回放失败（{error}）")
            self._append_log(f"语音回放失败：{error}")
        else:
            self._voice_recording_var.set(f"录音：已播放 {path}")

    def _queue_voice_status(self, status: VoiceSessionStatus) -> None:
        """把后台线程状态转发到 Tk 主线程。"""

        try:
            self._schedule_ui(0, self._apply_voice_status, status)
        except RuntimeError:
            pass

    def _apply_voice_status(self, status: VoiceSessionStatus) -> None:
        """更新语音页状态和输出统计。"""

        self._voice_status_var.set(f"状态：{status.state}（{status.message}）")
        if status.waveform_points:
            self._append_voice_waveform(status.waveform_points)
        active = status.state in VoiceSessionController._ACTIVE_STATES
        if self._voice_start_button is not None:
            self._voice_start_button.configure(state="disabled" if active else "normal")
        if self._voice_stop_button is not None:
            self._voice_stop_button.configure(state="normal" if active else "disabled")
        if status.result is not None:
            worker = getattr(status.result, "worker", None)
            if worker is not None:
                self._voice_stats_var.set(
                    "PCM 输出："
                    f"chunks={getattr(worker, 'chunks_written', 0)} "
                    f"bytes={getattr(worker, 'bytes_written', 0)} "
                    f"error={getattr(worker, 'error', None) or 'none'}"
                )
            recording_path = getattr(status.result, "recording_path", None)
            if recording_path:
                self._voice_last_recording_path = Path(recording_path)
                self._voice_recording_var.set(f"录音：{self._voice_last_recording_path}")
                if self._voice_replay_button is not None and not self._voice_replay_in_progress:
                    self._voice_replay_button.configure(state="normal")
        if status.error is not None:
            self._append_log(f"语音测试失败：{status.error}")

    def _append_voice_waveform(self, points: tuple[float, ...]) -> None:
        """追加最近波形点并重绘实时波形。"""

        self._voice_waveform_points = (*self._voice_waveform_points, *points)[-240:]
        self._draw_voice_waveform()

    def _draw_voice_waveform(self) -> None:
        """在 Canvas 上绘制中心线和归一化 PCM 波形。"""

        canvas = self._voice_waveform_canvas
        if canvas is None:
            return
        width = max(320, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        canvas.delete("all")
        center = height / 2
        canvas.create_line(0, center, width, center, fill="#475467")
        points = self._voice_waveform_points
        if len(points) < 2:
            canvas.create_text(
                width / 2,
                center,
                text="等待 PCM 音频…",
                fill="#98A2B3",
            )
            return
        coordinates: list[float] = []
        amplitude = height * 0.44
        for index, point in enumerate(points):
            x = index * (width - 1) / (len(points) - 1)
            y = center - max(-1.0, min(1.0, point)) * amplitude
            coordinates.extend((x, y))
        canvas.create_line(*coordinates, fill="#53B1FD", width=2, smooth=False)

    def open_mapping_editor(self) -> None:
        """兼容旧调用：切换到 Mapping 设置页。"""

        self._select_tab("Mapping 设置")

    def open_inspector(self) -> None:
        """兼容旧调用：切换到捕获页。"""

        self._select_tab("捕获")

    def minimize_to_tray(self) -> None:
        """关闭窗口时隐藏到托盘；没有托盘时直接退出。"""

        if not self.tray or not self.tray.is_running:
            self.close()
            return
        # 保留任务栏窗口项，只把主窗最小化；withdraw 会让任务栏按钮一起消失。
        self.root.iconify()
        self._append_log("窗口已最小化到托盘")

    def show_window(self) -> None:
        """从托盘恢复主窗口。"""

        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _monitor_tray(self) -> None:
        """托盘线程意外退出时恢复主窗口，避免应用永久隐藏。"""

        if self.tray and not self.tray.is_running and self.root.state() in {
            "withdrawn",
            "iconic",
        }:
            self._append_log("托盘图标已退出，已恢复主窗口")
            self.show_window()
        self._schedule_ui(1000, self._monitor_tray)

    def _refresh_status(self) -> None:
        """定时刷新会话状态和最近事件表。"""

        with self._session_lock:
            session = self._session
        if session:
            status = session.status()
            if status.state in {"error", "stopped"} and self._capture_controller is not None:
                # 工作线程故障时没有经过 stop 按钮，也要恢复捕获页的直读门控。
                self._capture_controller.set_mapping_active(False)
            if status.state in {"error", "stopped"}:
                self._set_mapping_icon(False)
            self._status_var.set(f"状态：{status.state}（{status.message}）")
            self._driver_var.set(f"驱动：{status.driver_state or '未连接'}")
            self._lease_var.set(f"租约：{'有效' if status.lease_active else '无'}")
            diagnostics = status.diagnostics
            self._counter_var.set(
                f"输入 {diagnostics.input_events} | 忽略 {diagnostics.ignored_inputs} | "
                f"映射 {diagnostics.mapping_events} | 输出 {diagnostics.output_events} | "
                f"命令 {diagnostics.command_events} | 错误 {diagnostics.errors}"
            )
            for item in self.recent_tree.get_children():
                self.recent_tree.delete(item)
            for index, record in enumerate(_newest_first_records(diagnostics.recent_events)):
                self.recent_tree.insert(
                    "",
                    "end",
                    iid=f"record-{index}",
                    values=(
                        record.timestamp_local,
                        record.result,
                        record.button or "-",
                        record.state,
                        record.action_kind or "-",
                        record.detail,
                    ),
                )
        try:
            self._schedule_ui(300, self._refresh_status)
        except RuntimeError:
            pass

    def _queue_log(self, message: str) -> None:
        try:
            self._schedule_ui(0, self._append_log, message)
        except RuntimeError:
            pass

    def _queue_error(self, error: Exception) -> None:
        self._queue_log(f"错误：{error}")

    def request_exit(self) -> None:
        """经确认后退出应用并释放 Mapping、音频和输入资源。"""

        if messagebox.askyesno(
            "确认退出应用",
            "确定退出 T1 Remote Mapping 吗？当前 Mapping 和语音会话会停止。",
            parent=self.root,
        ):
            self.close()

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def close(self) -> None:
        """关闭窗口前停止活动会话并清理捕获监听器。"""

        if self._closed:
            return
        self._closed = True
        for callback_id in tuple(self._pending_after_ids):
            try:
                self.root.after_cancel(callback_id)
            except (RuntimeError, tk.TclError):
                pass
        self._pending_after_ids.clear()
        with self._session_lock:
            session = self._session
        if session and session.status().state not in {"stopped", "error"}:
            session.stop()
        self._set_mapping_icon(False)
        try:
            self._voice_session.stop(wait=True)
        except VoiceSessionError as error:
            self._append_log(f"语音会话关闭失败：{error}")
        if self._capture_controller:
            self._capture_controller.cleanup()
            self._capture_controller = None
        self.root.destroy()
        unique_handles = {
            handle
            for handles in self._native_icon_handles_by_state.values()
            for handle in handles
        }
        destroy_icon_handles(tuple(unique_handles))
        self._native_icon_handles = ()
        self._native_icon_handles_by_state = {}


def run_app(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """启动 T1 Mapping 主前台。"""

    guard = SingleInstanceGuard("Local\\T1Remote.App")
    if not guard.acquire():
        if activate_window_by_title(MAIN_WINDOW_TITLE):
            print("已有一个 T1 Remote 主前台，已唤起旧窗口")
        else:
            print("已有一个 T1 Remote 主前台，但未找到可唤起的窗口")
        return 0
    try:
        set_process_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        # 任务栏分组标识设置失败时仍允许主窗口启动。
        pass
    root = tk.Tk()
    tray: TrayIcon | None = None
    try:
        app = MappingMonitorApp(root, config_path, auto_start_mapping=True)
        tray = TrayIcon(
            "T1 Remote",
            on_show=lambda: root.after(0, app.show_window),
            on_exit=lambda: root.after(0, app.close),
            icon_path=APP_ICON_PATH,
        )
        try:
            tray.start()
            app.tray = tray
            root.protocol("WM_DELETE_WINDOW", app.minimize_to_tray)
            app._schedule_ui(1000, app._monitor_tray)
        except Exception as error:
            app._append_log(f"托盘不可用：{error}")
            root.protocol("WM_DELETE_WINDOW", app.close)
        root.mainloop()
        return 0
    finally:
        if tray:
            tray.stop()
        guard.release()


def main() -> int:
    """解析命令行参数并启动主前台。"""

    parser = argparse.ArgumentParser(description="T1 Remote Mapping 主前台")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    return run_app(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
