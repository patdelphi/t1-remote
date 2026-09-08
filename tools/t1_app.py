"""程序说明：提供 T1 Remote Mapping 的启动、状态和诊断前台。

主窗口不直接处理 HID 报文，设备生命周期交给 T1MappingSession。界面只负责
启动/停止会话、显示诊断快照和打开配置/采集工具，避免 Tk 控件被后台线程直接访问。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from t1remote.windows.mapping_session import T1MappingSession
from t1remote.windows.single_instance import SingleInstanceGuard
from t1remote.windows.tray import TrayIcon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "t1-key-mapping.json"
DEFAULT_CAPTURE_PATH = PROJECT_ROOT / "captures" / "t1-remote-control.json"


class MappingMonitorApp:
    """T1 Mapping 会话监视窗口。"""

    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self._session: T1MappingSession | None = None
        self.tray: TrayIcon | None = None
        self._session_lock = threading.Lock()
        self._dry_run_var = tk.BooleanVar(value=True)
        self._status_var = tk.StringVar(value="状态：未启动")
        self._driver_var = tk.StringVar(value="驱动：未连接")
        self._lease_var = tk.StringVar(value="租约：无")
        self._config_var = tk.StringVar(value=f"配置：{config_path}")
        self._counter_var = tk.StringVar(value="输入 0 | 忽略 0 | 映射 0 | 输出 0 | 错误 0")

        self.root.title("T1 Remote Mapping")
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self._build_layout()
        self._refresh_status()

    def _build_layout(self) -> None:
        """创建状态栏、操作栏和诊断/日志页。"""

        ttk.Label(
            self.root,
            text="T1 Remote Mapping",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        ttk.Label(self.root, textvariable=self._config_var, foreground="#52606d").grid(
            row=1, column=0, sticky="w", padx=16, pady=(0, 8)
        )

        toolbar = ttk.Frame(self.root)
        toolbar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        ttk.Button(toolbar, text="启动 Mapping", command=self.start_session).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(toolbar, text="停止 Mapping", command=self.stop_session).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(
            toolbar,
            text="Dry-run（不调用 SendInput/命令）",
            variable=self._dry_run_var,
        ).pack(side="left", padx=12)
        ttk.Button(toolbar, text="打开映射编辑器", command=self.open_mapping_editor).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="打开 Inspector", command=self.open_inspector).pack(
            side="left", padx=6
        )

        status_frame = ttk.Frame(self.root)
        status_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        ttk.Label(status_frame, textvariable=self._status_var).pack(side="left")
        ttk.Label(status_frame, textvariable=self._driver_var).pack(side="left", padx=18)
        ttk.Label(status_frame, textvariable=self._lease_var).pack(side="left")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 14))
        self.root.rowconfigure(4, weight=1)
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

    def open_mapping_editor(self) -> None:
        """打开独立的 JSON 映射编辑器。"""

        self._open_module("tools.t1_mapping_gui", self.config_path)

    def open_inspector(self) -> None:
        """打开独立的 T1 Raw Input Inspector。"""

        self._open_module("tools.t1_inspector", "--output", DEFAULT_CAPTURE_PATH)

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

    def _open_module(self, module: str, *arguments: object) -> None:
        try:
            subprocess.Popen(
                [sys.executable, "-m", module, *(str(argument) for argument in arguments)],
                shell=False,
            )
        except OSError as error:
            messagebox.showerror("启动工具失败", str(error), parent=self.root)

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
        """关闭窗口前停止活动会话。"""

        with self._session_lock:
            session = self._session
        if session and session.status().state not in {"stopped", "error"}:
            session.stop()
        self.root.destroy()


def run_app(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """启动 T1 Mapping 主前台。"""

    guard = SingleInstanceGuard("Local\\T1Remote.App")
    if not guard.acquire():
        print("已有一个 T1 Remote 主前台正在运行")
        return 1
    root = tk.Tk()
    tray: TrayIcon | None = None
    try:
        app = MappingMonitorApp(root, config_path)
        tray = TrayIcon(
            "T1 Remote",
            on_show=lambda: root.after(0, app.show_window),
            on_exit=lambda: root.after(0, app.close),
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
