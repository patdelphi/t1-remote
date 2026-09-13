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
BRIDGE_SOURCE = ROOT / "native" / "t1bridge" / "t1bridge.c"
BRIDGE_PROTOCOL = ROOT / "native" / "t1bridge" / "t1bridge_protocol.h"


def _ioctl_definition(source: str, name: str) -> str:
    """返回一个 IOCTL 宏的完整定义文本，用于核对访问权限。"""

    start = source.index(f"#define IOCTL_T1FILTER_{name} \\")
    return source[start:source.index("\n", source.index("CTL_CODE(", start))]


def test_get_report_rejects_user_mode_before_packet_dereference() -> None:
    """完成回调不可解引用未经锁定的用户包；入口拒绝该未支持路径。"""
    source = FILTER_SOURCE.read_text(encoding="utf-8-sig")
    helper = source[source.index("T1FilterGetInputReportBuffer("):source.index("VOID\nT1FilterEvtGetInputReportCompletion(")]
    assert helper.index("irp->RequestorMode != KernelMode") < helper.index("packet->reportBuffer")
    forward = source[source.index("T1FilterForwardHidRequest("):source.index("VOID\nT1FilterEvtHidDeviceControl(")]
    assert "WdfRequestGetRequestorMode(Request) != KernelMode" in forward
    assert "STATUS_NOT_SUPPORTED" in forward


def test_collection_property_memory_belongs_to_device() -> None:
    """查询属性内存必须随设备释放，不能遗留到驱动卸载。"""
    source = FILTER_SOURCE.read_text(encoding="utf-8-sig")
    helper = source[source.index("T1FilterDetectCollection("):source.index("VOID\nT1FilterEvtDeviceCleanup(")]
    assert "attributes.ParentObject = Device;" in helper
    assert "&attributes," in helper


def test_hid_report_has_device_control_dispatch_path() -> None:
    """HID 读报告必须通过普通 DeviceControl 队列进入过滤器。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL T1FilterEvtHidDeviceControl;" in header
    assert "queue_config.EvtIoDeviceControl = T1FilterEvtHidDeviceControl;" in source
    assert "WdfRequestTypeDeviceControlInternal" in source
    assert "IOCTL_HID_READ_REPORT" in source


def test_ble_hid_read_requests_have_a_read_dispatch_path() -> None:
    """BLE HID/UMDF 的持续输入报告必须通过 Read 队列进入过滤器。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "EVT_WDF_IO_QUEUE_IO_READ T1FilterEvtHidRead;" in header
    assert "EVT_WDF_IO_QUEUE_IO_STOP T1FilterEvtIoStop;" in header
    assert "queue_config.EvtIoRead = T1FilterEvtHidRead;" in source
    assert "queue_config.EvtIoStop = T1FilterEvtIoStop;" in source
    assert "T1FilterEvtHidRead(" in source
    assert "T1FilterEvtReadCompletion" in source
    assert "WdfRequestCancelSentRequest(Request)" in source


def test_hid_report_completion_reads_wdf_ioctl_output_memory() -> None:
    """使用 CurrentType 转发时，完成回调只能从请求对象取输出缓冲区。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    completion = source[
        source.index("T1FilterEvtReadCompletion(") : source.index(
            "T1FilterForwardHidRequest("
        )
    ]

    assert "WdfRequestRetrieveOutputBuffer(" in completion
    assert "Params->Type" not in completion
    assert "Params->Parameters" not in completion


def test_get_input_report_requests_use_a_dedicated_completion_path() -> None:
    """GET_INPUT_REPORT 使用 HID_XFER_PACKET 专用完成回调并可拦截报告。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    forward_start = source.index("T1FilterForwardHidRequest(")
    forward_end = source.index("VOID\nT1FilterEvtHidDeviceControl", forward_start)
    forward = source[forward_start:forward_end]

    assert "IoControlCode == IOCTL_HID_READ_REPORT" in forward
    assert "IOCTL_HID_GET_INPUT_REPORT" in forward
    assert "IOCTL_UMDF_HID_GET_INPUT_REPORT" in forward
    assert "T1FilterEvtGetInputReportCompletion" in forward
    assert "EVT_WDF_REQUEST_COMPLETION_ROUTINE T1FilterEvtGetInputReportCompletion;" in FILTER_HEADER.read_text(encoding="utf-8")
    assert "WdfRequestWdmGetIrp" in source
    assert "irp->UserBuffer" in source
    assert "packet->reportBuffer" in source
    assert "packet->reportBufferLen" in source


