/*
 * 程序说明：T1 HID 集合的 KMDF 下层过滤驱动能力层。
 *
 * 驱动只负责提供设备级拦截、原始事件转发和同一 HID Usage Page 内的报文改写能力。
 * 哪些 Usage 要拦截、映射到哪个目标 Usage，由 Python 通过运行时策略下发；
 * 驱动不包含 Home、音量或其他业务键位配置。
 *
 * 本文件依赖 WDK/KMDF，最终安装前仍需在目标机器完成驱动栈和真机回归。
 */

#include "t1filter.h"

#define T1FILTER_STATE_UNKNOWN 0u
#define T1FILTER_STATE_STOPPED 1u
#define T1FILTER_STATE_RUNNING 2u
#define T1FILTER_STATE_ERROR 3u

static WDFDEVICE g_ControlDevice = NULL;
static PT1FILTER_CONTROL_CONTEXT g_ControlContext = NULL;
static PDRIVER_OBJECT g_DriverObject = NULL;

static BOOLEAN
T1FilterValidFieldRule(
    _In_ const T1BRIDGE_FIELD_RULE* Rule
)
{
    if (Rule == NULL || Rule->usage_page == 0 || Rule->collection == 0 ||
        Rule->byte_length == 0 || Rule->byte_length > 2 ||
        Rule->byte_offset >= T1BRIDGE_MAX_REPORT_BYTES ||
        (ULONG)Rule->byte_offset + Rule->byte_length >
            T1BRIDGE_MAX_REPORT_BYTES ||
        (Rule->flags & ~(T1BRIDGE_FIELD_RULE_FLAG_REMAP |
                         T1BRIDGE_FIELD_RULE_FLAG_DROP)) != 0) {
        return FALSE;
    }
    return TRUE;
}

static BOOLEAN
T1FilterLeaseIsValidLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context
)
{
    ULONGLONG now;

    if ((Context->policy.flags & T1BRIDGE_FLAG_LEASE_REQUIRED) == 0) {
        return TRUE;
    }

    now = KeQueryInterruptTime();
    if (Context->lease_deadline_100ns == 0 ||
        now >= Context->lease_deadline_100ns) {
        if (Context->filtering_enabled) {
            Context->filtering_enabled = FALSE;
            Context->lease_expirations++;
            Context->last_error = STATUS_TIMEOUT;
            Context->active_usage = 0;
            Context->active_mapped_usage = 0;
            Context->active_collection = 0;
        }
        return FALSE;
    }
    return TRUE;
}

static VOID
T1FilterRefreshLeaseLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context
)
{
    ULONG timeout_ms;

    if ((Context->policy.flags & T1BRIDGE_FLAG_LEASE_REQUIRED) == 0) {
        Context->lease_deadline_100ns = 0;
        return;
    }
    timeout_ms = Context->policy.lease_timeout_ms;
    Context->lease_deadline_100ns =
        KeQueryInterruptTime() + ((ULONGLONG)timeout_ms * 10000ull);
}

static ULONG
T1FilterLeaseRemainingMsLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context
)
{
    ULONGLONG now;
    ULONGLONG remaining;

    if ((Context->policy.flags & T1BRIDGE_FLAG_LEASE_REQUIRED) == 0 ||
        Context->lease_deadline_100ns == 0) {
        return 0;
    }
    now = KeQueryInterruptTime();
    if (now >= Context->lease_deadline_100ns) {
        return 0;
    }
    remaining = Context->lease_deadline_100ns - now;
    remaining /= 10000ull;
    return remaining > MAXULONG ? MAXULONG : (ULONG)remaining;
}

static BOOLEAN
T1FilterIsInputReportControl(
    _In_ ULONG IoControlCode
)
{
    return IoControlCode == IOCTL_HID_READ_REPORT ||
        IoControlCode == IOCTL_HID_GET_INPUT_REPORT ||
        IoControlCode == IOCTL_UMDF_HID_GET_INPUT_REPORT;
}

static VOID
T1FilterLogStatus(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ NTSTATUS Status,
    _In_ ULONG UniqueErrorValue
)
{
    PIO_ERROR_LOG_PACKET packet;

    packet = IoAllocateErrorLogEntry(
        DriverObject,
        sizeof(IO_ERROR_LOG_PACKET)
    );
    if (packet == NULL) {
        return;
    }

    RtlZeroMemory(packet, sizeof(*packet));
    packet->ErrorCode = (ULONG)Status;
    packet->FinalStatus = Status;
    packet->UniqueErrorValue = UniqueErrorValue;
    IoWriteErrorLogEntry(packet);
}

static BOOLEAN
T1FilterCollectionIsTarget(
    _In_ const T1BRIDGE_POLICY* Policy,
    _In_ USHORT Collection
)
{
    ULONG index;

    for (index = 0; index < Policy->target_collection_count; ++index) {
        if (Policy->target_collections[index] == Collection) {
            return TRUE;
        }
    }
    return FALSE;
}

