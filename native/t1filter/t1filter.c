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

#define T1FILTER_PARSER_POOL_TAG '1PrT'

static VOID
T1FilterReleaseParserDataLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT Collection
)
{
    PHIDP_PREPARSED_DATA preparsed_data;
    PUSAGE_AND_PAGE usage_list;
    PHIDP_DATA data_list;
    PT1FILTER_PARSER_DATA_MAP data_map;

    if (Context == NULL || Collection >= T1FILTER_MAX_COLLECTIONS) {
        return;
    }

    preparsed_data = Context->parser_preparsed_data[Collection];
    usage_list = Context->parser_usage_lists[Collection];
    data_list = Context->parser_data_lists[Collection];
    data_map = Context->parser_data_maps[Collection];
    Context->parser_preparsed_data[Collection] = NULL;
    Context->parser_usage_lists[Collection] = NULL;
    Context->parser_usage_capacity[Collection] = 0;
    Context->parser_data_lists[Collection] = NULL;
    Context->parser_data_capacity[Collection] = 0;
    Context->parser_data_maps[Collection] = NULL;
    Context->parser_data_map_capacity[Collection] = 0;
    if (preparsed_data != NULL) {
        ExFreePoolWithTag(preparsed_data, T1FILTER_PARSER_POOL_TAG);
    }
    if (usage_list != NULL) {
        ExFreePoolWithTag(usage_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (data_list != NULL) {
        ExFreePoolWithTag(data_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (data_map != NULL) {
        ExFreePoolWithTag(data_map, T1FILTER_PARSER_POOL_TAG);
    }
}

static VOID
T1FilterAddParserDataMap(
    _Inout_updates_(MapCapacity) PT1FILTER_PARSER_DATA_MAP Map,
    _In_ ULONG MapCapacity,
    _In_ USHORT DataIndex,
    _In_ USHORT UsagePage,
    _In_ USHORT Usage
)
{
    PT1FILTER_PARSER_DATA_MAP item;

    if (Map == NULL || DataIndex >= MapCapacity ||
        UsagePage == 0 || Usage == 0) {
        return;
    }
    item = &Map[DataIndex];
    if (!item->mapped && !item->ambiguous) {
        item->usage_page = UsagePage;
        item->usage = Usage;
        item->mapped = TRUE;
        return;
    }
    if (item->mapped && item->usage_page == UsagePage &&
        item->usage == Usage) {
        return;
    }
    item->mapped = FALSE;
    item->ambiguous = TRUE;
}

static VOID
T1FilterAddParserDataRange(
    _Inout_updates_(MapCapacity) PT1FILTER_PARSER_DATA_MAP Map,
    _In_ ULONG MapCapacity,
    _In_ USHORT DataIndexMin,
    _In_ USHORT DataIndexMax,
    _In_ USAGE UsageMin,
    _In_ USAGE UsageMax,
    _In_ USHORT UsagePage
)
{
    ULONG data_span;
    ULONG usage_span;
    ULONG offset;

    data_span = (ULONG)DataIndexMax - (ULONG)DataIndexMin + 1u;
    usage_span = (ULONG)UsageMax - (ULONG)UsageMin + 1u;
    if (data_span == 0 || data_span != usage_span) {
        /* 范围长度不一致时不能猜测 DataIndex 与 Usage 的对应关系。 */
        return;
    }
    for (offset = 0; offset < data_span; ++offset) {
        T1FilterAddParserDataMap(
            Map,
            MapCapacity,
            (USHORT)((ULONG)DataIndexMin + offset),
            UsagePage,
            (USHORT)((ULONG)UsageMin + offset)
        );
    }
}

static BOOLEAN
T1FilterRewriteReportWithParserLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _Inout_updates_bytes_(ReportLength) UCHAR* Report,
    _In_ ULONG ReportLength,
    _In_ USHORT Usage,
    _In_ USHORT MappedUsage
)
{
    PHIDP_PREPARSED_DATA preparsed_data;
    USAGE source_usage[1];
    USAGE mapped_usage[1];
    ULONG usage_length;
    NTSTATUS status;

    if (Context == NULL || Report == NULL || UsagePage == 0 ||
        Collection >= T1FILTER_MAX_COLLECTIONS || Usage == 0 ||
        MappedUsage == 0) {
        return FALSE;
    }
    preparsed_data = Context->parser_preparsed_data[Collection];
    if (preparsed_data == NULL) {
        return FALSE;
    }

    source_usage[0] = Usage;
    usage_length = ARRAYSIZE(source_usage);
    status = HidP_UnsetUsages(
        HidP_Input,
        UsagePage,
        0,
        source_usage,
        &usage_length,
        preparsed_data,
        (PCHAR)Report,
        ReportLength
    );
    if (!NT_SUCCESS(status)) {
        return FALSE;
    }

    mapped_usage[0] = MappedUsage;
    usage_length = ARRAYSIZE(mapped_usage);
    status = HidP_SetUsages(
        HidP_Input,
        UsagePage,
        0,
        mapped_usage,
        &usage_length,
        preparsed_data,
        (PCHAR)Report,
        ReportLength
    );
    return NT_SUCCESS(status);
}

static NTSTATUS
T1FilterCachePreparsedData(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT Collection,
    _In_reads_bytes_(DataLength) const UCHAR* Data,
    _In_ ULONG DataLength
)
{
    PHIDP_PREPARSED_DATA new_preparsed_data;
    PUSAGE_AND_PAGE new_usage_list;
    PHIDP_DATA new_data_list;
    PT1FILTER_PARSER_DATA_MAP new_data_map;
    PHIDP_BUTTON_CAPS button_caps;
    PHIDP_VALUE_CAPS value_caps;
    HIDP_CAPS caps;
    PHIDP_PREPARSED_DATA old_preparsed_data;
    PUSAGE_AND_PAGE old_usage_list;
    PHIDP_DATA old_data_list;
    PT1FILTER_PARSER_DATA_MAP old_data_map;
    ULONG usage_capacity;
    ULONG data_capacity;
    ULONG data_map_capacity;
    SIZE_T usage_bytes;
    SIZE_T data_bytes;
    SIZE_T data_map_bytes;
    USHORT button_caps_length;
    USHORT value_caps_length;
    ULONG index;
    NTSTATUS status;

    new_preparsed_data = NULL;
    new_usage_list = NULL;
    new_data_list = NULL;
    new_data_map = NULL;
    button_caps = NULL;
    value_caps = NULL;
    RtlZeroMemory(&caps, sizeof(caps));

    if (Context == NULL || Data == NULL || DataLength == 0 ||
        Collection >= T1FILTER_MAX_COLLECTIONS ||
        DataLength > T1BRIDGE_MAX_PREPARSED_DATA_BYTES) {
        return STATUS_INVALID_PARAMETER;
    }

    /* 能力数组查询要求 PASSIVE_LEVEL；本函数只由控制队列的同步
     * preparsed 查询路径调用。读完成回调只复用缓存。 */
    new_preparsed_data = (PHIDP_PREPARSED_DATA)ExAllocatePool2(
        POOL_FLAG_NON_PAGED,
        DataLength,
        T1FILTER_PARSER_POOL_TAG
    );
    if (new_preparsed_data == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    RtlCopyMemory(new_preparsed_data, Data, DataLength);

    status = HidP_GetCaps(new_preparsed_data, &caps);
    if (!NT_SUCCESS(status)) {
        status = STATUS_INVALID_PARAMETER;
        goto Exit;
    }

    usage_capacity = HidP_MaxUsageListLength(
        HidP_Input,
        0,
        new_preparsed_data
    );
    if (usage_capacity != 0) {
        if (usage_capacity > ((SIZE_T)-1) / sizeof(USAGE_AND_PAGE)) {
            status = STATUS_INVALID_PARAMETER;
            goto Exit;
        }
        usage_bytes = (SIZE_T)usage_capacity * sizeof(USAGE_AND_PAGE);
        new_usage_list = (PUSAGE_AND_PAGE)ExAllocatePool2(
            POOL_FLAG_NON_PAGED,
            usage_bytes,
            T1FILTER_PARSER_POOL_TAG
        );
        if (new_usage_list == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto Exit;
        }
    }

    data_capacity = HidP_MaxDataListLength(
        HidP_Input,
        new_preparsed_data
    );
    if (data_capacity != 0) {
        if (data_capacity > ((SIZE_T)-1) / sizeof(HIDP_DATA)) {
            status = STATUS_INVALID_PARAMETER;
            goto Exit;
        }
        data_bytes = (SIZE_T)data_capacity * sizeof(HIDP_DATA);
        new_data_list = (PHIDP_DATA)ExAllocatePool2(
            POOL_FLAG_NON_PAGED,
            data_bytes,
            T1FILTER_PARSER_POOL_TAG
        );
        if (new_data_list == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto Exit;
        }
    }

    data_map_capacity = caps.NumberInputDataIndices;
    if (data_map_capacity != 0) {
        data_map_bytes = (SIZE_T)data_map_capacity *
            sizeof(T1FILTER_PARSER_DATA_MAP);
        if (data_map_bytes / sizeof(T1FILTER_PARSER_DATA_MAP) !=
            data_map_capacity) {
            status = STATUS_INVALID_PARAMETER;
            goto Exit;
        }
        new_data_map = (PT1FILTER_PARSER_DATA_MAP)ExAllocatePool2(
            POOL_FLAG_NON_PAGED,
            data_map_bytes,
            T1FILTER_PARSER_POOL_TAG
        );
        if (new_data_map == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto Exit;
        }
        RtlZeroMemory(new_data_map, data_map_bytes);
    }

    button_caps_length = caps.NumberInputButtonCaps;
    if (button_caps_length != 0) {
        button_caps = (PHIDP_BUTTON_CAPS)ExAllocatePool2(
            POOL_FLAG_NON_PAGED,
            (SIZE_T)button_caps_length * sizeof(HIDP_BUTTON_CAPS),
            T1FILTER_PARSER_POOL_TAG
        );
        if (button_caps == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto Exit;
        }
        status = HidP_GetButtonCaps(
            HidP_Input,
            button_caps,
            &button_caps_length,
            new_preparsed_data
        );
        if (!NT_SUCCESS(status)) {
            goto Exit;
        }
        for (index = 0; index < button_caps_length; ++index) {
            if (button_caps[index].IsRange) {
                T1FilterAddParserDataRange(
                    new_data_map,
                    data_map_capacity,
                    button_caps[index].Range.DataIndexMin,
                    button_caps[index].Range.DataIndexMax,
                    button_caps[index].Range.UsageMin,
                    button_caps[index].Range.UsageMax,
                    button_caps[index].UsagePage
                );
            } else {
                T1FilterAddParserDataMap(
                    new_data_map,
                    data_map_capacity,
                    button_caps[index].NotRange.DataIndex,
                    button_caps[index].UsagePage,
                    button_caps[index].NotRange.Usage
                );
            }
        }
    }

    value_caps_length = caps.NumberInputValueCaps;
    if (value_caps_length != 0) {
        value_caps = (PHIDP_VALUE_CAPS)ExAllocatePool2(
            POOL_FLAG_NON_PAGED,
            (SIZE_T)value_caps_length * sizeof(HIDP_VALUE_CAPS),
            T1FILTER_PARSER_POOL_TAG
        );
        if (value_caps == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto Exit;
        }
        status = HidP_GetValueCaps(
            HidP_Input,
            value_caps,
            &value_caps_length,
            new_preparsed_data
        );
        if (!NT_SUCCESS(status)) {
            goto Exit;
        }
        for (index = 0; index < value_caps_length; ++index) {
            /* GetData 不支持 usage value array；此处只缓存能一一证明
             * DataIndex/Usage 关系的单值能力。 */
            if (value_caps[index].ReportCount != 1) {
                continue;
            }
            if (value_caps[index].IsRange) {
                T1FilterAddParserDataRange(
                    new_data_map,
                    data_map_capacity,
                    value_caps[index].Range.DataIndexMin,
                    value_caps[index].Range.DataIndexMax,
                    value_caps[index].Range.UsageMin,
                    value_caps[index].Range.UsageMax,
                    value_caps[index].UsagePage
                );
            } else {
                T1FilterAddParserDataMap(
                    new_data_map,
                    data_map_capacity,
                    value_caps[index].NotRange.DataIndex,
                    value_caps[index].UsagePage,
                    value_caps[index].NotRange.Usage
                );
            }
        }
    }

    WdfSpinLockAcquire(Context->lock);
    old_preparsed_data = Context->parser_preparsed_data[Collection];
    old_usage_list = Context->parser_usage_lists[Collection];
    old_data_list = Context->parser_data_lists[Collection];
    old_data_map = Context->parser_data_maps[Collection];
    Context->parser_preparsed_data[Collection] = new_preparsed_data;
    Context->parser_usage_lists[Collection] = new_usage_list;
    Context->parser_usage_capacity[Collection] = usage_capacity;
    Context->parser_data_lists[Collection] = new_data_list;
    Context->parser_data_capacity[Collection] = data_capacity;
    Context->parser_data_maps[Collection] = new_data_map;
    Context->parser_data_map_capacity[Collection] = data_map_capacity;
    WdfSpinLockRelease(Context->lock);

    if (old_preparsed_data != NULL) {
        ExFreePoolWithTag(old_preparsed_data, T1FILTER_PARSER_POOL_TAG);
    }
    if (old_usage_list != NULL) {
        ExFreePoolWithTag(old_usage_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (old_data_list != NULL) {
        ExFreePoolWithTag(old_data_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (old_data_map != NULL) {
        ExFreePoolWithTag(old_data_map, T1FILTER_PARSER_POOL_TAG);
    }
    if (button_caps != NULL) {
        ExFreePoolWithTag(button_caps, T1FILTER_PARSER_POOL_TAG);
    }
    if (value_caps != NULL) {
        ExFreePoolWithTag(value_caps, T1FILTER_PARSER_POOL_TAG);
    }
    return STATUS_SUCCESS;

Exit:
    if (button_caps != NULL) {
        ExFreePoolWithTag(button_caps, T1FILTER_PARSER_POOL_TAG);
    }
    if (value_caps != NULL) {
        ExFreePoolWithTag(value_caps, T1FILTER_PARSER_POOL_TAG);
    }
    if (new_data_map != NULL) {
        ExFreePoolWithTag(new_data_map, T1FILTER_PARSER_POOL_TAG);
    }
    if (new_data_list != NULL) {
        ExFreePoolWithTag(new_data_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (new_usage_list != NULL) {
        ExFreePoolWithTag(new_usage_list, T1FILTER_PARSER_POOL_TAG);
    }
    if (new_preparsed_data != NULL) {
        ExFreePoolWithTag(new_preparsed_data, T1FILTER_PARSER_POOL_TAG);
    }
    return status;
}

static BOOLEAN
T1FilterValidFieldRule(
    _In_ const T1BRIDGE_FIELD_RULE* Rule
)
{
    if (Rule == NULL || Rule->usage_page == 0 || Rule->collection == 0 ||
        Rule->collection >= T1FILTER_MAX_COLLECTIONS || Rule->usage == 0 ||
        Rule->byte_length == 0 || Rule->byte_length > 2 ||
        Rule->byte_offset >= T1BRIDGE_MAX_REPORT_BYTES ||
        (ULONG)Rule->byte_offset + Rule->byte_length >
            T1BRIDGE_MAX_REPORT_BYTES ||
        (((Rule->flags & T1BRIDGE_FIELD_RULE_FLAG_REMAP) != 0) &&
         ((Rule->flags & T1BRIDGE_FIELD_RULE_FLAG_DROP) != 0)) ||
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
            RtlZeroMemory(
                Context->active_collections,
                sizeof(Context->active_collections)
            );
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

static NTSTATUS
T1FilterTrackSentRequest(
    _In_ PT1FILTER_DEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST Request
)
{
    NTSTATUS status;

    if (DeviceContext == NULL || DeviceContext->sent_requests == NULL ||
        DeviceContext->sent_requests_lock == NULL) {
        return STATUS_DEVICE_NOT_READY;
    }

    /* 集合会增加请求引用，完成回调移除后再释放该引用。 */
    WdfSpinLockAcquire(DeviceContext->sent_requests_lock);
    status = WdfCollectionAdd(
        DeviceContext->sent_requests,
        Request
    );
    WdfSpinLockRelease(DeviceContext->sent_requests_lock);
    return status;
}

static BOOLEAN
T1FilterUntrackSentRequest(
    _In_ PT1FILTER_DEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST Request
)
{
    ULONG count;
    ULONG index;
    BOOLEAN found = FALSE;

    if (DeviceContext == NULL || DeviceContext->sent_requests == NULL ||
        DeviceContext->sent_requests_lock == NULL) {
        return FALSE;
    }

    WdfSpinLockAcquire(DeviceContext->sent_requests_lock);
    count = WdfCollectionGetCount(DeviceContext->sent_requests);
    for (index = 0; index < count; ++index) {
        WDFOBJECT object = WdfCollectionGetItem(
            DeviceContext->sent_requests,
            index
        );
        if (object == Request) {
            WdfCollectionRemoveItem(
                DeviceContext->sent_requests,
                index
            );
            found = TRUE;
            break;
        }
    }
    WdfSpinLockRelease(DeviceContext->sent_requests_lock);
    return found;
}

static BOOLEAN
T1FilterReferenceTrackedRequest(
    _In_ PT1FILTER_DEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST Request
)
{
    ULONG count;
    ULONG index;
    BOOLEAN found = FALSE;

    if (DeviceContext == NULL || DeviceContext->sent_requests == NULL ||
        DeviceContext->sent_requests_lock == NULL) {
        return FALSE;
    }

    /* 按 Microsoft 的取消同步顺序，在锁内确认并暂时引用请求。 */
    WdfSpinLockAcquire(DeviceContext->sent_requests_lock);
    count = WdfCollectionGetCount(DeviceContext->sent_requests);
    for (index = 0; index < count; ++index) {
        WDFOBJECT object = WdfCollectionGetItem(
            DeviceContext->sent_requests,
            index
        );
        if (object == Request) {
            WdfObjectReference(Request);
            found = TRUE;
            break;
        }
    }
    WdfSpinLockRelease(DeviceContext->sent_requests_lock);
    return found;
}

static BOOLEAN
T1FilterSendTrackedRequest(
    _In_ WDFREQUEST Request,
    _In_ WDFDEVICE Device
)
{
    PT1FILTER_DEVICE_CONTEXT device_context =
        T1FilterGetDeviceContext(Device);
    NTSTATUS status;

    status = T1FilterTrackSentRequest(device_context, Request);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return FALSE;
    }

    if (!WdfRequestSend(
            Request,
            WdfDeviceGetIoTarget(Device),
            WDF_NO_SEND_OPTIONS)) {
        status = WdfRequestGetStatus(Request);
        /* 发送失败时没有完成回调替我们移除集合项。 */
        (void)T1FilterUntrackSentRequest(device_context, Request);
        WdfRequestComplete(Request, status);
        return FALSE;
    }
    return TRUE;
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
T1FilterTryDecodeDataLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength,
    _Out_ USHORT* DecodedUsage,
    _Out_ BOOLEAN* Pressed
)
{
    PHIDP_PREPARSED_DATA preparsed_data;
    PHIDP_DATA data_list;
    PT1FILTER_PARSER_DATA_MAP data_map;
    ULONG data_capacity;
    ULONG data_map_capacity;
    ULONG data_length;
    ULONG index;
    ULONG active_count;
    USHORT candidate_usage;
    NTSTATUS status;

    if (Context == NULL || Report == NULL || DecodedUsage == NULL ||
        Pressed == NULL || Collection >= T1FILTER_MAX_COLLECTIONS ||
        ReportLength == 0) {
        return FALSE;
    }

    preparsed_data = Context->parser_preparsed_data[Collection];
    data_list = Context->parser_data_lists[Collection];
    data_map = Context->parser_data_maps[Collection];
    data_capacity = Context->parser_data_capacity[Collection];
    data_map_capacity = Context->parser_data_map_capacity[Collection];
    if (preparsed_data == NULL || data_list == NULL ||
        data_capacity == 0 || data_map == NULL || data_map_capacity == 0) {
        return FALSE;
    }

    data_length = data_capacity;
    status = HidP_GetData(
        HidP_Input,
        data_list,
        &data_length,
        preparsed_data,
        (PCHAR)Report,
        ReportLength
    );
    if (!NT_SUCCESS(status)) {
        return FALSE;
    }

    active_count = 0;
    candidate_usage = 0;
    for (index = 0; index < data_length; ++index) {
        PT1FILTER_PARSER_DATA_MAP item;

        /* HIDP_DATA 的 RawValue/On 共用同一个四字节字段。对 T1
         * 按键语义，只把非零控制值当成 active input。 */
        if (data_list[index].RawValue == 0) {
            continue;
        }
        if (data_list[index].DataIndex >= data_map_capacity) {
            return FALSE;
        }
        item = &data_map[data_list[index].DataIndex];
        if (!item->mapped || item->ambiguous ||
            item->usage_page != UsagePage || item->usage == 0) {
            /* DataIndex 没有唯一 Usage 证明时，交给兼容路径。 */
            return FALSE;
        }
        candidate_usage = item->usage;
        active_count++;
        if (active_count > 1) {
            /* 多个控制同时 active 时不猜测应映射哪个业务 Usage。 */
            return FALSE;
        }
    }
    if (active_count == 0) {
        *DecodedUsage = 0;
        *Pressed = FALSE;
        return TRUE;
    }
    *DecodedUsage = candidate_usage;
    *Pressed = TRUE;
    return TRUE;
}

static BOOLEAN
T1FilterTryDecodeWithParserLocked(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength,
    _Out_ USHORT* DecodedUsage,
    _Out_ BOOLEAN* Pressed
)
{
    PHIDP_PREPARSED_DATA preparsed_data;
    PUSAGE_AND_PAGE usage_list;
    ULONG usage_capacity;
    ULONG usage_length;
    NTSTATUS status;

    if (Context == NULL || Report == NULL || DecodedUsage == NULL ||
        Pressed == NULL || Collection >= T1FILTER_MAX_COLLECTIONS ||
        ReportLength == 0) {
        return FALSE;
    }

    preparsed_data = Context->parser_preparsed_data[Collection];
    usage_list = Context->parser_usage_lists[Collection];
    usage_capacity = Context->parser_usage_capacity[Collection];
    if (preparsed_data == NULL || usage_list == NULL || usage_capacity == 0) {
        return FALSE;
    }

    usage_length = usage_capacity;
    status = HidP_GetUsagesEx(
        HidP_Input,
        0,
        usage_list,
        &usage_length,
        preparsed_data,
        (PCHAR)Report,
        ReportLength
    );
    if (status == HIDP_STATUS_USAGE_NOT_FOUND) {
        *DecodedUsage = 0;
        *Pressed = FALSE;
        return TRUE;
    }
    if (!NT_SUCCESS(status) || usage_length != 1 ||
        usage_list[0].UsagePage != UsagePage ||
        usage_list[0].Usage == 0) {
        /* 多个活动 Usage 或 parser 拒绝报告时不猜一个结果，交给兼容
         * 路径处理；这样未知布局不会被错误地映射成第一个字节。 */
        return FALSE;
    }

    *DecodedUsage = usage_list[0].Usage;
    *Pressed = TRUE;
    return TRUE;
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
        (Policy->flags & ~(T1BRIDGE_FLAG_ENABLED |
                           T1BRIDGE_FLAG_DROP_UNMAPPED |
                           T1BRIDGE_FLAG_REMAP |
                           T1BRIDGE_FLAG_LEASE_REQUIRED)) != 0 ||
        Policy->usage_count > T1BRIDGE_MAX_BLOCKED_USAGES ||
        Policy->target_collection_count == 0 ||
        Policy->target_collection_count > T1BRIDGE_MAX_TARGET_COLLECTIONS ||
        Policy->field_rule_count > T1BRIDGE_MAX_FIELD_RULES ||
        Policy->lease_timeout_ms < T1BRIDGE_MIN_LEASE_TIMEOUT_MS ||
        Policy->lease_timeout_ms > T1BRIDGE_MAX_LEASE_TIMEOUT_MS) {
        return FALSE;
    }
    for (ULONG index = 0; index < Policy->target_collection_count; ++index) {
        if (Policy->target_collections[index] == 0 ||
            Policy->target_collections[index] >= T1FILTER_MAX_COLLECTIONS) {
            return FALSE;
        }
    }
    for (ULONG index = 0; index < Policy->usage_count; ++index) {
        if (Policy->usages[index].usage_page == 0 ||
            Policy->usages[index].usage == 0 ||
            Policy->usages[index].collection >= T1FILTER_MAX_COLLECTIONS) {
            return FALSE;
        }
    }
    for (ULONG index = 0; index < Policy->usage_count; ++index) {
        for (ULONG other_index = index + 1;
             other_index < Policy->usage_count;
             ++other_index) {
            const T1BRIDGE_HID_USAGE* source = &Policy->usages[index];
            const T1BRIDGE_HID_USAGE* other = &Policy->usages[other_index];
            if (source->usage_page == other->usage_page &&
                source->usage == other->usage &&
                (source->collection == other->collection ||
                 source->collection == 0 || other->collection == 0)) {
                return FALSE;
            }
        }
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
    _Out_ BOOLEAN* Pressed,
    _Out_ ULONG* PolicyGeneration
)
{
    T1BRIDGE_POLICY policy;
    USHORT decoded_usage = 0;
    USHORT mapped_usage = 0;
    BOOLEAN pressed;
    BOOLEAN matched;
    BOOLEAN field_matched;
    BOOLEAN parser_decoded;
    BOOLEAN parser_pressed;
    BOOLEAN lease_valid;
    BOOLEAN blocked;
    BOOLEAN target_collection;
    const T1BRIDGE_FIELD_RULE* field_rule = NULL;
    T1FILTER_ACTIVE_COLLECTION_STATE* active_state = NULL;
    ULONG field_value = 0;
    ULONG policy_generation;

    if (Context == NULL || Report == NULL || Usage == NULL ||
        MappedUsage == NULL || Pressed == NULL || PolicyGeneration == NULL ||
        ReportLength < 2 || UsagePage == 0 || Collection == 0) {
        return FALSE;
    }

    /* 每个 Top-Level Collection 独立维护活动 Usage，避免 COL03 的零报告
     * 把 COL02 Voice 误判为已释放。Collection 编号来自设备栈，超出数组
     * 范围时不保存状态，但仍按当前报告安全处理。 */
    if (Collection < T1FILTER_MAX_COLLECTIONS) {
        active_state = &Context->active_collections[Collection];
    }

    WdfSpinLockAcquire(Context->lock);
    policy = Context->policy;
    policy_generation = Context->policy_generation;
    parser_decoded = T1FilterTryDecodeDataLocked(
        Context,
        UsagePage,
        Collection,
        Report,
        ReportLength,
        &decoded_usage,
        &parser_pressed
    );
    if (!parser_decoded) {
        parser_decoded = T1FilterTryDecodeWithParserLocked(
            Context,
            UsagePage,
            Collection,
            Report,
            ReportLength,
            &decoded_usage,
            &parser_pressed
        );
    }
    if (parser_decoded) {
        /* HidP_GetData 优先覆盖 value control；GetUsagesEx 作为按钮
         * 能力或旧缓存的兼容路径。两条 parser 路径都不读取固定字节。 */
        pressed = parser_pressed;
        field_matched = FALSE;
    } else {
        /* parser 没有唯一 DataIndex/Usage 证据时，保留当前 T1 兼容
         * 布局，直到真实能力证据足以替换该 ABI。固定 byte offset 只在 parser
         * 不可用或拒绝当前报告时使用。 */
        if (UsagePage == 0x0001 && Collection == 3) {
            if (ReportLength < 2) {
                WdfSpinLockRelease(Context->lock);
                return FALSE;
            }
            decoded_usage = (USHORT)Report[1];
        } else {
            if (ReportLength < 3) {
                WdfSpinLockRelease(Context->lock);
                return FALSE;
            }
            decoded_usage = (USHORT)Report[1] |
                ((USHORT)Report[2] << 8);
        }
        pressed = decoded_usage != 0;
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
    }
    lease_valid = T1FilterLeaseIsValidLocked(Context);
    if (pressed) {
        if (active_state != NULL) {
            active_state->usage = decoded_usage;
            active_state->mapped_usage = decoded_usage;
        }
    } else if (active_state != NULL && active_state->usage != 0) {
        decoded_usage = active_state->usage;
        mapped_usage = active_state->mapped_usage;
        active_state->usage = 0;
        active_state->mapped_usage = 0;
    }
    if (parser_decoded && decoded_usage != 0) {
        field_matched = T1FilterFindFieldRuleForUsage(
            &policy,
            UsagePage,
            Collection,
            decoded_usage,
            &field_rule
        );
    }
    target_collection = T1FilterCollectionIsTarget(&policy, Collection);
    if (pressed) {
        mapped_usage = decoded_usage;
    }
    if (!pressed && decoded_usage != 0 && !field_matched) {
        field_matched = T1FilterFindFieldRuleForUsage(
            &policy,
            UsagePage,
            Collection,
            decoded_usage,
            &field_rule
        );
    }
    matched = T1FilterFindUsageRule(
        &policy,
        UsagePage,
        decoded_usage,
        Collection,
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
        if (active_state != NULL) {
            active_state->mapped_usage = mapped_usage;
        }
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
    *PolicyGeneration = policy_generation;
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
T1FilterRewriteReport(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Collection,
    _Inout_updates_bytes_(ReportLength) UCHAR* Report,
    _In_ ULONG ReportLength,
    _In_ USHORT Usage,
    _In_ USHORT MappedUsage,
    _In_ ULONG ExpectedGeneration
)
{
    T1BRIDGE_POLICY policy;
    const T1BRIDGE_FIELD_RULE* rule = NULL;
    ULONG field_value = 0;
    UCHAR byte_index;
    BOOLEAN rewritten = FALSE;

    if (Context == NULL || Report == NULL ||
        Collection >= T1FILTER_MAX_COLLECTIONS) {
        return FALSE;
    }
    WdfSpinLockAcquire(Context->lock);
    if (Context->policy_generation != ExpectedGeneration) {
        WdfSpinLockRelease(Context->lock);
        return FALSE;
    }
    policy = Context->policy;
    if (Context->parser_preparsed_data[Collection] != NULL) {
        rewritten = T1FilterRewriteReportWithParserLocked(
            Context,
            UsagePage,
            Collection,
            Report,
            ReportLength,
            Usage,
            MappedUsage
        );
        WdfSpinLockRelease(Context->lock);
        return rewritten;
    }
    if (T1FilterFindFieldRule(
            &policy,
            UsagePage,
            Collection,
            Report,
            ReportLength,
            &rule,
            &field_value)) {
        UNREFERENCED_PARAMETER(field_value);
        for (byte_index = 0; byte_index < rule->byte_length; ++byte_index) {
            Report[rule->byte_offset + byte_index] =
                (UCHAR)((MappedUsage >> (byte_index * 8)) & 0xFF);
        }
        rewritten = TRUE;
    } else if (UsagePage == 0x0001 && Collection == 3 &&
               ReportLength >= 2) {
        Report[1] = (UCHAR)(MappedUsage & 0xFF);
        rewritten = TRUE;
    } else if (ReportLength >= 3) {
        Report[1] = (UCHAR)(MappedUsage & 0xFF);
        Report[2] = (UCHAR)((MappedUsage >> 8) & 0xFF);
        rewritten = TRUE;
    }
    WdfSpinLockRelease(Context->lock);
    return rewritten;
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
T1FilterRecordControlError(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ ULONG IoControlCode,
    _In_ NTSTATUS Status
)
{
    if (Context == NULL || NT_SUCCESS(Status) ||
        (IoControlCode != IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR &&
         IoControlCode != IOCTL_T1FILTER_GET_PREPARSED_DATA)) {
        return;
    }

    /* 保留下层原始 NTSTATUS，便于把 Win32 错误码 1 还原为真实原因。 */
    WdfSpinLockAcquire(Context->lock);
    Context->last_error = Status;
    WdfSpinLockRelease(Context->lock);
}

static NTSTATUS
T1FilterReadReportDescriptor(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT Collection,
    _Out_writes_bytes_(DescriptorCapacity) UCHAR* Descriptor,
    _In_ ULONG DescriptorCapacity,
    _Out_ PULONG DescriptorLength
)
{
    WDFIOTARGET target = NULL;
    WDF_MEMORY_DESCRIPTOR output_descriptor;
    WDF_REQUEST_SEND_OPTIONS send_options;
    ULONG_PTR bytes_returned = 0;
    NTSTATUS status;

    if (Context == NULL || Descriptor == NULL || DescriptorLength == NULL ||
        DescriptorCapacity == 0 || Collection >= T1FILTER_MAX_COLLECTIONS) {
        return STATUS_INVALID_PARAMETER;
    }

    *DescriptorLength = 0;
    WdfSpinLockAcquire(Context->lock);
    target = Context->collection_targets[Collection];
    if (target != NULL) {
        /* 设备清理可能并发清空表项；引用保证同步查询期间 target 仍存活。 */
        WdfObjectReference(target);
    }
    WdfSpinLockRelease(Context->lock);
    if (target == NULL) {
        return STATUS_DEVICE_NOT_READY;
    }

    /* 同步查询必须有界，避免异常的下层 HID 栈永久占住控制队列。 */
    WDF_REQUEST_SEND_OPTIONS_INIT(&send_options, 0);
    WDF_REQUEST_SEND_OPTIONS_SET_TIMEOUT(
        &send_options,
        WDF_REL_TIMEOUT_IN_SEC(5)
    );
    /* 该调用运行在控制队列的 PASSIVE_LEVEL，直接向 HID minidriver 查询。 */
    WDF_MEMORY_DESCRIPTOR_INIT_BUFFER(
        &output_descriptor,
        Descriptor,
        DescriptorCapacity
    );
    status = WdfIoTargetSendIoctlSynchronously(
        target,
        NULL,
        IOCTL_HID_GET_REPORT_DESCRIPTOR,
        NULL,
        &output_descriptor,
        &send_options,
        &bytes_returned
    );
    WdfObjectDereference(target);
    if (bytes_returned > DescriptorCapacity) {
        bytes_returned = DescriptorCapacity;
    }
    *DescriptorLength = (ULONG)bytes_returned;
    return status;
}

static NTSTATUS
T1FilterReadPreparsedData(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT Collection,
    _Out_writes_bytes_(DataCapacity) UCHAR* Data,
    _In_ ULONG DataCapacity,
    _Out_ PULONG DataLength
)
{
    WDFIOTARGET target = NULL;
    HID_COLLECTION_INFORMATION collection_information;
    WDF_MEMORY_DESCRIPTOR information_descriptor;
    WDF_MEMORY_DESCRIPTOR output_descriptor;
    WDF_REQUEST_SEND_OPTIONS send_options;
    ULONG_PTR bytes_returned = 0;
    ULONG descriptor_size;
    NTSTATUS status;

    if (Context == NULL || Data == NULL || DataLength == NULL ||
        DataCapacity == 0 || Collection >= T1FILTER_MAX_COLLECTIONS) {
        return STATUS_INVALID_PARAMETER;
    }

    *DataLength = 0;
    WdfSpinLockAcquire(Context->lock);
    target = Context->collection_targets[Collection];
    if (target != NULL) {
        /* 设备清理可能并发清空表项；引用保证两次查询期间 target 仍存活。 */
        WdfObjectReference(target);
    }
    WdfSpinLockRelease(Context->lock);
    if (target == NULL) {
        return STATUS_DEVICE_NOT_READY;
    }

    /* 同步查询必须有界，避免异常的下层 HID 栈永久占住控制队列。 */
    WDF_REQUEST_SEND_OPTIONS_INIT(&send_options, 0);
    WDF_REQUEST_SEND_OPTIONS_SET_TIMEOUT(
        &send_options,
        WDF_REL_TIMEOUT_IN_SEC(5)
    );
    /* 按 HID 官方顺序先取得长度，再请求 opaque Collection Descriptor。 */
    RtlZeroMemory(&collection_information, sizeof(collection_information));
    WDF_MEMORY_DESCRIPTOR_INIT_BUFFER(
        &information_descriptor,
        &collection_information,
        sizeof(collection_information)
    );
    status = WdfIoTargetSendIoctlSynchronously(
        target,
        NULL,
        IOCTL_HID_GET_COLLECTION_INFORMATION,
        NULL,
        &information_descriptor,
        &send_options,
        &bytes_returned
    );
    if (!NT_SUCCESS(status)) {
        WdfObjectDereference(target);
        return status;
    }

    descriptor_size = collection_information.DescriptorSize;
    if (descriptor_size == 0 || descriptor_size > DataCapacity) {
        WdfObjectDereference(target);
        return STATUS_BUFFER_TOO_SMALL;
    }

    bytes_returned = 0;
    WDF_MEMORY_DESCRIPTOR_INIT_BUFFER(
        &output_descriptor,
        Data,
        DataCapacity
    );
    status = WdfIoTargetSendIoctlSynchronously(
        target,
        NULL,
        IOCTL_HID_GET_COLLECTION_DESCRIPTOR,
        NULL,
        &output_descriptor,
        &send_options,
        &bytes_returned
    );
    WdfObjectDereference(target);
    if (bytes_returned > DataCapacity) {
        bytes_returned = DataCapacity;
    }
    if (NT_SUCCESS(status) && bytes_returned == 0) {
        /* 某些 HID 栈不回填 Information，长度仍由 Information 查询给出。 */
        bytes_returned = descriptor_size;
    }
    *DataLength = (ULONG)bytes_returned;
    if (NT_SUCCESS(status) && bytes_returned != 0) {
        /* 复制一份 NonPaged parser 输入和能力映射，供后续 Read 完成
         * 回调在 DISPATCH_LEVEL 直接调用 HidP_GetData；输出仍保留给
         * 桥接层。 */
        (void)T1FilterCachePreparsedData(
            Context,
            Collection,
            Data,
            (ULONG)bytes_returned
        );
    }
    return status;
}

static VOID
T1FilterCompleteReadRequest(
    _In_ WDFREQUEST Request,
    _In_ PWDF_REQUEST_COMPLETION_PARAMS Params,
    _In_opt_ PT1FILTER_DEVICE_CONTEXT DeviceContext
)
{
    (void)T1FilterUntrackSentRequest(DeviceContext, Request);

    /* 过滤完成回调仍然拥有请求，必须把原状态和字节数回传给 HID 栈。 */
    WdfRequestCompleteWithInformation(
        Request,
        Params->IoStatus.Status,
        Params->IoStatus.Information
    );
}

static VOID
T1FilterClearReportPayload(
    _Inout_updates_bytes_(ReportLength) PUCHAR Report,
    _In_ ULONG ReportLength
)
{
    if (Report == NULL || ReportLength <= 1) {
        return;
    }

    /* HID 输入报告的首字节是 Report ID，清零 payload 但保留该 ID。 */
    RtlZeroMemory(Report + 1, ReportLength - 1);
}

static BOOLEAN
T1FilterGetInputReportBuffer(
    _In_ WDFREQUEST Request,
    _Outptr_result_bytebuffer_(*ReportLength) PUCHAR* Report,
    _Out_ ULONG* ReportLength
)
{
    PIRP irp;
    PHID_XFER_PACKET packet;
    PUCHAR mapped_buffer;
    PUCHAR original_buffer;
    ULONG mdl_length;
    ULONG_PTR report_address;
    ULONG_PTR mdl_address;
    ULONG_PTR offset;

    if (Request == NULL || Report == NULL || ReportLength == NULL) {
        return FALSE;
    }
    *Report = NULL;
    *ReportLength = 0;

    irp = WdfRequestWdmGetIrp(Request);
    if (irp == NULL || irp->UserBuffer == NULL) {
        return FALSE;
    }
    packet = (PHID_XFER_PACKET)irp->UserBuffer;
    if (packet->reportBuffer == NULL || packet->reportBufferLen == 0) {
        return FALSE;
    }

    /*
     * GET_INPUT_REPORT 使用 IRP UserBuffer 中的 HID_XFER_PACKET，报告本体
     * 由 reportBuffer 指向。优先把 MDL 映射到内核地址，避免在完成回调中
     * 直接解引用用户地址；没有 MDL 时只接受内核发起的请求。
     */
    if (irp->MdlAddress == NULL) {
        if (irp->RequestorMode != KernelMode) {
            return FALSE;
        }
        *Report = packet->reportBuffer;
        *ReportLength = packet->reportBufferLen;
        return TRUE;
    }

    mapped_buffer = (PUCHAR)MmGetSystemAddressForMdlSafe(
        irp->MdlAddress,
        NormalPagePriority
    );
    original_buffer = (PUCHAR)MmGetMdlVirtualAddress(irp->MdlAddress);
    mdl_length = MmGetMdlByteCount(irp->MdlAddress);
    if (mapped_buffer == NULL || original_buffer == NULL || mdl_length == 0) {
        return FALSE;
    }

    report_address = (ULONG_PTR)packet->reportBuffer;
    mdl_address = (ULONG_PTR)original_buffer;
    if (report_address < mdl_address) {
        return FALSE;
    }
    offset = report_address - mdl_address;
    if (offset >= mdl_length ||
        packet->reportBufferLen > mdl_length - (ULONG)offset) {
        return FALSE;
    }
    *Report = mapped_buffer + (ULONG)offset;
    *ReportLength = packet->reportBufferLen;
    return TRUE;
}

VOID
T1FilterEvtGetInputReportCompletion(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target,
    _In_ PWDF_REQUEST_COMPLETION_PARAMS Params,
    _In_ WDFCONTEXT Context
)
{
    WDFDEVICE device = (WDFDEVICE)Context;
    PT1FILTER_DEVICE_CONTEXT device_context =
        T1FilterGetDeviceContext(device);
    PUCHAR report = NULL;
    ULONG report_length = 0;
    USHORT usage = 0;
    USHORT mapped_usage = 0;
    BOOLEAN pressed = FALSE;
    ULONG policy_generation = 0;

    UNREFERENCED_PARAMETER(Target);

    if (!NT_SUCCESS(Params->IoStatus.Status) ||
        device_context == NULL || device_context->control == NULL) {
        if (device_context != NULL && device_context->control != NULL) {
            WdfSpinLockAcquire(device_context->control->lock);
            device_context->control->completion_errors++;
            WdfSpinLockRelease(device_context->control->lock);
        }
        T1FilterCompleteReadRequest(Request, Params, device_context);
        return;
    }

    WdfSpinLockAcquire(device_context->control->lock);
    device_context->control->received_reports++;
    WdfSpinLockRelease(device_context->control->lock);

    if (!T1FilterGetInputReportBuffer(
            Request,
            &report,
            &report_length
        ) || report_length < 2) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->buffer_errors++;
        device_context->control->forwarded_reports++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(Request, Params, device_context);
        return;
    }

    if (!T1FilterShouldBlockReport(
            device_context->control,
            device_context->usage_page,
            device_context->collection,
            report,
            report_length,
            &usage,
            &mapped_usage,
            &pressed,
            &policy_generation
        )) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->forwarded_reports++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(Request, Params, device_context);
        return;
    }

    T1FilterQueueEvent(
        device_context->control,
        device_context->usage_page,
        usage,
        device_context->collection,
        report,
        report_length
    );
    WdfSpinLockAcquire(device_context->control->lock);
    device_context->control->blocked_reports++;
    WdfSpinLockRelease(device_context->control->lock);
    if (pressed && mapped_usage != usage) {
        if (!T1FilterRewriteReport(
                device_context->control,
                device_context->usage_page,
                device_context->collection,
                report,
                report_length,
                usage,
                mapped_usage,
                policy_generation
            )) {
            T1FilterClearReportPayload(report, report_length);
        }
    } else {
        T1FilterClearReportPayload(report, report_length);
    }
    T1FilterCompleteReadRequest(Request, Params, device_context);
}

static VOID
T1FilterEvtForwardCompletion(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target,
    _In_ PWDF_REQUEST_COMPLETION_PARAMS Params,
    _In_ WDFCONTEXT Context
)
{
    WDFDEVICE device = (WDFDEVICE)Context;
    PT1FILTER_DEVICE_CONTEXT device_context =
        T1FilterGetDeviceContext(device);

    UNREFERENCED_PARAMETER(Target);
    /* 非输入控制请求也必须从已发送集合移除，避免设备对象泄漏。 */
    T1FilterCompleteReadRequest(Request, Params, device_context);
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
    PVOID report = NULL;
    size_t report_length = 0;
    ULONG bytes_returned;
    USHORT usage = 0;
    USHORT mapped_usage = 0;
    BOOLEAN pressed = FALSE;
    ULONG policy_generation = 0;
    NTSTATUS status;

    UNREFERENCED_PARAMETER(Target);

    if (!NT_SUCCESS(Params->IoStatus.Status) ||
        device_context == NULL || device_context->control == NULL) {
        if (device_context != NULL && device_context->control != NULL) {
            WdfSpinLockAcquire(device_context->control->lock);
            device_context->control->completion_errors++;
            WdfSpinLockRelease(device_context->control->lock);
        }
        T1FilterCompleteReadRequest(
            Request,
            Params,
            device_context
        );
        return;
    }

    WdfSpinLockAcquire(device_context->control->lock);
    device_context->control->received_reports++;
    WdfSpinLockRelease(device_context->control->lock);

    /*
     * 这里使用 WdfRequestFormatRequestUsingCurrentType 转发请求。根据
     * KMDF 约定，完成参数中只有 IoStatus 有效，不能读取完成参数中的
     * 请求类型或 IOCTL 内存句柄；报告缓冲区从仍未完成的请求对象取得。
     */
    status = WdfRequestRetrieveOutputBuffer(
        Request,
        1,
        &report,
        &report_length
    );
    if (!NT_SUCCESS(status)) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->buffer_errors++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(
            Request,
            Params,
            device_context
        );
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
            &pressed,
            &policy_generation)) {
        WdfSpinLockAcquire(device_context->control->lock);
        device_context->control->forwarded_reports++;
        WdfSpinLockRelease(device_context->control->lock);
        T1FilterCompleteReadRequest(
            Request,
            Params,
            device_context
        );
        return;
    }
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
        if (!T1FilterRewriteReport(
                device_context->control,
                device_context->usage_page,
                device_context->collection,
                (UCHAR*)report,
                bytes_returned,
                usage,
                mapped_usage,
                policy_generation)) {
            /* 策略在解码后发生变化时，宁可丢弃本次报告也不写错字段。 */
            T1FilterClearReportPayload((UCHAR*)report, bytes_returned);
        }
    } else {
        T1FilterClearReportPayload((UCHAR*)report, bytes_returned);
    }
    T1FilterCompleteReadRequest(
        Request,
        Params,
        device_context
    );
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
    /*
     * 持续 Read 和 GET_INPUT_REPORT 使用不同的缓冲区契约：前者从 WDF
     * 输出缓冲区读取，后者从 HID_XFER_PACKET.reportBuffer 读取。两者
     * 必须使用不同的完成回调，避免把嵌入指针当成报告本体。
     */
    if (IoControlCode == IOCTL_HID_READ_REPORT) {
        WdfRequestSetCompletionRoutine(
            Request,
            T1FilterEvtReadCompletion,
            Device
        );
    } else if (
        IoControlCode == IOCTL_HID_GET_INPUT_REPORT ||
        IoControlCode == IOCTL_UMDF_HID_GET_INPUT_REPORT
    ) {
        WdfRequestSetCompletionRoutine(
            Request,
            T1FilterEvtGetInputReportCompletion,
            Device
        );
    } else {
        WdfRequestSetCompletionRoutine(
            Request,
            T1FilterEvtForwardCompletion,
            Device
        );
    }
    (void)T1FilterSendTrackedRequest(Request, Device);
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
T1FilterEvtHidRead(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t Length
)
{
    WDFDEVICE device = WdfIoQueueGetDevice(Queue);

    UNREFERENCED_PARAMETER(Length);

    /* BLE HID/UMDF 的持续输入报告通过 Read 请求到达这里。 */
    WdfRequestFormatRequestUsingCurrentType(Request);
    WdfRequestSetCompletionRoutine(
        Request,
        T1FilterEvtReadCompletion,
        device
    );
    (void)T1FilterSendTrackedRequest(Request, device);
}

VOID
T1FilterEvtIoStop(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ ULONG ActionFlags
)
{
    WDFDEVICE device = WdfIoQueueGetDevice(Queue);
    PT1FILTER_DEVICE_CONTEXT device_context =
        T1FilterGetDeviceContext(device);

    /* 转发中的请求必须参与睡眠、移除和取消流程。 */
    if ((ActionFlags & WdfRequestStopActionPurge) != 0) {
        if (T1FilterReferenceTrackedRequest(device_context, Request)) {
            (void)WdfRequestCancelSentRequest(Request);
            WdfObjectDereference(Request);
        } else {
            /* 发送失败或已完成的请求仍需向框架确认停止。 */
            WdfRequestStopAcknowledge(Request, FALSE);
        }
        return;
    }

    WdfRequestStopAcknowledge(Request, FALSE);
}

VOID
T1FilterEvtIoResume(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request
)
{
    UNREFERENCED_PARAMETER(Queue);
    UNREFERENCED_PARAMETER(Request);
    /* 下层目标仍持有转发请求，回到 D0 后无需重新提交。 */
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
            BOOLEAN was_filtering = context->filtering_enabled;
            context->policy = *(const T1BRIDGE_POLICY*)buffer;
            context->filtering_enabled = was_filtering &&
                (context->policy.flags & T1BRIDGE_FLAG_ENABLED) != 0;
            RtlZeroMemory(
                context->active_collections,
                sizeof(context->active_collections)
            );
            context->policy_generation++;
            T1FilterRefreshLeaseLocked(context);
            context->last_error = STATUS_SUCCESS;
            WdfSpinLockRelease(context->lock);
        }
        break;

    case IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR:
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(T1BRIDGE_DESCRIPTOR_QUERY),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            const T1BRIDGE_DESCRIPTOR_QUERY* query =
                (const T1BRIDGE_DESCRIPTOR_QUERY*)buffer;
            USHORT collection = query->collection;
            T1BRIDGE_REPORT_DESCRIPTOR* output;
            ULONG descriptor_length = 0;

            if (query->reserved != 0 ||
                query->collection >= T1FILTER_MAX_COLLECTIONS) {
                status = STATUS_INVALID_PARAMETER;
                break;
            }
            status = WdfRequestRetrieveOutputBuffer(
                Request,
                sizeof(T1BRIDGE_REPORT_DESCRIPTOR),
                &buffer,
                &buffer_length
            );
            if (!NT_SUCCESS(status)) {
                break;
            }
            output = (T1BRIDGE_REPORT_DESCRIPTOR*)buffer;
            RtlZeroMemory(output, sizeof(*output));
            output->size = sizeof(*output);
            output->abi_version = T1BRIDGE_ABI_VERSION;
            output->collection = collection;
            status = T1FilterReadReportDescriptor(
                context,
                collection,
                output->descriptor,
                T1BRIDGE_MAX_REPORT_DESCRIPTOR_BYTES,
                &descriptor_length
            );
            if (NT_SUCCESS(status)) {
                output->descriptor_length = descriptor_length;
                T1FilterCompleteStatus(
                    Request,
                    status,
                    FIELD_OFFSET(T1BRIDGE_REPORT_DESCRIPTOR, descriptor) +
                        descriptor_length
                );
                return;
            }
        }
        break;

    case IOCTL_T1FILTER_GET_PREPARSED_DATA:
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(T1BRIDGE_DESCRIPTOR_QUERY),
            &buffer,
            &buffer_length
        );
        if (NT_SUCCESS(status)) {
            const T1BRIDGE_DESCRIPTOR_QUERY* query =
                (const T1BRIDGE_DESCRIPTOR_QUERY*)buffer;
            USHORT collection = query->collection;
            T1BRIDGE_PREPARSED_DATA* output;
            ULONG data_length = 0;

            if (query->reserved != 0 ||
                query->collection >= T1FILTER_MAX_COLLECTIONS) {
                status = STATUS_INVALID_PARAMETER;
                break;
            }
            status = WdfRequestRetrieveOutputBuffer(
                Request,
                sizeof(T1BRIDGE_PREPARSED_DATA),
                &buffer,
                &buffer_length
            );
            if (!NT_SUCCESS(status)) {
                break;
            }
            output = (T1BRIDGE_PREPARSED_DATA*)buffer;
            RtlZeroMemory(output, sizeof(*output));
            output->size = sizeof(*output);
            output->abi_version = T1BRIDGE_ABI_VERSION;
            output->collection = collection;
            status = T1FilterReadPreparsedData(
                context,
                collection,
                output->data,
                T1BRIDGE_MAX_PREPARSED_DATA_BYTES,
                &data_length
            );
            if (NT_SUCCESS(status)) {
                output->data_length = data_length;
                T1FilterCompleteStatus(
                    Request,
                    status,
                    FIELD_OFFSET(T1BRIDGE_PREPARSED_DATA, data) +
                        data_length
                );
                return;
            }
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
        RtlZeroMemory(
            context->active_collections,
            sizeof(context->active_collections)
        );
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
                T1BRIDGE_CAPABILITY_DESCRIPTOR_RULES |
                T1BRIDGE_CAPABILITY_REPORT_DESCRIPTOR |
                T1BRIDGE_CAPABILITY_PREPARSED_DATA;
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

    T1FilterRecordControlError(context, IoControlCode, status);
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
    /* 同步 HID 描述符查询要求 PASSIVE_LEVEL。 */
    queue_attributes.ExecutionLevel = WdfExecutionLevelPassive;
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
    if (context->collection < T1FILTER_MAX_COLLECTIONS) {
        context->control->collection_targets[context->collection] = NULL;
        T1FilterReleaseParserDataLocked(
            context->control,
            context->collection
        );
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
    status = WdfSpinLockCreate(
        WDF_NO_OBJECT_ATTRIBUTES,
        &context->sent_requests_lock
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3006);
        return status;
    }
    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = device;
    status = WdfCollectionCreate(
        &attributes,
        &context->sent_requests
    );
    if (!NT_SUCCESS(status)) {
        T1FilterLogStatus(g_DriverObject, status, 0x3007);
        return status;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queue_config,
        WdfIoQueueDispatchParallel
    );
    queue_config.EvtIoDeviceControl = T1FilterEvtHidDeviceControl;
    queue_config.EvtIoRead = T1FilterEvtHidRead;
    queue_config.EvtIoStop = T1FilterEvtIoStop;
    queue_config.EvtIoResume = T1FilterEvtIoResume;
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
        WdfIoQueueDispatchParallel
    );
    queue_config.EvtIoInternalDeviceControl =
        T1FilterEvtInternalDeviceControl;
    /* 内部 HID 读请求也会被转发，设备移除时必须走同一取消路径。 */
    queue_config.EvtIoStop = T1FilterEvtIoStop;
    queue_config.EvtIoResume = T1FilterEvtIoResume;
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
    if (context->collection < T1FILTER_MAX_COLLECTIONS) {
        context->control->collection_targets[context->collection] =
            WdfDeviceGetIoTarget(device);
    }
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
