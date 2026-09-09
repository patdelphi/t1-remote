"""程序说明：提供 T1 Remote Mapping 的启动、状态和诊断前台。

主窗口不直接处理 HID 报文，设备生命周期交给 T1MappingSession。界面负责
承载捕获、Mapping 设置和 Mapping 服务三个页面，避免多个 Tk 主循环共享同一个窗口。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk

from tools.t1_inspector_gui import build_capture_tab
from tools.t1_mapping_gui import create_mapping_editor_tab
from t1remote.windows.mapping_session import T1MappingSession
from t1remote.windows.app_icon import (
    APP_USER_MODEL_ID,
    destroy_icon_handles,
    load_icon_handles,
    set_process_app_user_model_id,
    set_window_icons,
)
from t1remote.windows.single_instance import SingleInstanceGuard
from t1remote.windows.tray import TrayIcon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "t1-key-mapping.json"
DEFAULT_CAPTURE_PATH = PROJECT_ROOT / "captures" / "t1-remote-control.json"
APP_ICON_PATH = PROJECT_ROOT / "assets" / "t1-remote-icon.ico"
MAIN_TAB_LABELS = ("捕获", "Mapping 设置", "Mapping 服务")


class MappingMonitorApp:
    """T1 Mapping 会话监视窗口。"""

    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self._session: T1MappingSession | None = None
        self.tray: TrayIcon | None = None
        self._session_lock = threading.Lock()
        self._capture_cleanup = None
        self._mapping_editor = None
        self._main_notebook: ttk.Notebook | None = None
        self._tab_by_name: dict[str, ttk.Frame] = {}
        self._native_icon_handles: tuple[int, ...] = ()
        # Dry-run 只用于测试，正式启动时默认关闭，避免误以为已经执行真实映射。
        self._dry_run_var = tk.BooleanVar(value=False)
        self._status_var = tk.StringVar(value="状态：未启动")
        self._driver_var = tk.StringVar(value="驱动：未连接")
        self._lease_var = tk.StringVar(value="租约：无")
        self._config_var = tk.StringVar(value=f"配置：{config_path}")
        self._counter_var = tk.StringVar(value="输入 0 | 忽略 0 | 映射 0 | 输出 0 | 错误 0")

        self.root.title("T1 Remote Mapping")
        try:
            self.root.iconbitmap(str(APP_ICON_PATH))
            self._native_icon_handles = load_icon_handles(APP_ICON_PATH)
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

    def _configure_app_styles(self) -> None:
        """配置三个主页面共享的浅色卡片样式和较大字号。"""

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
        """创建捕获、Mapping 设置和 Mapping 服务三个主页面。"""

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self._main_notebook = notebook

        capture_tab = ttk.Frame(notebook, style="Root.TFrame")
        mapping_tab = ttk.Frame(notebook, style="Root.TFrame")
        service_tab = ttk.Frame(notebook, style="Root.TFrame")
        self._tab_by_name = dict(zip(MAIN_TAB_LABELS, (capture_tab, mapping_tab, service_tab)))
        for label, tab in zip(MAIN_TAB_LABELS, (capture_tab, mapping_tab, service_tab)):
            notebook.add(tab, text=label)

        self._build_service_layout(service_tab)
        try:
            self._capture_cleanup = build_capture_tab(
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
        ttk.Label(diagnostics_tab, textvariable=self._counter_var).grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        self.recent_tree = ttk.Treeview(
            diagnostics_tab,
            columns=("time", "result", "button", "state", "action", "detail"),
            show="headings",
        )
        for column, heading, width in (
            ("time", "时间", 180),
            ("result", "结果", 80),
            ("button", "按键", 110),
            ("state", "状态", 70),
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

    def start_session(self) -> None:
        """在后台线程启动设备会话，避免阻塞 Tk 主循环。"""

        with self._session_lock:
            if self._session and self._session.status().state in {"starting", "running"}:
                return
            session = T1MappingSession(
                self.config_path,
                dry_run=self._dry_run_var.get(),
                on_log=self._queue_log,
                on_error=self._queue_error,
            )
            self._session = session
        threading.Thread(target=self._start_worker, args=(session,), daemon=True).start()

    def _start_worker(self, session: T1MappingSession) -> None:
        try:
            session.start()
        except Exception as error:
            self._queue_error(error)

    def stop_session(self) -> None:
        """在后台线程停止会话并释放设备输入。"""

        with self._session_lock:
            session = self._session
        if not session:
            return
        threading.Thread(target=session.stop, name="t1-mapping-stop", daemon=True).start()

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
        self.root.withdraw()
        self._append_log("窗口已隐藏到托盘")

    def show_window(self) -> None:
        """从托盘恢复主窗口。"""

        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _refresh_status(self) -> None:
        """定时刷新会话状态和最近事件表。"""

        with self._session_lock:
            session = self._session
        if session:
            status = session.status()
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
            for index, record in enumerate(diagnostics.recent_events):
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
            self.root.after(300, self._refresh_status)
        except RuntimeError:
            pass

    def _queue_log(self, message: str) -> None:
        try:
            self.root.after(0, self._append_log, message)
        except RuntimeError:
            pass

    def _queue_error(self, error: Exception) -> None:
        self._queue_log(f"错误：{error}")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def close(self) -> None:
        """关闭窗口前停止活动会话并清理捕获监听器。"""

        with self._session_lock:
            session = self._session
        if session and session.status().state not in {"stopped", "error"}:
            session.stop()
        if self._capture_cleanup:
            self._capture_cleanup()
            self._capture_cleanup = None
        self.root.destroy()
        destroy_icon_handles(self._native_icon_handles)
        self._native_icon_handles = ()


def run_app(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """启动 T1 Mapping 主前台。"""

    guard = SingleInstanceGuard("Local\\T1Remote.App")
    if not guard.acquire():
        print("已有一个 T1 Remote 主前台正在运行")
        return 1
    try:
        set_process_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        # 任务栏分组标识设置失败时仍允许主窗口启动。
        pass
    root = tk.Tk()
    tray: TrayIcon | None = None
    try:
        app = MappingMonitorApp(root, config_path)
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