static BOOLEAN
T1FilterFindUsageRule(
    _In_ const T1BRIDGE_POLICY* Policy,
    _In_ USHORT UsagePage,
    _In_ USHORT Usage,
    _In_ USHORT Collection,
    _Out_ USHORT* MappedUsage
)
{
    ULONG index;

    if (MappedUsage == NULL || !T1FilterCollectionIsTarget(Policy, Collection)) {
        return FALSE;
    }

    for (index = 0; index < Policy->usage_count; ++index) {
        const T1BRIDGE_HID_USAGE* candidate = &Policy->usages[index];
        if (candidate->usage_page != UsagePage || candidate->usage != Usage) {
            continue;
        }
        if (candidate->collection == 0 || candidate->collection == Collection) {
            *MappedUsage = candidate->mapped_usage != 0
                ? candidate->mapped_usage
                : Usage;
            return TRUE;
        }
    }
    return FALSE;
}

static BOOLEAN
T1FilterFindFieldRule(
    _In_ const T1BRIDGE_POLICY* Policy,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength,
    _Out_ const T1BRIDGE_FIELD_RULE** MatchedRule,
    _Out_ ULONG* FieldValue
)
{
    ULONG index;

    if (Policy == NULL || Report == NULL || MatchedRule == NULL ||
        FieldValue == NULL) {
        return FALSE;
    }

    for (index = 0; index < Policy->field_rule_count; ++index) {
        const T1BRIDGE_FIELD_RULE* candidate = &Policy->field_rules[index];
        ULONG value = 0;
        UCHAR byte_index;

        if (candidate->usage_page != UsagePage ||
            candidate->collection != Collection ||
            (ULONG)candidate->byte_offset + candidate->byte_length >
                ReportLength ||
            (candidate->report_id != 0 &&
             (ReportLength == 0 || Report[0] != candidate->report_id))) {
            continue;
        }
        for (byte_index = 0; byte_index < candidate->byte_length; ++byte_index) {
            value |= (ULONG)Report[candidate->byte_offset + byte_index]
                << (byte_index * 8);
        }
        if (value != candidate->usage) {
            continue;
        }
        *MatchedRule = candidate;
        *FieldValue = value;
        return TRUE;
    }
    return FALSE;
}

static BOOLEAN
T1FilterFindFieldRuleForUsage(
    _In_ const T1BRIDGE_POLICY* Policy,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _In_ USHORT Usage,
    _Out_ const T1BRIDGE_FIELD_RULE** MatchedRule
)
{
    ULONG index;

    if (Policy == NULL || MatchedRule == NULL) {
        return FALSE;
    }
    for (index = 0; index < Policy->field_rule_count; ++index) {
        const T1BRIDGE_FIELD_RULE* candidate = &Policy->field_rules[index];
        if (candidate->usage_page == UsagePage &&
            candidate->collection == Collection &&
            candidate->usage == Usage) {
            *MatchedRule = candidate;
            return TRUE;
        }
    }
    return FALSE;
}

static BOOLEAN
T1FilterValidPolicy(
    _In_ const T1BRIDGE_POLICY* Policy,
    _In_ size_t Length
)
{
    if (Policy == NULL || Length < sizeof(T1BRIDGE_POLICY)) {
        return FALSE;
    }
    if (Policy->size != sizeof(T1BRIDGE_POLICY) ||
        Policy->abi_version != T1BRIDGE_ABI_VERSION ||
        Policy->vid != 0x620A ||
        Policy->pid != 0x0407 ||
        Policy->usage_count > T1BRIDGE_MAX_BLOCKED_USAGES ||
        Policy->target_collection_count == 0 ||
        Policy->target_collection_count > T1BRIDGE_MAX_TARGET_COLLECTIONS ||
        Policy->field_rule_count > T1BRIDGE_MAX_FIELD_RULES ||
        Policy->lease_timeout_ms < T1BRIDGE_MIN_LEASE_TIMEOUT_MS ||
        Policy->lease_timeout_ms > T1BRIDGE_MAX_LEASE_TIMEOUT_MS) {
        return FALSE;
    }
    for (ULONG index = 0; index < Policy->field_rule_count; ++index) {
        if (!T1FilterValidFieldRule(&Policy->field_rules[index])) {
            return FALSE;
        }
    }
    return TRUE;
}