def test_blocked_reports_preserve_hid_report_id_and_clear_payload() -> None:
    """清零被拦截报告时必须保留 Report ID，避免 HID 栈重新解释报文。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "T1FilterClearReportPayload" in source
    helper_start = source.index("T1FilterClearReportPayload(")
    helper_end = source.index("static BOOLEAN\nT1FilterGetInputReportBuffer", helper_start)
    helper = source[helper_start:helper_end]
    assert "Report + 1" in helper
    assert "ReportLength - 1" in helper
    assert "T1FilterClearReportPayload(report, report_length);" in source
    assert "T1FilterClearReportPayload((UCHAR*)report, bytes_returned);" in source


def test_system_control_collection_is_attached_and_decoded() -> None:
    """Power 所在的 COL03 必须绑定过滤器，并按单字节 System Control 解析。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")
    inf = FILTER_INF.read_text(encoding="utf-8")

    assert "&Col03" in inf
    assert "[T1RemoteFilter_Install.NT.Filters]" in inf
    assert "AddFilter = T1RemoteFilter, 0, T1RemoteFilter_Filter" in inf
    assert "FilterPosition = Lower" in inf
    assert "usage_page" in header
    assert "case 3:" in source
    assert "context->usage_page = 0x0001;" in source
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


def test_hid_queues_use_parallel_dispatch_for_continuous_input() -> None:
    """持续挂起的 HID Read 不能阻塞同一设备的其他 HID 请求。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    device_add = source[source.index("T1FilterEvtDeviceAdd("):]
    first_queue = device_add[:device_add.index("WDF_IO_QUEUE_CONFIG_INIT(\n")]
    second_queue = device_add[device_add.index("WDF_IO_QUEUE_CONFIG_INIT(\n"):]
    second_queue = second_queue[:second_queue.index("status = WdfIoQueueCreate")]

    assert "WdfIoQueueDispatchParallel" in first_queue
    assert "WdfIoQueueDispatchParallel" in second_queue


def test_control_queue_runs_at_passive_level_for_sync_hid_queries() -> None:
    """同步向下层 HID 发送的查询必须由 PASSIVE_LEVEL 队列回调执行。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    create_control = source[
        source.index("T1FilterCreateControlDevice(") : source.index(
            "static BOOLEAN\nT1FilterCharEqualsInsensitive"
        )
    ]

    assert "queue_attributes.ExecutionLevel = WdfExecutionLevelPassive;" in create_control


def test_collection_target_is_referenced_during_sync_query() -> None:
    """描述符查询期间必须保持下层 WDF I/O target 存活。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "WdfObjectReference(target);" in source
    assert "WdfObjectDereference(target);" in source


def test_internal_hid_queue_has_stop_callback_for_forwarded_reads() -> None:
    """内部 HID 读请求队列在移除时必须取消已转发的请求。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    device_add = source[source.index("T1FilterEvtDeviceAdd("):]
    second_queue_start = device_add.index("WDF_IO_QUEUE_CONFIG_INIT(\n")
    second_queue = device_add[second_queue_start:]
    second_queue = second_queue[:second_queue.index("status = WdfIoQueueCreate")]

    assert "queue_config.EvtIoInternalDeviceControl" in second_queue
    assert "queue_config.EvtIoStop = T1FilterEvtIoStop;" in second_queue


def test_forwarded_requests_are_tracked_before_cancel_and_released_on_completion() -> None:
    """EvtIoStop 取消转发请求时必须保护请求句柄的生命周期。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "WDFCOLLECTION sent_requests;" in header
    assert "WDFSPINLOCK sent_requests_lock;" in header
    assert "WdfCollectionAdd" in source
    assert "WdfCollectionRemoveItem" in source
    assert "WdfObjectReference(Request)" in source
    assert "WdfObjectDereference(Request)" in source


def test_forwarded_request_stop_has_resume_callback() -> None:
    """StopAcknowledge(FALSE) 的队列必须注册对应的 EvtIoResume。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "EVT_WDF_IO_QUEUE_IO_RESUME T1FilterEvtIoResume;" in header
    assert "VOID\nT1FilterEvtIoResume(" in source
    assert source.count("queue_config.EvtIoResume = T1FilterEvtIoResume;") >= 2


