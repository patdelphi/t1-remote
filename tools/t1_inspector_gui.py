"""程序说明：以 T1 遥控器正面产品图为中心，提供 14 键报文采集界面。"""

from __future__ import annotations

from pathlib import Path
import threading
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
)
from t1remote.windows.hid_input import HidInputEvent, HidInputListener
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


def build_capture_tab(
    parent: tk.Misc,
    output_path: Path,
) -> Callable[[], None]:
    """在现有 Tk 窗口中创建采集页，并返回清理回调。"""

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
        text="左侧按产品图操作遥控器，右侧查看与保存报文；T1 输入报文全部记录，当前标签仅用于标注。",
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

    def on_raw_event(raw_event: RawInputEvent, from_direct_hid: bool = False) -> None:
        """在 Raw Input 线程中复制事件和当前标签，再投递到 UI 线程。"""

        # 不按 Usage、按键类型或当前标签丢弃 T1 报文；未选标签的报文标记为“未标记”。
        if not is_t1_device_path(raw_event.device_path):
            return
        collection = collection_from_device_path(raw_event.device_path)
        with state_lock:
            button = current_button or "未标记"
            direct_is_active = hid_active
        if not from_direct_hid and collection in ("COL02", "COL03"):
            # 直读监听器已经提供完整报文时，Raw Input 只会造成重复。
            if direct_is_active:
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

    def hid_error_callback(error: Exception) -> None:
        """把 HID 直读错误显示到窗口状态栏。"""

        try:
            root.after(0, status_label.set, f"状态：HID 直读错误：{error}")
        except RuntimeError:
            pass

    listener = RawInputListener(on_event=on_raw_event, on_error=on_error)
    try:
        listener.start()
    except Exception as error:
        messagebox.showerror("Inspector 启动失败", str(error), parent=root)
        raise RuntimeError("Inspector 启动失败") from error

    # Inspector 是采集工具，不在采集阶段启用桥接拦截，避免改变 Home、Power 等按键行为。
    driver_stats_label.set("驱动统计：采集模式未启用拦截")
    status_label.set("状态：准备启动 HID 直读采集")

    try:
        hid_listener = HidInputListener(
            on_event=hid_event_loop_callback,
            on_error=hid_error_callback,
            target_collections=("COL02", "COL03"),
        )
        hid_listener.start()
        with state_lock:
            hid_active = True
        status_label.set(
            "状态：HID 直读采集已启动，输入保持正常透传；请点击按键标签后操作遥控器"
        )
    except Exception as error:
        hid_listener = None
        hid_active = False
        hid_error_callback(error)

    def cleanup() -> None:
        """停止 Raw Input 线程并保存当前结果。"""

        nonlocal hid_active
        with state_lock:
            hid_active = False
        if hid_listener:
            try:
                hid_listener.stop()
            except Exception as error:
                hid_error_callback(error)
        listener.stop()
        save_capture()

    return cleanup


def run_gui(output_path: Path) -> int:
    """运行以产品图为中心的 Tkinter 遥控区域采集窗口。"""

    root = tk.Tk()
    root.title("T1 Remote 遥控区域报文 Inspector")
    root.geometry("1520x860")
    root.minsize(1280, 760)
    try:
        cleanup = build_capture_tab(root, output_path)
    except Exception:
        root.destroy()
        raise

    def on_close() -> None:
        """停止采集并关闭独立窗口。"""

        cleanup()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui(Path("captures") / "t1-remote-control.json"))