BOOLEAN
T1FilterShouldBlockReport(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength,
    _Out_ USHORT* Usage,
    _Out_ USHORT* MappedUsage,
    _Out_ BOOLEAN* Pressed
)
{
    T1BRIDGE_POLICY policy;
    USHORT decoded_usage = 0;
    USHORT mapped_usage = 0;
    USHORT collection = Collection;
    BOOLEAN pressed;
    BOOLEAN matched;
    BOOLEAN field_matched;
    BOOLEAN lease_valid;
    BOOLEAN blocked;
    BOOLEAN target_collection;
    const T1BRIDGE_FIELD_RULE* field_rule = NULL;
    ULONG field_value = 0;

    if (Context == NULL || Report == NULL || Usage == NULL ||
        MappedUsage == NULL || Pressed == NULL ||
        ReportLength < 2 || UsagePage == 0 || Collection == 0) {
        return FALSE;
    }

    /* COL03 System Control 是 Report ID + 1 字节 Usage；COL02 保持 16 位格式。 */
    if (UsagePage == 0x0001 && Collection == 3) {
        decoded_usage = (USHORT)Report[1];
    } else {
        if (ReportLength < 3) {
            return FALSE;
        }
        decoded_usage = (USHORT)Report[1] | ((USHORT)Report[2] << 8);
    }
    pressed = decoded_usage != 0;

    WdfSpinLockAcquire(Context->lock);
    policy = Context->policy;
    field_matched = T1FilterFindFieldRule(
        &policy,
        UsagePage,
        Collection,
        Report,
        ReportLength,
        &field_rule,
        &field_value
    );
    if (field_matched) {
        decoded_usage = (USHORT)field_value;
    }
    pressed = decoded_usage != 0;
    lease_valid = T1FilterLeaseIsValidLocked(Context);
    if (pressed) {
        Context->active_usage = decoded_usage;
        Context->active_mapped_usage = decoded_usage;
        Context->active_collection = collection;
    } else {
        decoded_usage = Context->active_usage;
        mapped_usage = Context->active_mapped_usage;
        collection = Context->active_collection;
        Context->active_usage = 0;
        Context->active_mapped_usage = 0;
        Context->active_collection = 0;
    }
    target_collection = T1FilterCollectionIsTarget(&policy, collection);
    if (pressed) {
        mapped_usage = decoded_usage;
    }
    if (!pressed && decoded_usage != 0 && !field_matched) {
        field_matched = T1FilterFindFieldRuleForUsage(
            &policy,
            UsagePage,
            collection,
            decoded_usage,
            &field_rule
        );
    }
    matched = T1FilterFindUsageRule(
        &policy,
        UsagePage,
        decoded_usage,
        collection,
        &mapped_usage
    );
    matched = matched || field_matched;
    if (field_matched) {
        if ((field_rule->flags & T1BRIDGE_FIELD_RULE_FLAG_DROP) != 0) {
            mapped_usage = decoded_usage;
        } else if (field_rule->mapped_usage != 0) {
            mapped_usage = field_rule->mapped_usage;
        }
    }
    if (pressed && matched) {
        Context->active_mapped_usage = mapped_usage;
    }
    if ((policy.flags & T1BRIDGE_FLAG_REMAP) == 0) {
        mapped_usage = decoded_usage;
    }
    if (!pressed && mapped_usage == 0) {
        mapped_usage = decoded_usage;
    }
    blocked = Context->filtering_enabled && lease_valid && target_collection &&
        (matched ||
         ((policy.flags & T1BRIDGE_FLAG_DROP_UNMAPPED) != 0));
    WdfSpinLockRelease(Context->lock);

    *Usage = decoded_usage;
    *MappedUsage = mapped_usage;
    *Pressed = pressed;
    return blocked;
}

VOID
T1FilterQueueEvent(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Usage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength
)
{
    T1BRIDGE_EVENT event;
    ULONG copy_length;

    if (Context == NULL || Report == NULL) {
        return;
    }

    RtlZeroMemory(&event, sizeof(event));
    event.size = sizeof(event);
    event.abi_version = T1BRIDGE_ABI_VERSION;
    event.timestamp_100ns = KeQueryInterruptTime();
    event.usage_page = UsagePage;
    event.usage = Usage;
    event.collection = Collection;
    copy_length = min(ReportLength, T1BRIDGE_MAX_REPORT_BYTES);
    event.report_length = (USHORT)copy_length;
    RtlCopyMemory(event.report, Report, copy_length);

    WdfSpinLockAcquire(Context->lock);
    event.sequence = ++Context->next_sequence;
    Context->queued_events++;
    if (Context->event_count == T1FILTER_EVENT_QUEUE_CAPACITY) {
        Context->event_tail =
            (Context->event_tail + 1) % T1FILTER_EVENT_QUEUE_CAPACITY;
        Context->event_count--;
        Context->dropped_events++;
        Context->dropped_reports = Context->dropped_events;
        Context->last_error = STATUS_BUFFER_OVERFLOW;
    }
    Context->events[Context->event_head] = event;
    Context->event_head =
        (Context->event_head + 1) % T1FILTER_EVENT_QUEUE_CAPACITY;
    Context->event_count++;
    WdfSpinLockRelease(Context->lock);
}