def test_driver_has_single_session_lease_and_safe_timeout() -> None:
    """应用崩溃后驱动必须停止吞键，并提供心跳刷新接口。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    protocol = (ROOT / "native" / "t1bridge" / "t1bridge_protocol.h").read_text(
        encoding="utf-8"
    )

    assert "IOCTL_T1FILTER_HEARTBEAT" in protocol
    assert "lease_timeout_ms" in protocol
    assert "T1BRIDGE_FLAG_LEASE_REQUIRED" in protocol
    assert "KeQueryInterruptTime" in source
    assert "lease_expirations" in source


def test_driver_tracks_collection_lifecycle_and_request_paths() -> None:
    """设备重连和普通/内部请求路径必须可诊断。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "EVT_WDF_OBJECT_CONTEXT_CLEANUP" in header
    assert "EvtCleanupCallback" in source
    assert "attached_collections" in source
    assert "device_control_reports" in source
    assert "internal_device_control_reports" in source


def test_driver_keeps_active_report_state_isolated_per_collection() -> None:
    """COL03 的零报告不能清除 COL02 Voice 的活动状态。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "T1FILTER_MAX_COLLECTIONS" in header
    assert "active_collections" in header
    assert "Collection < T1FILTER_MAX_COLLECTIONS" in source
    assert "active_collections[Collection]" in source


def test_driver_supports_descriptor_field_rules() -> None:
    """Report Descriptor 编译出的字段规则必须有固定 ABI 和边界校验。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    protocol = (ROOT / "native" / "t1bridge" / "t1bridge_protocol.h").read_text(
        encoding="utf-8"
    )

    assert "T1BRIDGE_FIELD_RULE" in protocol
    assert "field_rule_count" in protocol
    assert "T1FilterValidFieldRule" in source
    assert "byte_offset" in source


def test_policy_validation_rejects_unknown_flags_and_invalid_collections() -> None:
    """内核策略校验必须拒绝无法由固定状态表表示的值。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    validation = source[
        source.index("T1FilterValidPolicy(") : source.index(
            "BOOLEAN\nT1FilterShouldBlockReport"
        )
    ]

    assert "T1BRIDGE_FLAG_ENABLED |" in validation
    assert "target_collections[index] == 0" in validation
    assert "target_collections[index] >= T1FILTER_MAX_COLLECTIONS" in validation
    assert "usages[index].collection >= T1FILTER_MAX_COLLECTIONS" in validation


def test_policy_update_applies_enabled_flag_and_clears_active_reports() -> None:
    """SetPolicy 按 FLAG_ENABLED 决定是否拦截，并且不能让旧策略的按下状态泄漏。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    update = source[
        source.index("case IOCTL_T1FILTER_SET_POLICY:") : source.index(
            "case IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR:"
        )
    ]

    assert "context->filtering_enabled =" in update
    assert "T1BRIDGE_FLAG_ENABLED) != 0;" in update
    assert "RtlZeroMemory(" in update
    assert "context->active_collections" in update


def test_stop_and_owner_close_keep_resident_filtering() -> None:
    """常驻拦截：STOP 与所有者关闭都不得关闭过滤，只有策略能关。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    stop_case = source[
        source.index("case IOCTL_T1FILTER_STOP:") : source.index(
            "case IOCTL_T1FILTER_HEARTBEAT:"
        )
    ]
    close_callback = source[
        source.index("T1FilterEvtFileClose(") : source.index(
            "VOID\nT1FilterEvtDeviceControl("
        )
    ]

    assert "filtering_enabled = FALSE" not in stop_case
    assert "filtering_enabled = FALSE" not in close_callback


def test_report_rewrite_is_guarded_by_policy_generation() -> None:
    """并发热更新策略时，报文改写必须与解码使用同一代策略。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "PolicyGeneration" in source
    assert "Context->policy_generation != ExpectedGeneration" in source


def test_field_rule_remap_and_drop_flags_are_mutually_exclusive() -> None:
    """字段规则不能同时要求改写和丢弃。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "T1BRIDGE_FIELD_RULE_FLAG_REMAP" in source
    assert "T1BRIDGE_FIELD_RULE_FLAG_DROP" in source
    assert "Rule->flags & T1BRIDGE_FIELD_RULE_FLAG_REMAP" in source


def test_driver_exposes_report_descriptor_from_lower_hid_target() -> None:
    """原始 Report Descriptor 必须由过滤器向下层 HID minidriver 请求。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")
    protocol = (ROOT / "native" / "t1bridge" / "t1bridge_protocol.h").read_text(
        encoding="utf-8"
    )

    assert "IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR" in protocol
    assert "T1BRIDGE_REPORT_DESCRIPTOR" in protocol
    descriptor_start = source.index("T1FilterReadReportDescriptor")
    descriptor_end = source.index("T1FilterReadPreparsedData")
    descriptor_query = source[descriptor_start:descriptor_end]
    assert "WdfIoTargetSendIoctlSynchronously" in descriptor_query
    assert "WdfIoTargetSendInternalIoctlSynchronously" not in descriptor_query
    assert "IOCTL_HID_GET_REPORT_DESCRIPTOR" in source
    assert "collection_targets" in header


