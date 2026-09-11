"""程序说明：以 T1 遥控器正面产品图为中心，提供 14 键报文采集界面。"""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Callable

import tkinter as tk
from tkinter import messagebox, ttk

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    CaptureEvent,
    build_logical_actions,
    collection_from_device_path,
    is_t1_device_path,
    selected_capture_button,
)
from t1remote.windows.hid_input import HidInputEvent, HidInputListener
from t1remote.windows.driver_bridge import (
    BridgeStatus,
    T1BridgeClient,
    build_default_interception_policy,
)
from t1remote.windows.raw_input import RawInputEvent, RawInputListener


PRODUCT_IMAGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "t1-remote-front-clean-v2.png"
)
BUTTON_DISPLAY_NAMES = {
    "Volume Plus": "Volume +",
    "Volume Minus": "Volume -",
}


def should_ignore_passive_t1_raw_event(
    collection: str,
    *,
    direct_hid_active: bool,
    bridge_active: bool,
) -> bool:
    """判断是否应丢弃被动 Raw Input，避免与主动读取路径重复。"""

    return collection in ("COL02", "COL03") and (
        direct_hid_active or bridge_active
    )


class CaptureBridgeSession:
    """维护捕获页使用的驱动拦截会话和原始报文队列。"""

    def __init__(self, bridge_factory: Callable[[], object] = T1BridgeClient) -> None:
        self._bridge_factory = bridge_factory
        self._bridge: object | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._on_event: Callable[[object], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None
        self._status: BridgeStatus | None = None

    def start(
        self,
        on_event: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> BridgeStatus:
        """启动桥接，并确认驱动已经进入有效拦截态。"""

        with self._lifecycle_lock:
            if self._bridge is not None or (
                self._thread is not None and self._thread.is_alive()
            ):
                if self._status is None:
                    raise RuntimeError("捕获桥接会话状态不可用")
                return self._status
            bridge = self._bridge_factory()
            policy = build_default_interception_policy(
                enabled=True,
                lease_required=True,
            )
            try:
                bridge.open(policy)  # type: ignore[attr-defined]
                get_preparsed_data = getattr(bridge, "get_preparsed_data", None)
                if not callable(get_preparsed_data):
                    raise RuntimeError("驱动 HID parser 接口不可用，无法安全拦截 Power")
                # 先让驱动缓存两个目标 Collection 的 opaque preparsed data，
                # 这样内核按真实 HID Usage（Power 为 0x0081）解析报告。
                for collection in ("COL02", "COL03"):
                    bytes(get_preparsed_data(collection))
                bridge.start()  # type: ignore[attr-defined]
                # 租约策略必须在启动后立即刷新一次，避免首个报告到达前失租约。
                bridge.heartbeat()  # type: ignore[attr-defined]
                status = bridge.status()  # type: ignore[attr-defined]
                if status.state != "running" or not status.lease_active:
                    raise RuntimeError(
                        "驱动未进入有效拦截态："
                        f"状态={status.state}，租约={'有效' if status.lease_active else '无效'}"
                    )
            except Exception:
                self._stop_bridge(bridge)
                raise
            self._bridge = bridge
            self._status = status
            self._on_event = on_event
            self._on_error = on_error
            self._stop_requested.clear()
            self._thread = threading.Thread(
                target=self._read_loop,
                name="t1-capture-bridge",
                daemon=True,
            )
            self._thread.start()
            return status

    def stop(self) -> None:
        """停止读取线程和拦截租约；重复调用不会重复释放桥接句柄。"""

        self._stop_requested.set()
        with self._lifecycle_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lifecycle_lock:
            if thread is self._thread:
                self._thread = None
            bridge = self._bridge
            self._bridge = None
            self._status = None
            self._on_event = None
            self._on_error = None
        if bridge is not None:
            self._stop_bridge(bridge)

    def _read_loop(self) -> None:
        """后台读取驱动队列，并按租约周期发送心跳。"""

        next_heartbeat = time.monotonic() + 1.0
        try:
            while not self._stop_requested.is_set():
                bridge = self._bridge
                if bridge is None:
                    return
                now = time.monotonic()
                if now >= next_heartbeat:
                    bridge.heartbeat()  # type: ignore[attr-defined]
                    status = bridge.status()  # type: ignore[attr-defined]
                    if status.state != "running" or not status.lease_active:
                        raise RuntimeError(
                            "驱动拦截租约已失效："
                            f"状态={status.state}，租约={'有效' if status.lease_active else '无效'}"
                        )
                    next_heartbeat = now + 1.0
                event = bridge.read_event()  # type: ignore[attr-defined]
                if event is not None and self._on_event is not None:
                    self._on_event(event)
                elif event is None:
                    self._stop_requested.wait(0.01)
        except Exception as error:
            if self._on_error is not None:
                self._on_error(error)
            self._stop_requested.set()
            with self._lifecycle_lock:
                bridge = self._bridge
                self._bridge = None
                self._status = None
            if bridge is not None:
                self._stop_bridge(bridge)
        finally:
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    @staticmethod
    def _stop_bridge(bridge: object) -> None:
        """尽力停止并关闭桥接，保证启动失败和窗口退出都能释放租约。"""

        try:
            bridge.stop()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            bridge.close()  # type: ignore[attr-defined]
        except Exception:
            pass


class CaptureRawInputSession:
    """管理捕获 Raw Input 注册，支持 Mapping 切换后重新注册。"""

    def __init__(self, listener: RawInputListener) -> None:
        self._listener = listener
        self._active = False
        self._lifecycle_lock = threading.RLock()

    @property
    def is_active(self) -> bool:
        """返回当前捕获监听是否已注册。"""

        with self._lifecycle_lock:
            return self._active

    def start(self) -> None:
        """启动监听；重复调用不会重复注册。"""

        with self._lifecycle_lock:
            if self._active:
                return
            self._listener.start()
            self._active = True

    def stop(self) -> None:
        """停止监听；重复调用不会重复释放。"""

        with self._lifecycle_lock:
            if not self._active:
                return
            try:
                self._listener.stop()
            finally:
                self._active = False

def _load_remote_image(root: tk.Tk) -> tk.PhotoImage | None:
    """加载项目内的干净遥控器正面产品图。"""

    if not PRODUCT_IMAGE_PATH.exists():
        return None
    try:
        source = tk.PhotoImage(file=str(PRODUCT_IMAGE_PATH))
        # 生成素材已经只包含遥控器正面，这里仅做等比缩小。
        scaled = source.subsample(4, 4)
        # 保存引用，避免 Tk 图片被垃圾回收。
        root._t1_product_source = source  # type: ignore[attr-defined]
        root._t1_product_image = scaled  # type: ignore[attr-defined]
        return scaled
    except tk.TclError:
        return None


class CaptureTabController:
    """控制捕获页清理和 Mapping 期间的 HID 直读暂停。"""

    def __init__(
        self,
        cleanup_callback: Callable[[], None],
        mapping_state_callback: Callable[[bool], None],
    ) -> None:
        self._cleanup_callback = cleanup_callback
        self._mapping_state_callback = mapping_state_callback

    def cleanup(self) -> None:
        """停止捕获页监听器并保存当前记录。"""

        self._cleanup_callback()

    def set_mapping_active(self, active: bool) -> None:
        """通知捕获页 Mapping 状态，避免两个读取路径同时抢报告。"""

        self._mapping_state_callback(active)

    def __call__(self) -> None:
        """兼容旧的清理回调调用方式。"""

        self.cleanup()


def build_capture_tab(
    parent: tk.Misc,
    output_path: Path,
) -> CaptureTabController:
    """在现有 Tk 窗口中创建采集页，并返回生命周期控制器。"""

    # 延迟导入，避免 --help 或命令行模式强制依赖 GUI 模块。
    from tools.t1_inspector import (
        _append_capture_if_nonempty,
        _build_event,
    )

    root = parent.winfo_toplevel()
    # 采用横向工作台：左侧操作遥控器，右侧查看记录并执行保存操作。
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(2, weight=1)
    style = ttk.Style(root)
    style.configure(
        "CaptureKey.TButton",
        padding=(12, 8),
        font=("Segoe UI", 10),
        foreground="#172033",
        background="#FFFFFF",
    )
    style.configure(
        "CaptureKeySelected.TButton",
        padding=(12, 8),
        font=("Segoe UI", 10, "bold"),
        foreground="#1D4ED8",
        background="#DCE9FF",
    )
    style.configure(
        "CaptureKeyDisabled.TButton",
        padding=(12, 8),
        font=("Segoe UI", 10),
        foreground="#98A2B3",
        background="#F2F4F7",
    )

    ttk.Label(
        parent,
        text="T1 Remote 遥控区域报文采集",
        font=("Segoe UI", 16, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
    ttk.Label(
        parent,
        text="左侧选择按键后操作遥控器，右侧查看与保存报文；未选择按键时忽略输入。",
    ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

    selected_label = tk.StringVar(value="当前标签：未选择")
    status_label = tk.StringVar(value="状态：正在启动 Raw Input")
    driver_stats_label = tk.StringVar(value="驱动统计：采集模式未启用拦截")
    count_label = tk.StringVar(value="原始包：0 | 逻辑操作：0")
    current_button: str | None = None
    state_lock = threading.Lock()
    events: list[CaptureEvent] = []
    persisted_event_count = 0
    hid_listener: HidInputListener | None = None
    hid_active = False
    listener: RawInputListener | None = None
    raw_listener_session: CaptureRawInputSession | None = None
    mapping_active = False
    cleanup_requested = False
    capture_interception_var = tk.BooleanVar(value=True)
    capture_interception_button: ttk.Checkbutton | None = None
    bridge_active = False
    bridge_session = CaptureBridgeSession()

    content_frame = ttk.Frame(parent)
    content_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
    content_frame.columnconfigure(0, weight=3, minsize=700)
    content_frame.columnconfigure(1, weight=2, minsize=520)
    content_frame.rowconfigure(0, weight=1)

    visual_frame = ttk.LabelFrame(
        content_frame,
        text="遥控区域按键",
        style="Card.TLabelframe",
    )
    visual_frame.grid(row=0, column=0, sticky="nsew")
    visual_frame.columnconfigure(0, weight=1)
    visual_frame.rowconfigure(0, weight=1)
    canvas = tk.Canvas(
        visual_frame,
        background="#f4f6f8",
        highlightthickness=0,
        height=650,
    )
    canvas.grid(row=0, column=0, sticky="nsew")

    product_image = _load_remote_image(root)
    image_item = None
    if product_image:
        image_item = canvas.create_image(0, 0, image=product_image, anchor="center")
    else:
        canvas.create_text(
            450,
            280,
            text="未找到产品图片\nt1-remote-front-clean-v2.png",
            fill="#9aa3ad",
            font=("Segoe UI", 13),
        )

    # 相对图片中心的按钮位置：按钮与图片保持紧凑间距，整体留在左侧工作区。
    # 方向区仍在图片上方，左右功能键按产品图的三行按键对齐。
    button_offsets = {
        "Power": (205, -335),
        "Arrow Up": (0, -335),
        "Arrow Left": (-125, -290),
        "OK": (0, -290),
        "Arrow Right": (125, -290),
        "Arrow Down": (0, -245),
        "Return": (-185, -70),
        "Voice": (185, -70),
        "Mute": (-185, -30),
        "Home": (185, -30),
        "Air Mouse": (-185, 15),
        "Menu": (185, 15),
        "Volume Plus": (185, -150),
        "Volume Minus": (185, -115),
    }
    button_items: dict[str, int] = {}
    button_widgets: dict[str, ttk.Button] = {}

    def select_button(button: str) -> None:
        """切换当前物理按键标签。"""

        nonlocal current_button
        with state_lock:
            mapping_is_active = mapping_active
        if mapping_is_active:
            messagebox.showwarning(
                "捕获不可用",
                "Mapping 服务运行中，捕获不可用。请先停止 Mapping 服务。",
                parent=root,
            )
            return
        with state_lock:
            current_button = button
        selected_label.set(
            f"当前标签：{BUTTON_DISPLAY_NAMES.get(button, button)}"
        )
        status_label.set("状态：监听中，只按当前选择的遥控键")
        for name, widget in button_widgets.items():
            if name in DISABLED_CAPTURE_BUTTONS:
                widget.configure(style="CaptureKeyDisabled.TButton")
            else:
                widget.configure(
                    style=(
                        "CaptureKeySelected.TButton"
                        if name == button
                        else "CaptureKey.TButton"
                    )
                )
        # 桥接开启时由驱动队列提供完整报告，不能再并行读取被清零的 HID 报告。
        set_direct_hid_enabled(not bridge_active)

    def clear_button_selection() -> None:
        """清除标签并停止直读，防止未选择时继续占用 HID 报告路径。"""

        nonlocal current_button
        with state_lock:
            current_button = None
        set_direct_hid_enabled(False)
        selected_label.set("当前标签：未选择")
        status_label.set("状态：未选择按键，已忽略输入")
        for name, widget in button_widgets.items():
            widget.configure(
                style=(
                    "CaptureKeyDisabled.TButton"
                    if name in DISABLED_CAPTURE_BUTTONS
                    else "CaptureKey.TButton"
                )
            )

    for button in REMOTE_BUTTONS:
        widget = ttk.Button(
            canvas,
            text=(
                f"{BUTTON_DISPLAY_NAMES.get(button, button)}（禁用）"
                if button in DISABLED_CAPTURE_BUTTONS
                else BUTTON_DISPLAY_NAMES.get(button, button)
            ),
            command=(
                None
                if button in DISABLED_CAPTURE_BUTTONS
                else lambda selected=button: select_button(selected)
            ),
            width=18,
            style=(
                "CaptureKeyDisabled.TButton"
                if button in DISABLED_CAPTURE_BUTTONS
                else "CaptureKey.TButton"
            ),
        )
        if button in DISABLED_CAPTURE_BUTTONS:
            # Air Mouse 会切换飞鼠模式；Power 已由驱动层拦截，可安全采集。
            widget.state(["disabled"])
        button_widgets[button] = widget
        button_items[button] = canvas.create_window(
            0,
            0,
            window=widget,
            anchor="center",
        )

    def reposition_items(_event: tk.Event[tk.Misc] | None = None) -> None:
        """窗口缩放时重新计算产品图和周围按键位置。"""

        center_x = max(canvas.winfo_width() // 2, 360)
        # 图片下移，为顶部方向区留出完整空间。
        center_y = max(canvas.winfo_height() // 2 + 55, 330)
        if image_item is not None:
            canvas.coords(image_item, center_x, center_y)
        for button, item in button_items.items():
            offset_x, offset_y = button_offsets[button]
            canvas.coords(item, center_x + offset_x, center_y + offset_y)

    canvas.bind("<Configure>", reposition_items)

    operation_frame = ttk.LabelFrame(
        content_frame,
        text="记录与操作",
        style="Card.TLabelframe",
    )
    operation_frame.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
    operation_frame.columnconfigure(0, weight=1)
    operation_frame.rowconfigure(1, weight=1)

    ttk.Label(operation_frame, textvariable=selected_label).grid(
        row=0, column=0, sticky="w", padx=10, pady=(8, 4)
    )

    event_frame = ttk.LabelFrame(
        operation_frame,
        text="采集事件",
        style="Card.TLabelframe",
    )
    event_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    event_frame.columnconfigure(0, weight=1)
    event_frame.rowconfigure(0, weight=1)
    columns = ("number", "button", "state", "packets", "duration", "signature")
    tree = ttk.Treeview(event_frame, columns=columns, show="headings", height=12)
    tree.heading("number", text="#")
    tree.heading("button", text="物理按键")
    tree.heading("state", text="逻辑状态")
    tree.heading("packets", text="原始包")
    tree.heading("duration", text="按下时长")
    tree.heading("signature", text="配对签名")
    tree.column("number", width=42, anchor="center")
    tree.column("button", width=125)
    tree.column("state", width=90)
    tree.column("packets", width=58, anchor="center")
    tree.column("duration", width=88, anchor="center")
    tree.column("signature", width=230)
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(event_frame, orient="vertical", command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)

    # 保留设备状态，Mapping 设置和服务通过主窗口 Tab 直接访问。
    future_frame = ttk.LabelFrame(
        operation_frame,
        text="设备状态",
        style="Card.TLabelframe",
    )
    future_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
    future_frame.columnconfigure(0, weight=1)
    ttk.Label(
        future_frame,
        text="设备状态 · 按键映射 · 快捷操作",
        foreground="#7b8794",
    ).grid(row=0, column=0, sticky="w", padx=10, pady=10)
    ttk.Label(
        future_frame,
        textvariable=driver_stats_label,
        foreground="#52606d",
    ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
    capture_interception_button = ttk.Checkbutton(
        future_frame,
        text="拦截原生键位",
        variable=capture_interception_var,
        command=lambda: toggle_capture_interception(),
    )
    capture_interception_button.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))
    ttk.Label(
        future_frame,
        text="开启后由驱动保存完整原始报文，关闭后使用 HID 直读。",
        foreground="#7b8794",
    ).grid(row=3, column=0, sticky="w", padx=10, pady=(0, 8))

    def save_capture() -> None:
        """保存当前内存中的脱敏采集结果。"""

        nonlocal persisted_event_count
        pending_events = events[persisted_event_count:]
        try:
            saved = _append_capture_if_nonempty(output_path, pending_events)
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=root)
            return
        if not saved:
            status_label.set("状态：没有待存储的新采集记录，已有 JSON 文件未改变")
            return
        persisted_event_count = len(events)
        action_count = len(build_logical_actions(events))
        status_label.set(
            f"状态：已追加 {len(pending_events)} 个新原始包，当前会话 {action_count} 个逻辑操作"
        )

    def clear_events() -> None:
        """清空当前窗口内尚未保存的事件。"""

        nonlocal persisted_event_count
        if events and not messagebox.askyesno("确认清空", "清空当前采集记录？", parent=root):
            return
        events.clear()
        persisted_event_count = 0
        for item in tree.get_children():
            tree.delete(item)
        count_label.set("原始包：0 | 逻辑操作：0")
        status_label.set("状态：记录已清空")

    # 保存和清空是采集页的核心操作，单独占一行，避免输出路径过长时被挤出窗口。
    action_bar = ttk.Frame(operation_frame)
    action_bar.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 8))
    ttk.Button(action_bar, text="保存", command=save_capture).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(action_bar, text="清空记录", command=clear_events).pack(side="left")
    ttk.Button(action_bar, text="取消选择", command=clear_button_selection).pack(
        side="left", padx=(6, 0)
    )

    footer = ttk.Frame(operation_frame)
    footer.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 10))
    footer.columnconfigure(2, weight=1)
    ttk.Label(footer, textvariable=status_label).grid(row=0, column=0, sticky="w")
    ttk.Label(footer, textvariable=count_label).grid(row=0, column=1, padx=12)
    ttk.Label(footer, text=f"输出：{output_path}").grid(
        row=0, column=2, sticky="w", padx=12
    )

    def refresh_action_table() -> None:
        """把原始包配对后的逻辑操作显示在窗口中。"""

        state_names = {
            "pressed": "已按下",
            "press_release": "按下+抬起",
            "release_only": "单独抬起",
            "unknown": "未知",
        }
        actions = build_logical_actions(events)
        for item in tree.get_children():
            tree.delete(item)
        for action in actions:
            duration = (
                f"{action.duration_ms} ms"
                if action.duration_ms is not None
                else "等待抬起"
            )
            tree.insert(
                "",
                "end",
                values=(
                    action.action_id,
                    action.button,
                    state_names.get(action.state, action.state),
                    action.packet_count,
                    duration,
                    action.signature,
                ),
            )
        if actions:
            tree.yview_moveto(1)
        count_label.set(
            f"原始包：{len(events)} | 逻辑操作：{len(actions)}"
        )

    def handle_raw_event(raw_event: RawInputEvent, button: str) -> None:
        """在 UI 线程中更新表格，避免跨线程操作 Tkinter 控件。"""

        event = _build_event(raw_event, button)
        events.append(event)
        refresh_action_table()
        status_label.set("状态：监听中")

    def on_raw_event(
        raw_event: RawInputEvent,
        from_direct_hid: bool = False,
        from_bridge: bool = False,
    ) -> None:
        """在 Raw Input 线程中复制事件和当前标签，再投递到 UI 线程。"""

        if not is_t1_device_path(raw_event.device_path):
            return
        collection = collection_from_device_path(raw_event.device_path)
        with state_lock:
            button = selected_capture_button(current_button)
            direct_is_active = hid_active
            mapping_is_active = mapping_active
            bridge_is_active = bridge_active
        # 没有明确选择标签时不记录，避免后台输入污染采集结果。
        if button is None or mapping_is_active:
            return
        if (
            not from_direct_hid
            and not from_bridge
            and should_ignore_passive_t1_raw_event(
                collection,
                direct_hid_active=direct_is_active,
                bridge_active=bridge_is_active,
            )
        ):
            # 主动直读或驱动桥接已经提供完整报文，Raw Input 只会造成重复。
            return
        try:
            root.after(0, handle_raw_event, raw_event, button)
        except RuntimeError:
            # 窗口关闭后，消息线程可能仍收到最后一条输入。
            pass

    def on_error(error: Exception) -> None:
        """把底层异常显示到窗口状态栏。"""

        try:
            root.after(0, status_label.set, f"状态：Raw Input 错误：{error}")
        except RuntimeError:
            pass

    def hid_event_to_raw_event(event: HidInputEvent) -> RawInputEvent:
        """把 HID 直读报告转成采集表事件。"""

        return RawInputEvent(
            device_path=event.device_path,
            raw_input_type=2,
            raw_data=event.report,
        )

    def hid_event_loop_callback(event: HidInputEvent) -> None:
        """使用直读报告作为 COL02/COL03 的唯一采集来源。"""

        on_raw_event(hid_event_to_raw_event(event), from_direct_hid=True)

    def bridge_event_to_raw_event(event: object) -> RawInputEvent:
        """把驱动队列事件包装成可复用采集管线能识别的 HID 事件。"""

        collection = str(getattr(event, "collection", "UNKNOWN")).upper()
        if collection not in ("COL02", "COL03"):
            collection = "COL02"
        return RawInputEvent(
            device_path=(
                "\\\\?\\HID#VID_620A&PID_0407&"
                f"{collection}#T1REMOTE\\CAPTURE"
            ),
            raw_input_type=2,
            raw_data=bytes(getattr(event, "report", b"")),
        )

    def bridge_event_callback(event: object) -> None:
        """使用驱动在拦截前保存的完整报告作为采集来源。"""

        on_raw_event(bridge_event_to_raw_event(event), from_bridge=True)

    def bridge_error_callback(error: Exception) -> None:
        """把桥接线程异常投递到 Tk 主线程，不阻塞窗口退出。"""

        def update_status() -> None:
            nonlocal bridge_active
            bridge_active = False
            capture_interception_var.set(False)
            driver_stats_label.set(f"驱动统计：拦截异常：{error}")
            status_label.set(f"状态：原生键位拦截异常：{error}")
            set_direct_hid_enabled(current_button is not None)

        try:
            root.after(0, update_status)
        except RuntimeError:
            pass

    def hid_error_callback(error: Exception) -> None:
        """把 HID 直读错误显示到窗口状态栏。"""

        try:
            root.after(0, status_label.set, f"状态：HID 直读错误：{error}")
        except RuntimeError:
            pass

    def set_raw_input_enabled(enabled: bool) -> None:
        """切换捕获 Raw Input 注册，避免 Mapping 覆盖 COL01 接收窗口。"""

        if raw_listener_session is None:
            return
        if enabled:
            raw_listener_session.start()
        else:
            raw_listener_session.stop()

    def set_capture_interception_enabled(enabled: bool) -> None:
        """切换驱动拦截；关闭时保留 HID 直读采集能力。"""

        nonlocal bridge_active
        with state_lock:
            if mapping_active:
                capture_interception_var.set(True)
                return
        if enabled:
            if bridge_active:
                return
            try:
                bridge_status = bridge_session.start(
                    bridge_event_callback,
                    bridge_error_callback,
                )
            except Exception as error:
                bridge_active = False
                capture_interception_var.set(False)
                driver_stats_label.set(f"驱动统计：拦截不可用：{error}")
                status_label.set(f"状态：Raw Input 已启动，拦截不可用：{error}")
                return
            bridge_active = True
            # 如果用户是在直读模式下打开开关，先关闭旧读路径，避免重复和清零抬起。
            set_direct_hid_enabled(False)
            attached = bridge_status.attached_collections
            attached_text = ",".join(
                f"COL{collection:02d}"
                for collection in range(1, 32)
                if attached & (1 << collection)
            ) or "无"
            driver_stats_label.set(
                f"驱动统计：拦截已确认 | 租约有效 | 附着 {attached_text}"
            )
            status_label.set("状态：原生键位拦截已确认，未选择按键时忽略输入")
            return
        bridge_session.stop()
        bridge_active = False
        set_direct_hid_enabled(current_button is not None)
        driver_stats_label.set("驱动统计：采集模式未启用拦截")
        status_label.set("状态：Raw Input 已启动，未启用原生键位拦截")

    def toggle_capture_interception() -> None:
        """响应捕获页的原生键位拦截开关。"""

        with state_lock:
            if mapping_active:
                capture_interception_var.set(True)
                status_label.set("状态：Mapping 服务运行中，捕获不可用")
                return
        set_capture_interception_enabled(bool(capture_interception_var.get()))

    def set_direct_hid_enabled(enabled: bool) -> None:
        """按选择状态和 Mapping 状态启停 COL02/COL03 直读。"""

        nonlocal hid_listener, hid_active
        with state_lock:
            should_start = (
                enabled
                and current_button is not None
                and not mapping_active
                and not cleanup_requested
                and not bridge_active
                and not hid_active
            )
            listener_to_stop = hid_listener if not enabled else None
            if not enabled:
                hid_listener = None
                hid_active = False
        if listener_to_stop is not None:
            try:
                listener_to_stop.stop()
            except Exception as error:
                hid_error_callback(error)
        if not should_start:
            return

        candidate = HidInputListener(
            on_event=hid_event_loop_callback,
            on_error=hid_error_callback,
            target_collections=("COL02", "COL03"),
        )
        try:
            candidate.start()
        except Exception as error:
            hid_error_callback(error)
            return
        with state_lock:
            can_keep_running = (
                current_button is not None
                and not mapping_active
                and not cleanup_requested
                and not bridge_active
                and not hid_active
            )
            if can_keep_running:
                hid_listener = candidate
                hid_active = True
        if not can_keep_running:
            try:
                candidate.stop()
            except Exception as error:
                hid_error_callback(error)

    def set_mapping_active(active: bool) -> None:
        """Mapping 启动时暂停捕获，停止后按开关恢复捕获。"""

        if threading.current_thread() is not threading.main_thread():
            try:
                root.after(0, set_mapping_active, active)
            except RuntimeError:
                pass
            return
        nonlocal mapping_active, bridge_active
        with state_lock:
            mapping_active = active
        if active:
            # 同一进程只能为同一类 Raw Input 保留一个接收窗口；Mapping
            # 启动前释放捕获注册，避免 COL01/Menu 被 Mapping 隐藏窗口接走。
            set_raw_input_enabled(False)
            bridge_session.stop()
            bridge_active = False
            set_direct_hid_enabled(False)
            if capture_interception_button is not None:
                capture_interception_button.state(["disabled"])
            driver_stats_label.set("驱动统计：Mapping 服务运行中，捕获不可用")
            status_label.set("状态：Mapping 服务运行中，捕获不可用")
        else:
            with state_lock:
                should_restore_capture = not cleanup_requested
            if should_restore_capture:
                try:
                    # Mapping 停止后重新注册，恢复 COL01/Menu 的 Raw Input 路径。
                    set_raw_input_enabled(True)
                except Exception as error:
                    on_error(error)
            if capture_interception_button is not None:
                capture_interception_button.state(["!disabled"])
            set_capture_interception_enabled(bool(capture_interception_var.get()))
            set_direct_hid_enabled(current_button is not None)
            if current_button is None:
                status_label.set("状态：未选择按键，已忽略输入")
            elif not bridge_active:
                status_label.set("状态：HID 直读采集已恢复")

    listener = RawInputListener(on_event=on_raw_event, on_error=on_error)
    raw_listener_session = CaptureRawInputSession(listener)
    try:
        set_raw_input_enabled(True)
    except Exception as error:
        messagebox.showerror("Inspector 启动失败", str(error), parent=root)
        raise RuntimeError("Inspector 启动失败") from error

    # 默认开启驱动拦截，确保捕获到驱动清零前的完整原始报告。
    set_capture_interception_enabled(True)

    def cleanup() -> None:
        """停止 Raw Input 线程并保存当前结果。"""

        nonlocal cleanup_requested
        with state_lock:
            cleanup_requested = True
        set_direct_hid_enabled(False)
        set_raw_input_enabled(False)
        bridge_session.stop()
        save_capture()

    return CaptureTabController(cleanup, set_mapping_active)


def run_gui(output_path: Path) -> int:
    """运行以产品图为中心的 Tkinter 遥控区域采集窗口。"""

    root = tk.Tk()
    root.title("T1 Remote 遥控区域报文 Inspector")
    root.geometry("1520x860")
    root.minsize(1280, 760)
    try:
        capture_controller = build_capture_tab(root, output_path)
    except Exception:
        root.destroy()
        raise

    def on_close() -> None:
        """停止采集并关闭独立窗口。"""

        capture_controller.cleanup()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui(Path("captures") / "t1-remote-control.json"))