NTSTATUS
T1FilterPopEvent(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _Out_ T1BRIDGE_EVENT* Event
)
{
    if (Context == NULL || Event == NULL) {
        return STATUS_INVALID_PARAMETER;
    }

    WdfSpinLockAcquire(Context->lock);
    if (Context->event_count == 0) {
        WdfSpinLockRelease(Context->lock);
        return STATUS_NO_MORE_ENTRIES;
    }
    *Event = Context->events[Context->event_tail];
    Context->event_tail =
        (Context->event_tail + 1) % T1FILTER_EVENT_QUEUE_CAPACITY;
    Context->event_count--;
    WdfSpinLockRelease(Context->lock);
    return STATUS_SUCCESS;
}

static BOOLEAN
T1FilterRewriteFieldReport(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _Inout_updates_bytes_(ReportLength) UCHAR* Report,
    _In_ ULONG ReportLength,
    _In_ USHORT MappedUsage
)
{
    T1BRIDGE_POLICY policy;
    const T1BRIDGE_FIELD_RULE* rule = NULL;
    ULONG field_value = 0;
    UCHAR byte_index;

    WdfSpinLockAcquire(Context->lock);
    policy = Context->policy;
    WdfSpinLockRelease(Context->lock);

    if (!T1FilterFindFieldRule(
            &policy,
            UsagePage,
            Collection,
            Report,
            ReportLength,
            &rule,
            &field_value)) {
        return FALSE;
    }
    UNREFERENCED_PARAMETER(field_value);
    for (byte_index = 0; byte_index < rule->byte_length; ++byte_index) {
        Report[rule->byte_offset + byte_index] =
            (UCHAR)((MappedUsage >> (byte_index * 8)) & 0xFF);
    }
    return TRUE;
}

static VOID
T1FilterCompleteStatus(
    _In_ WDFREQUEST Request,
    _In_ NTSTATUS Status,
    _In_ ULONG_PTR Information
)
{
    WdfRequestCompleteWithInformation(Request, Status, Information);
}

static VOID
T1FilterCompleteReadRequest(
    _In_ WDFREQUEST Request,
    _In_ PWDF_REQUEST_COMPLETION_PARAMS Params
)
{
    /* 过滤完成回调仍然拥有请求，必须把原状态和字节数回传给 HID 栈。 */
    WdfRequestCompleteWithInformation(
        Request,
        Params->IoStatus.Status,
        Params->IoStatus.Information
    );
}

VOID
T1FilterEvtReadCompletion(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target,
    _In_ PWDF_REQUEST_COMPLETION_PARAMS Params,
    _In_ WDFCONTEXT Context
)
{
    WDFDEVICE device = (WDFDEVICE)Context;
    PT1FILTER_DEVICE_CONTEXT device_context = T1FilterGetDeviceContext(device);
    WDFMEMORY output_memory = NULL;
    PVOID report = NULL;
    size_t report_length = 0;
    ULONG bytes_returned;
    USHORT usage = 0;
    USHORT mapped_usage = 0;
    BOOLEAN pressed = FALSE;
    NTSTATUS status;

    UNREFERENCED_PARAMETER(Target);

    if (!NT_SUCCESS(Params->IoStatus.Status) ||
        device_context == NULL || device_context->control == NULL) {
        if (device_context != NULL && device_context->control != NULL) {
            WdfSpinLockAcquire(device_context->control->lock);
            device_context->control->completion_errors++;
            WdfSpinLockRelease(device_context->control->lock);
        }
        T1FilterCompleteReadRequest(Request, Params);
        return;
    }

    WdfSpinLockAcquire(device_context->control->lock);
    device_context->control->received_reports++;
    WdfSpinLockRelease(device_context->control->lock);

    /*
     * IOCTL_HID_READ_REPORT 使用 METHOD_NEITHER。对于 HIDClass 传入的
     * DeviceControl 请求，WDF 完成参数中的 Output.Buffer 才是首选报告内存。
     * 保留 RetrieveOutputBuffer 作为旧内部控制请求的兼容路径。
     */
    if (Params->Type == WdfRequestTypeDeviceControl ||
        Params->Type == WdfRequestTypeDeviceControlInternal) {
        output_memory = Params->Parameters.Ioctl.Output.Buffer;
        if (output_memory != NULL) {
            report = WdfMemoryGetBuffer(output_memory, &report_length);
        }
    }
    if (report == NULL) {
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            1,
            &report,
            &report_length
        );
    } else {
        status = STATUS_SUCCESS;
    }
    if (!NT_SUCCESS(status)) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->buffer_errors++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(Request, Params);
        return;
    }

    bytes_returned = (ULONG)min(Params->IoStatus.Information, report_length);
    if (!T1FilterShouldBlockReport(
            device_context->control,
            device_context->usage_page,
            device_context->collection,
            (const UCHAR*)report,
            bytes_returned,
            &usage,
            &mapped_usage,
            &pressed)) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->forwarded_reports++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(Request, Params);
        return;
    }
    UNREFERENCED_PARAMETER(pressed);

    T1FilterQueueEvent(
        device_context->control,
        device_context->usage_page,
        usage,
        device_context->collection,
        (const UCHAR*)report,
        bytes_returned
    );
    WdfSpinLockAcquire(device_context->control->lock);
    device_context->control->blocked_reports++;
    WdfSpinLockRelease(device_context->control->lock);
    if (pressed && mapped_usage != usage) {
        if (T1FilterRewriteFieldReport(
                device_context->control,
                device_context->usage_page,
                device_context->collection,
                (UCHAR*)report,
                bytes_returned,
                mapped_usage)) {
            /* 字段规则已经完成按位字段改写。 */
        } else if (device_context->usage_page == 0x0001 &&
            device_context->collection == 3) {
            ((UCHAR*)report)[1] = (UCHAR)(mapped_usage & 0xFF);
        } else if (bytes_returned >= 3) {
            ((UCHAR*)report)[1] = (UCHAR)(mapped_usage & 0xFF);
            ((UCHAR*)report)[2] = (UCHAR)((mapped_usage >> 8) & 0xFF);
        }
    } else {
        RtlZeroMemory(report, bytes_returned);
    }
    T1FilterCompleteReadRequest(Request, Params);
}