def test_driver_exposes_official_hid_preparsed_data_query() -> None:
    """HID 官方能力查询必须按 Information/Collection Descriptor 顺序执行。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    protocol = (ROOT / "native" / "t1bridge" / "t1bridge_protocol.h").read_text(
        encoding="utf-8"
    )

    assert "IOCTL_T1FILTER_GET_PREPARSED_DATA" in protocol
    assert "IOCTL_HID_GET_COLLECTION_INFORMATION" in source
    assert "IOCTL_HID_GET_COLLECTION_DESCRIPTOR" in source
    assert "HID_COLLECTION_INFORMATION" in source
    preparsed_start = source.index("T1FilterReadPreparsedData")
    preparsed_end = source.index("T1FilterCompleteReadRequest")
    preparsed_query = source[preparsed_start:preparsed_end]
    assert "WdfIoTargetSendIoctlSynchronously" in preparsed_query
    assert "WdfIoTargetSendInternalIoctlSynchronously" not in preparsed_query


def test_driver_uses_cached_hid_parser_before_fixed_report_fallback() -> None:
    """已有 preparsed data 时必须优先使用官方 parser 解码 Usage。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")
    project = (ROOT / "native" / "t1filter" / "t1filter.vcxproj").read_text(
        encoding="utf-8"
    )

    assert "#include <hidpddi.h>" in header
    assert "parser_preparsed_data" in header
    assert "HidP_GetUsagesEx" in source
    assert "HidP_UnsetUsages" in source
    assert "HidP_SetUsages" in source
    assert "T1FilterTryDecodeWithParserLocked" in source
    assert "T1FilterRewriteReportWithParserLocked" in source
    assert "T1FilterCachePreparsedData" in source
    assert "hidparse.lib" in project
    assert "固定 byte offset 只在 parser" in source


def test_driver_uses_hidp_data_and_capabilities_for_value_controls() -> None:
    """value control 必须通过 HidP_GetData 和能力表解析 DataIndex。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")

    assert "HidP_MaxDataListLength" in source
    assert "HidP_GetData" in source
    assert "HidP_GetValueCaps" in source
    assert "parser_data_maps" in header
    assert "parser_data_lists" in header
    assert "T1FilterTryDecodeDataLocked" in source
    assert "RawValue" in source
    assert "DataIndex" in source


def test_sync_hid_queries_use_a_bounded_nonzero_timeout() -> None:
    """同步 HID 查询必须遵守 KMDF SyncReqSend 的非零超时要求。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "WDF_REQUEST_SEND_OPTIONS" in source
    assert "WDF_REQUEST_SEND_OPTIONS_INIT" in source
    assert "WDF_REQUEST_SEND_OPTIONS_SET_TIMEOUT" in source
    assert source.count("&send_options") >= 3


