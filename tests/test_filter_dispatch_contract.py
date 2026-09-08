"""
程序说明：验证 T1 HID 过滤驱动把输入报告请求路由到正确的 WDF 队列。

IOCTL_HID_READ_REPORT 的主 IRP 类型是 IRP_MJ_DEVICE_CONTROL；这个回归测试
防止以后只保留 InternalDeviceControl 路径，导致驱动挂载但收不到 HID 报告。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILTER_SOURCE = ROOT / "native" / "t1filter" / "t1filter.c"
FILTER_HEADER = ROOT / "native" / "t1filter" / "t1filter.h"
FILTER_INF = ROOT / "native" / "t1filter" / "t1filter.inf"


def test_hid_report_has_device_control_dispatch_path() -> None:
    """HID 读报告必须通过普通 DeviceControl 队列进入过滤器。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL T1FilterEvtHidDeviceControl;" in header
    assert "queue_config.EvtIoDeviceControl = T1FilterEvtHidDeviceControl;" in source
    assert "WdfRequestTypeDeviceControlInternal" in source
    assert "IOCTL_HID_READ_REPORT" in source


def test_hid_report_completion_reads_wdf_ioctl_output_memory() -> None:
    """METHOD_NEITHER 报告完成时必须优先读取 WDF IOCTL 输出内存。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "Params->Parameters.Ioctl.Output.Buffer" in source
    assert "WdfMemoryGetBuffer" in source


def test_umdf_input_report_is_filtered_with_hid_read_report() -> None:
    """UMDF HID 转发的输入报告也必须进入同一完成处理逻辑。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "IOCTL_UMDF_HID_GET_INPUT_REPORT" in source
    assert "IOCTL_HID_GET_INPUT_REPORT" in source


def test_system_control_collection_is_attached_and_decoded() -> None:
    """Power 所在的 COL03 必须绑定过滤器，并按单字节 System Control 解析。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")
    inf = FILTER_INF.read_text(encoding="utf-8")

    assert "&Col03" in inf
    assert "LowerFilters" in inf
    assert "usage_page" in header
    assert "collection == 3" in source
    assert "Report[1]" in source
    assert "event.usage_page = UsagePage" in source


def test_hid_read_completion_completes_the_forwarded_request() -> None:
    """完成回调必须把下层请求状态继续回传给 HID 栈。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "WdfRequestCompleteWithInformation(Request" in source


def test_default_hid_queue_is_not_configured_twice() -> None:
    """默认队列已经绑定普通 DeviceControl，不能再次配置同一请求类型。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    device_add = source[source.index("T1FilterEvtDeviceAdd("):]
    second_queue = device_add.index("WDF_IO_QUEUE_CONFIG_INIT(\n")
    default_queue_section = device_add[:second_queue]

    assert "queue_config.EvtIoDeviceControl = T1FilterEvtHidDeviceControl;" in default_queue_section
    assert "WdfDeviceConfigureRequestDispatching" not in default_queue_section