VOID
T1FilterForwardHidRequest(
    _In_ WDFREQUEST Request,
    _In_ WDFDEVICE Device,
    _In_ ULONG IoControlCode,
    _In_ BOOLEAN Internal
)
{
    PT1FILTER_DEVICE_CONTEXT device_context =
        T1FilterGetDeviceContext(Device);
    NTSTATUS status;

    if (device_context != NULL && device_context->control != NULL &&
        T1FilterIsInputReportControl(IoControlCode)) {
        WdfSpinLockAcquire(device_context->control->lock);
        if (Internal) {
            device_context->control->internal_device_control_reports++;
        } else {
            device_context->control->device_control_reports++;
        }
        WdfSpinLockRelease(device_context->control->lock);
    }

    WdfRequestFormatRequestUsingCurrentType(Request);
    if (IoControlCode == IOCTL_HID_READ_REPORT ||
        IoControlCode == IOCTL_HID_GET_INPUT_REPORT ||
        IoControlCode == IOCTL_UMDF_HID_GET_INPUT_REPORT) {
        WdfRequestSetCompletionRoutine(
            Request,
            T1FilterEvtReadCompletion,
            Device
        );
    }

    if (!WdfRequestSend(
            Request,
            WdfDeviceGetIoTarget(Device),
            WDF_NO_SEND_OPTIONS)) {
        status = WdfRequestGetStatus(Request);
        WdfRequestComplete(Request, status);
    }
}

VOID
T1FilterEvtHidDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
)
{
    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    T1FilterForwardHidRequest(
        Request,
        WdfIoQueueGetDevice(Queue),
        IoControlCode,
        FALSE
    );
}

VOID
T1FilterEvtInternalDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
)
{
    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    T1FilterForwardHidRequest(
        Request,
        WdfIoQueueGetDevice(Queue),
        IoControlCode,
        TRUE
    );
}