def test_descriptor_query_failures_are_exposed_as_last_error() -> None:
    """现场查询失败时必须保留精确 NTSTATUS，不能只返回 Win32 错误码。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "T1FilterRecordControlError(context, IoControlCode, status);" in source
    assert "Context->last_error = Status;" in source
    assert "IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR" in source
    assert "IOCTL_T1FILTER_GET_PREPARSED_DATA" in source


def test_bridge_checks_structured_ioctl_output_length_and_collection_bounds() -> None:
    """桥接层不能把短输出或 COL00 当成有效的 HID 结构。"""

    source = BRIDGE_SOURCE.read_text(encoding="utf-8")

    assert "minimum_output_size" in source
    assert "bytes_returned < minimum_output_size" in source
    assert "ERROR_INSUFFICIENT_BUFFER" in source
    assert "descriptor->collection == 0" in source
    assert "preparsed_data->collection == 0" in source


def test_control_ioctls_require_read_or_write_access() -> None:
    """管理类 IOCTL 不能使用 FILE_ANY_ACCESS：查询只读、修改需要写权限。"""

    protocol = BRIDGE_PROTOCOL.read_text(encoding="utf-8-sig")
    ctl_code_lines = [line for line in protocol.splitlines() if "CTL_CODE(" in line]

    assert ctl_code_lines
    assert all("FILE_ANY_ACCESS" not in line for line in ctl_code_lines)
    for name in ("SET_POLICY", "START", "STOP", "HEARTBEAT", "FLUSH_EVENTS"):
        assert "FILE_WRITE_DATA" in _ioctl_definition(protocol, name), name
    for name in (
        "GET_STATUS",
        "READ_EVENT",
        "GET_CAPABILITIES",
        "GET_STATS",
        "GET_REPORT_DESCRIPTOR",
        "GET_PREPARSED_DATA",
    ):
        assert "FILE_READ_DATA" in _ioctl_definition(protocol, name), name


def test_control_session_owner_guards_mutating_ioctls() -> None:
    """只有所有者句柄能改策略、启停过滤和刷新租约，查询请求不受限。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    header = FILTER_HEADER.read_text(encoding="utf-8")
    owner_section = source[
        source.index("T1FilterRequiresOwner(") : source.index(
            "NTSTATUS\nT1FilterCreateControlDevice("
        )
    ]

    assert "WDFFILEOBJECT owner_file;" in header
    assert "EVT_WDF_FILE_CLOSE T1FilterEvtFileClose;" in header
    assert "WdfRequestGetFileObject(Request)" in owner_section
    assert "STATUS_ACCESS_DENIED" in owner_section
    assert owner_section.index("T1FilterRequiresOwner(IoControlCode)") < (
        owner_section.index("switch (IoControlCode)")
    )


def test_filter_attaches_to_keyboard_collection_with_correct_usage_page() -> None:
    """键盘集合必须挂过滤器，并使用 HID Usage Page 0x07 解码。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    inf = FILTER_INF.read_text(encoding="utf-8")
    device_add = source[source.index("T1FilterEvtDeviceAdd("):]

    assert "&Col01" in inf
    assert "&Col02" in inf
    assert "&Col03" in inf
    assert "case 1:" in device_add
    assert "context->usage_page = 0x0007;" in device_add
    assert "context->usage_page = 0x0001;" in device_add
    assert "context->usage_page = 0x000C;" in device_add


def test_policy_is_persisted_and_loaded_at_driver_start() -> None:
    """常驻拦截：策略必须写入注册表，并在驱动启动时加载。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")

    assert "T1FilterPersistPolicy(" in source
    assert "T1FilterLoadPersistedPolicy(" in source
    assert "ZwSetValueKey(" in source
    assert "ZwQueryValueKey(" in source
    assert "Services\\\\T1RemoteFilter\\\\Parameters" in source
    # SET_POLICY 分支必须落盘
    set_policy = source[source.index("case IOCTL_T1FILTER_SET_POLICY:"):source.index("case IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR:")]
    assert "T1FilterPersistPolicy(" in set_policy
    # DriverEntry 必须在创建控制设备后加载持久策略
    driver_entry = source[source.index("DriverEntry("):]
    assert "T1FilterLoadPersistedPolicy(g_ControlContext);" in driver_entry


def test_byte_fallback_only_applies_to_consumer_control() -> None:
    """无 parser 时只有消费者控制的字节布局可以解码，其他集合不猜测。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    share_block = source[
        source.index("T1FilterShouldBlockReport(") : source.index(
            "VOID\nT1FilterQueueEvent("
        )
    ]

    assert "} else if (UsagePage == 0x000C) {" in share_block
    assert "只有消费者控制的字节布局经过验证" in source


def test_owner_close_stops_filter_and_control_device_limits_write_access() -> None:
    """所有者句柄关闭等同 STOP；控制设备注册文件回调并只给管理员写权限。"""

    source = FILTER_SOURCE.read_text(encoding="utf-8")
    close_callback = source[
        source.index("T1FilterEvtFileClose(") : source.index(
            "VOID\nT1FilterEvtDeviceControl("
        )
    ]
    create_device = source[
        source.index("T1FilterCreateControlDevice(") : source.index(
            "static BOOLEAN\nT1FilterCharEqualsInsensitive"
        )
    ]

    assert "context->owner_file == FileObject" in close_callback
    assert "WdfObjectDereference(FileObject);" in close_callback
    assert "WdfDeviceInitSetFileObjectConfig" in create_device
    assert "T1FilterEvtFileClose" in create_device
    assert "(A;;GR;;;BU)" in create_device
    assert "GRGW" not in create_device
