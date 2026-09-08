"""程序说明：以 T1 遥控器正面产品图为中心，提供 14 键报文采集界面。"""

from __future__ import annotations

from pathlib import Path
import threading

import tkinter as tk
from tkinter import messagebox, ttk

from t1remote.core.capture_scope import (
    DISABLED_CAPTURE_BUTTONS,
    REMOTE_BUTTONS,
    CaptureEvent,
    build_logical_actions,
    is_t1_device_path,
)
from t1remote.windows.driver_bridge import (
    BridgeError,
    BridgeUnavailable,
    DriverInputEvent,
    HidUsage,
    InterceptionPolicy,
    T1BridgeClient,
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

DRIVER_BLOCKED_USAGES = (
    HidUsage(0x0C, 0x223, "COL02"),  # Home
    HidUsage(0x0C, 0x221, "COL02"),  # Voice
    HidUsage(0x0C, 0x0E2, "COL02"),  # Mute
    HidUsage(0x0C, 0x0E9, "COL02"),  # Volume Plus
    HidUsage(0x0C, 0x0EA, "COL02"),  # Volume Minus
    HidUsage(0x0C, 0x224, "COL02"),  # Return
    HidUsage(0x01, 0x081, "COL03"),  # Power / System Power Down
)


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


def run_gui(output_path: Path) -> int:
    """运行以产品图为中心的 Tkinter 遥控区域采集窗口。"""

    # 延迟导入，避免 --help 或命令行模式强制依赖 GUI 模块。
    from tools.t1_inspector import (
        _build_event,
        _write_capture_if_nonempty,
    )

    root = tk.Tk()
    root.title("T1 Remote 遥控区域报文 Inspector")
    # 采用横向工作台：左侧操作遥控器，右侧查看记录并执行保存操作。
    root.geometry("1520x860")
    root.minsize(1280, 760)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(2, weight=1)

    ttk.Label(
        root,
        text="T1 Remote 遥控区域报文采集",
        font=("Segoe UI", 16, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
    ttk.Label(
        root,
        text="左侧按产品图操作遥控器，右侧查看与保存报文；键盘面、空中鼠标移动、Power 和 Air Mouse 不写入夹具。",
    ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

    selected_label = tk.StringVar(value="当前标签：未选择")
    status_label = tk.StringVar(value="状态：正在启动 Raw Input")
    count_label = tk.StringVar(value="原始包：0 | 逻辑操作：0")
    current_button: str | None = None
    state_lock = threading.Lock()
    events: list[CaptureEvent] = []
    driver_client: T1BridgeClient | None = None
    driver_stop = threading.Event()
    driver_thread: threading.Thread | None = None

    content_frame = ttk.Frame(root)
    content_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
    content_frame.columnconfigure(0, weight=3, minsize=700)
    content_frame.columnconfigure(1, weight=2, minsize=520)
    content_frame.rowconfigure(0, weight=1)

    visual_frame = ttk.LabelFrame(content_frame, text="遥控区域按键")
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
            current_button = button
        selected_label.set(
            f"当前标签：{BUTTON_DISPLAY_NAMES.get(button, button)}"
        )
        status_label.set("状态：监听中，只按当前选择的遥控键")
        for name, widget in button_widgets.items():
            if name in DISABLED_CAPTURE_BUTTONS:
                widget.configure(text=f"{name}（禁用）")
            else:
                display_name = BUTTON_DISPLAY_NAMES.get(name, name)
                widget.configure(
                    text=(f"✓ {display_name}" if name == button else display_name)
                )

    for button in REMOTE_BUTTONS:
        widget = ttk.Button(
            canvas,
            text=(
                f"{button}（禁用）"
                if button in DISABLED_CAPTURE_BUTTONS
                else BUTTON_DISPLAY_NAMES.get(button, button)
            ),
            command=(
                None
                if button in DISABLED_CAPTURE_BUTTONS
                else lambda selected=button: select_button(selected)
            ),
            width=17,
        )
        if button in DISABLED_CAPTURE_BUTTONS:
            # Power 可能被 Windows 当作系统电源键处理；Air Mouse 会切换飞鼠模式。
            widget.state(["disabled"])
        button_widgets[button] = widget
        button_items[button] = canvas.create_window(0, 0, window=widget)

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

    operation_frame = ttk.LabelFrame(content_frame, text="记录与操作")
    operation_frame.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
    operation_frame.columnconfigure(0, weight=1)
    operation_frame.rowconfigure(1, weight=1)

    ttk.Label(operation_frame, textvariable=selected_label).grid(
        row=0, column=0, sticky="w", padx=10, pady=(8, 4)
    )

    event_frame = ttk.LabelFrame(operation_frame, text="采集事件")
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

    # 预留后续组件位置，避免以后新增设备状态或映射配置时再次改变主布局。
    future_frame = ttk.LabelFrame(operation_frame, text="后续组件预留")
    future_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
    future_frame.columnconfigure(0, weight=1)
    ttk.Label(
        future_frame,
        text="设备状态 · 按键映射 · 快捷操作",
        foreground="#7b8794",
    ).grid(row=0, column=0, sticky="w", padx=10, pady=10)

    footer = ttk.Frame(operation_frame)
    footer.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 10))
    footer.columnconfigure(0, weight=1)
    ttk.Label(footer, textvariable=status_label).grid(row=0, column=0, sticky="w")
    ttk.Label(footer, textvariable=count_label).grid(row=0, column=1, padx=12)
    ttk.Label(footer, text=f"输出：{output_path}").grid(row=0, column=2, padx=12)

    def save_capture() -> None:
        """保存当前内存中的脱敏采集结果。"""

        try:
            saved = _write_capture_if_nonempty(output_path, events)
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=root)
            return
        if not saved:
            status_label.set("状态：没有新采集记录，未覆盖已有 JSON 文件")
            return
        action_count = len(build_logical_actions(events))
        status_label.set(f"状态：已保存 {len(events)} 个原始包，{action_count} 个逻辑操作")

    def clear_events() -> None:
        """清空当前窗口内尚未保存的事件。"""

        if events and not messagebox.askyesno("确认清空", "清空当前采集记录？", parent=root):
            return
        events.clear()
        for item in tree.get_children():
            tree.delete(item)
        count_label.set("原始包：0 | 逻辑操作：0")
        status_label.set("状态：记录已清空")

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

    def on_raw_event(raw_event: RawInputEvent) -> None:
        """在 Raw Input 线程中复制事件和当前标签，再投递到 UI 线程。"""

        # 空中鼠标移动会产生大量 Mouse Report，本轮按键夹具不记录它。
        if raw_event.raw_input_type == 0 or not is_t1_device_path(raw_event.device_path):
            return
        with state_lock:
            button = current_button
        if button is None:
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

    def driver_event_to_raw_event(event: DriverInputEvent) -> RawInputEvent:
        """把驱动事件转成采集表复用的 Raw Input 事件模型。"""

        return RawInputEvent(
            device_path=f"HID\\VID_620A&PID_0407&{event.collection}",
            raw_input_type=2,
            raw_data=event.report,
        )

    def driver_event_loop(client: T1BridgeClient) -> None:
        """轮询驱动事件队列，并把原始报文投递到 Tk 线程。"""

        while not driver_stop.is_set():
            try:
                event = client.read_event()
            except BridgeError as error:
                try:
                    root.after(0, on_error, error)
                except RuntimeError:
                    pass
                return
            if event is None:
                driver_stop.wait(0.02)
                continue
            on_raw_event(driver_event_to_raw_event(event))

    listener = RawInputListener(on_event=on_raw_event, on_error=on_error)
    try:
        listener.start()
    except Exception as error:
        messagebox.showerror("Inspector 启动失败", str(error), parent=root)
        root.destroy()
        return 1

    try:
        driver_client = T1BridgeClient()
        driver_client.open(
            InterceptionPolicy(
                blocked_usages=DRIVER_BLOCKED_USAGES,
                target_collections=("COL02", "COL03"),
            )
        )
        driver_client.start()
        driver_thread = threading.Thread(
            target=driver_event_loop,
            args=(driver_client,),
            name="t1-driver-events",
            daemon=True,
        )
        driver_thread.start()
        status_label.set("状态：驱动拦截已启动，请点击按键标签后操作遥控器")
    except BridgeUnavailable:
        # 未安装驱动时继续保留 Raw Input 采集模式，方便开发机采集报文。
        driver_client = None
        status_label.set("状态：Raw Input 监听中；驱动未安装")
    except BridgeError as error:
        if driver_client:
            driver_client.close()
        driver_client = None
        status_label.set(f"状态：驱动未启动：{error}")

    ttk.Button(footer, text="保存", command=save_capture).grid(row=0, column=3, padx=4)
    ttk.Button(footer, text="清空记录", command=clear_events).grid(row=0, column=4, padx=4)

    def on_close() -> None:
        """停止 Raw Input 线程并保存当前结果。"""

        driver_stop.set()
        if driver_thread:
            driver_thread.join(timeout=1)
        if driver_client:
            try:
                driver_client.stop()
            except BridgeError:
                pass
            driver_client.close()
        listener.stop()
        try:
            _write_capture_if_nonempty(output_path, events)
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=root)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui(Path("captures") / "t1-remote-control.json"))