VOID
T1FilterEvtDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
)
{
    PT1FILTER_CONTROL_CONTEXT context = g_ControlContext;
    PVOID buffer = NULL;
    size_t buffer_length = 0;
    NTSTATUS status = STATUS_SUCCESS;

    UNREFERENCED_PARAMETER(Queue);
    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    if (context == NULL) {
        T1FilterCompleteStatus(Request, STATUS_DEVICE_NOT_READY, 0);
        return;
    }

    switch (IoControlCode) {
    case IOCTL_T1FILTER_SET_POLICY:
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(T1BRIDGE_POLICY),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status) && !T1FilterValidPolicy(
                (const T1BRIDGE_POLICY*)buffer,
                buffer_length)) {
            status = STATUS_REVISION_MISMATCH;
        }
        if (NT_SUCCESS(status)) {
            WdfSpinLockAcquire(context->lock);
            context->policy = *(const T1BRIDGE_POLICY*)buffer;
            context->policy_generation++;
            T1FilterRefreshLeaseLocked(context);
            context->last_error = STATUS_SUCCESS;
            WdfSpinLockRelease(context->lock);
        }
        break;

    case IOCTL_T1FILTER_START:
        WdfSpinLockAcquire(context->lock);
        context->filtering_enabled =
            (context->policy.flags & T1BRIDGE_FLAG_ENABLED) != 0;
        T1FilterRefreshLeaseLocked(context);
        context->last_error = STATUS_SUCCESS;
        WdfSpinLockRelease(context->lock);
        break;

    case IOCTL_T1FILTER_STOP:
        WdfSpinLockAcquire(context->lock);
        context->filtering_enabled = FALSE;
        context->lease_deadline_100ns = 0;
        context->active_usage = 0;
        context->active_mapped_usage = 0;
        context->active_collection = 0;
        WdfSpinLockRelease(context->lock);
        break;

    case IOCTL_T1FILTER_HEARTBEAT:
        WdfSpinLockAcquire(context->lock);
        if ((context->policy.flags & T1BRIDGE_FLAG_LEASE_REQUIRED) != 0 &&
            context->filtering_enabled) {
            T1FilterRefreshLeaseLocked(context);
            context->last_error = STATUS_SUCCESS;
        }
        WdfSpinLockRelease(context->lock);
        break;

    case IOCTL_T1FILTER_GET_STATUS:
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(T1BRIDGE_STATUS),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            T1BRIDGE_STATUS* output = (T1BRIDGE_STATUS*)buffer;
            BOOLEAN lease_valid;
            WdfSpinLockAcquire(context->lock);
            RtlZeroMemory(output, sizeof(*output));
            output->size = sizeof(*output);
            output->abi_version = T1BRIDGE_ABI_VERSION;
            lease_valid = T1FilterLeaseIsValidLocked(context);
            output->state = context->filtering_enabled
                ? T1FILTER_STATE_RUNNING
                : T1FILTER_STATE_STOPPED;
            output->last_error = (LONG)context->last_error;
            output->dropped_reports = context->dropped_events;
            output->policy_generation = context->policy_generation;
            output->attached_collections = context->attached_collections;
            output->lease_remaining_ms = T1FilterLeaseRemainingMsLocked(context);
            output->lease_active = lease_valid && context->filtering_enabled;
            if (!output->lease_active) {
                output->state = T1FILTER_STATE_STOPPED;
            }
            WdfSpinLockRelease(context->lock);
            T1FilterCompleteStatus(Request, STATUS_SUCCESS, sizeof(*output));
            return;
        }
        break;

    case IOCTL_T1FILTER_GET_CAPABILITIES:
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(T1BRIDGE_CAPABILITIES),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            T1BRIDGE_CAPABILITIES* output =
                (T1BRIDGE_CAPABILITIES*)buffer;
            RtlZeroMemory(output, sizeof(*output));
            output->size = sizeof(*output);
            output->abi_version = T1BRIDGE_ABI_VERSION;
            output->flags =
                T1BRIDGE_CAPABILITY_RUNTIME_POLICY |
                T1BRIDGE_CAPABILITY_STATS |
                T1BRIDGE_CAPABILITY_FLUSH_EVENTS |
                T1BRIDGE_CAPABILITY_REPORT_REMAP |
                T1BRIDGE_CAPABILITY_SESSION_LEASE |
                T1BRIDGE_CAPABILITY_DIAGNOSTICS |
                T1BRIDGE_CAPABILITY_DESCRIPTOR_RULES;
            output->max_blocked_usages = T1BRIDGE_MAX_BLOCKED_USAGES;
            output->max_target_collections = T1BRIDGE_MAX_TARGET_COLLECTIONS;
            output->max_report_bytes = T1BRIDGE_MAX_REPORT_BYTES;
            output->event_queue_capacity = T1FILTER_EVENT_QUEUE_CAPACITY;
            output->max_field_rules = T1BRIDGE_MAX_FIELD_RULES;
            output->min_lease_timeout_ms = T1BRIDGE_MIN_LEASE_TIMEOUT_MS;
            output->max_lease_timeout_ms = T1BRIDGE_MAX_LEASE_TIMEOUT_MS;
            T1FilterCompleteStatus(
                Request,
                STATUS_SUCCESS,
                sizeof(*output)
            );
            return;
        }
        break;

    case IOCTL_T1FILTER_GET_STATS:
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(T1BRIDGE_STATS),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            T1BRIDGE_STATS* output = (T1BRIDGE_STATS*)buffer;
            WdfSpinLockAcquire(context->lock);
            RtlZeroMemory(output, sizeof(*output));
            output->size = sizeof(*output);
            output->abi_version = T1BRIDGE_ABI_VERSION;
            output->received_reports = context->received_reports;
            output->blocked_reports = context->blocked_reports;
            output->queued_events = context->queued_events;
            output->dropped_events = context->dropped_events;
            output->buffer_errors = context->buffer_errors;
            output->queue_depth = context->event_count;
            output->forwarded_reports = context->forwarded_reports;
            output->completion_errors = context->completion_errors;
            output->lease_expirations = context->lease_expirations;
            output->device_adds = context->device_adds;
            output->device_removes = context->device_removes;
            output->device_control_reports = context->device_control_reports;
            output->internal_device_control_reports =
                context->internal_device_control_reports;
            WdfSpinLockRelease(context->lock);
            T1FilterCompleteStatus(
                Request,
                STATUS_SUCCESS,
                sizeof(*output)
            );
            return;
        }
        break;

    case IOCTL_T1FILTER_FLUSH_EVENTS:
        WdfSpinLockAcquire(context->lock);
        context->event_head = 0;
        context->event_tail = 0;
        context->event_count = 0;
        WdfSpinLockRelease(context->lock);
        break;

    case IOCTL_T1FILTER_READ_EVENT:
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(T1BRIDGE_EVENT),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            status = T1FilterPopEvent(context, (T1BRIDGE_EVENT*)buffer);
            if (NT_SUCCESS(status)) {
                T1FilterCompleteStatus(Request, status, sizeof(T1BRIDGE_EVENT));
                return;
            }
        }
        break;

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    T1FilterCompleteStatus(Request, status, 0);
}

NTSTATUS
T1FilterCreateControlDevice(
    _In_ WDFDRIVER Driver,
    _Out_ WDFDEVICE* ControlDevice
)
{
    PWDFDEVICE_INIT init = NULL;
    WDFDEVICE device = NULL;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_IO_QUEUE_CONFIG queue_config;
    WDF_OBJECT_ATTRIBUTES queue_attributes;
    WDFQUEUE queue = NULL;
    UNICODE_STRING device_name;
    UNICODE_STRING symbolic_link;
    UNICODE_STRING sddl;
    NTSTATUS status;

    RtlInitUnicodeString(&sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;BU)");
    init = WdfControlDeviceInitAllocate(Driver, &sddl);
    if (init == NULL) {
        T1FilterLogStatus(g_DriverObject, STATUS_INSUFFICIENT_RESOURCES, 0x2001);
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    RtlInitUnicodeString(&device_name, L"\\Device\\T1RemoteFilter");
    status = WdfDeviceInitAssignName(init, &device_name);
    if (!NT_SUCCESS(status)) {
        WdfDeviceInitFree(init);
        T1FilterLogStatus(g_DriverObject, status, 0x2002);
        return status;
    }

    WdfDeviceInitSetDeviceType(init, FILE_DEVICE_UNKNOWN);
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, T1FILTER_CONTROL_CONTEXT);
    status = WdfDeviceCreate(&init, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x2003);
        return status;
    }

    g_ControlContext = T1FilterGetControlContext(device);
    RtlZeroMemory(g_ControlContext, sizeof(*g_ControlContext));
    status = WdfSpinLockCreate(WDF_NO_OBJECT_ATTRIBUTES, &g_ControlContext->lock);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x2004);
        return status;
    }
    g_ControlContext->policy.size = sizeof(T1BRIDGE_POLICY);
    g_ControlContext->policy.abi_version = T1BRIDGE_ABI_VERSION;
    g_ControlContext->policy.vid = 0x620A;
    g_ControlContext->policy.pid = 0x0407;
    g_ControlContext->policy.lease_timeout_ms =
        T1BRIDGE_DEFAULT_LEASE_TIMEOUT_MS;
    g_ControlContext->policy.field_rule_count = 0;
    g_ControlContext->last_error = STATUS_SUCCESS;

    RtlInitUnicodeString(&symbolic_link, L"\\DosDevices\\T1RemoteFilter");
    status = WdfDeviceCreateSymbolicLink(device, &symbolic_link);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x2005);
        return status;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queue_config,
        WdfIoQueueDispatchSequential
    );
    queue_config.EvtIoDeviceControl = T1FilterEvtDeviceControl;
    WDF_OBJECT_ATTRIBUTES_INIT(&queue_attributes);
    status = WdfIoQueueCreate(device, &queue_config, &queue_attributes, &queue);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x2006);
        return status;
    }
    /* 默认队列已经接收 DeviceControl 请求，不再重复配置分发类型。 */
    WdfControlFinishInitializing(device);
    g_ControlDevice = device;
    *ControlDevice = device;
    return STATUS_SUCCESS;
}

static BOOLEAN
T1FilterCharEqualsInsensitive(
    _In_ WCHAR Value,
    _In_ WCHAR Expected
)
{
    if (Value >= L'a' && Value <= L'z') {
        Value = (WCHAR)(Value - (L'a' - L'A'));
    }
    return Value == Expected;
}

static USHORT
T1FilterDetectCollection(
    _In_ WDFDEVICE Device
)
{
    WDFMEMORY memory = NULL;
    PVOID property_buffer = NULL;
    size_t property_length = 0;
    const WCHAR* property_text;
    ULONG character_count;
    ULONG index;
    NTSTATUS status;

    status = WdfDeviceAllocAndQueryProperty(
        Device,
        DevicePropertyHardwareID,
        NonPagedPoolNx,
        WDF_NO_OBJECT_ATTRIBUTES,
        &memory
    );
    if (!NT_SUCCESS(status) || memory == NULL) {
        return 0;
    }

    property_buffer = WdfMemoryGetBuffer(memory, &property_length);
    if (property_buffer == NULL || property_length < 5 * sizeof(WCHAR)) {
        return 0;
    }

    property_text = (const WCHAR*)property_buffer;
    character_count = (ULONG)(property_length / sizeof(WCHAR));
    for (index = 0; index + 5 < character_count; ++index) {
        if (!T1FilterCharEqualsInsensitive(property_text[index], L'C') ||
            !T1FilterCharEqualsInsensitive(property_text[index + 1], L'O') ||
            !T1FilterCharEqualsInsensitive(property_text[index + 2], L'L') ||
            property_text[index + 3] != L'0' ||
            (property_text[index + 4] != L'2' &&
             property_text[index + 4] != L'3')) {
            continue;
        }
        return (USHORT)(property_text[index + 4] - L'0');
    }
    return 0;
}

VOID
T1FilterEvtDeviceCleanup(
    _In_ WDFOBJECT Object
)
{
    WDFDEVICE device = (WDFDEVICE)Object;
    PT1FILTER_DEVICE_CONTEXT context = T1FilterGetDeviceContext(device);

    if (context == NULL || context->control == NULL || !context->registered) {
        return;
    }
    WdfSpinLockAcquire(context->control->lock);
    if (context->collection < 32) {
        context->control->attached_collections &=
            ~(1u << context->collection);
    }
    context->control->device_removes++;
    context->registered = FALSE;
    WdfSpinLockRelease(context->control->lock);
}

NTSTATUS
T1FilterEvtDeviceAdd(
    _In_ WDFDRIVER Driver,
    _Inout_ PWDFDEVICE_INIT DeviceInit
)
{
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_IO_QUEUE_CONFIG queue_config;
    WDFQUEUE queue = NULL;
    WDFDEVICE device = NULL;
    PT1FILTER_DEVICE_CONTEXT context;
    NTSTATUS status;

    UNREFERENCED_PARAMETER(Driver);
    WdfFdoInitSetFilter(DeviceInit);
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, T1FILTER_DEVICE_CONTEXT);
    attributes.EvtCleanupCallback = T1FilterEvtDeviceCleanup;
    status = WdfDeviceCreate(&DeviceInit, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3001);
        return status;
    }

    context = T1FilterGetDeviceContext(device);
    context->control = g_ControlContext;
    context->collection = T1FilterDetectCollection(device);
    context->usage_page = context->collection == 3 ? 0x0001 : 0x000C;

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queue_config,
        WdfIoQueueDispatchSequential
    );
    queue_config.EvtIoDeviceControl = T1FilterEvtHidDeviceControl;
    status = WdfIoQueueCreate(
        device,
        &queue_config,
        WDF_NO_OBJECT_ATTRIBUTES,
        &queue
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3002);
        return status;
    }

    /* 默认队列已接收普通 DeviceControl，不再重复配置该请求类型。 */
    WDF_IO_QUEUE_CONFIG_INIT(
        &queue_config,
        WdfIoQueueDispatchSequential
    );
    queue_config.EvtIoInternalDeviceControl =
        T1FilterEvtInternalDeviceControl;
    status = WdfIoQueueCreate(
        device,
        &queue_config,
        WDF_NO_OBJECT_ATTRIBUTES,
        &queue
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3004);
        return status;
    }
    status = WdfDeviceConfigureRequestDispatching(
        device,
        queue,
        WdfRequestTypeDeviceControlInternal
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3005);
        return status;
    }
    WdfSpinLockAcquire(context->control->lock);
    if (context->collection < 32) {
        context->control->attached_collections |=
            1u << context->collection;
    }
    context->control->device_adds++;
    context->registered = TRUE;
    WdfSpinLockRelease(context->control->lock);
    /* 普通和内部设备控制请求分别进入对应转发队列。 */
    return STATUS_SUCCESS;
}

NTSTATUS
DriverEntry(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PUNICODE_STRING RegistryPath
)
{
    WDF_DRIVER_CONFIG config;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDFDRIVER driver = NULL;
    NTSTATUS status;

    g_DriverObject = DriverObject;

    WDF_DRIVER_CONFIG_INIT(&config, T1FilterEvtDeviceAdd);
    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    status = WdfDriverCreate(
        DriverObject,
        RegistryPath,
        &attributes,
        &config,
        &driver
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(DriverObject, status, 0x1001);
        return status;
    }

    status = T1FilterCreateControlDevice(driver, &g_ControlDevice);
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(DriverObject, status, 0x1002);
    }
    return status;
}
