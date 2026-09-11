#pragma once

/* 程序说明：定义 T1 HID 过滤驱动的 KMDF 上下文和内部接口。 */

#include <ntddk.h>
#include <wdf.h>
#include <hidclass.h>
#include <hidpddi.h>
#include <hidport.h>

#include "..\t1bridge\t1bridge_protocol.h"

#define T1FILTER_EVENT_QUEUE_CAPACITY 64u
#define T1FILTER_MAX_COLLECTIONS 32u

typedef struct _T1FILTER_ACTIVE_COLLECTION_STATE {
    USHORT usage;
    USHORT mapped_usage;
} T1FILTER_ACTIVE_COLLECTION_STATE;

/*
 * 程序说明：缓存 HID parser 为每个输入 DataIndex 解析出的唯一 Usage。
 *
 * 映射来源必须是 HIDP_BUTTON_CAPS/HIDP_VALUE_CAPS，不能从报告字节位置
 * 反推。ambiguous 用于保留异常或冲突能力，避免内核猜测业务键位。
 */
typedef struct _T1FILTER_PARSER_DATA_MAP {
    USHORT usage_page;
    USHORT usage;
    BOOLEAN mapped;
    BOOLEAN ambiguous;
} T1FILTER_PARSER_DATA_MAP, *PT1FILTER_PARSER_DATA_MAP;

typedef struct _T1FILTER_CONTROL_CONTEXT {
    WDFSPINLOCK lock;
    T1BRIDGE_POLICY policy;
    BOOLEAN filtering_enabled;
    WDFIOTARGET collection_targets[T1FILTER_MAX_COLLECTIONS];
    PHIDP_PREPARSED_DATA parser_preparsed_data[T1FILTER_MAX_COLLECTIONS];
    PUSAGE_AND_PAGE parser_usage_lists[T1FILTER_MAX_COLLECTIONS];
    ULONG parser_usage_capacity[T1FILTER_MAX_COLLECTIONS];
    PHIDP_DATA parser_data_lists[T1FILTER_MAX_COLLECTIONS];
    ULONG parser_data_capacity[T1FILTER_MAX_COLLECTIONS];
    PT1FILTER_PARSER_DATA_MAP parser_data_maps[T1FILTER_MAX_COLLECTIONS];
    ULONG parser_data_map_capacity[T1FILTER_MAX_COLLECTIONS];
    T1FILTER_ACTIVE_COLLECTION_STATE active_collections[
        T1FILTER_MAX_COLLECTIONS
    ];
    ULONG event_head;
    ULONG event_tail;
    ULONG event_count;
    ULONGLONG next_sequence;
    ULONGLONG received_reports;
    ULONGLONG blocked_reports;
    ULONGLONG queued_events;
    ULONGLONG dropped_events;
    ULONGLONG buffer_errors;
    ULONGLONG dropped_reports;
    ULONGLONG forwarded_reports;
    ULONGLONG completion_errors;
    ULONGLONG lease_expirations;
    ULONGLONG device_adds;
    ULONGLONG device_removes;
    ULONGLONG device_control_reports;
    ULONGLONG internal_device_control_reports;
    ULONGLONG lease_deadline_100ns;
    ULONG policy_generation;
    ULONG attached_collections;
    NTSTATUS last_error;
    T1BRIDGE_EVENT events[T1FILTER_EVENT_QUEUE_CAPACITY];
} T1FILTER_CONTROL_CONTEXT, *PT1FILTER_CONTROL_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(
    T1FILTER_CONTROL_CONTEXT,
    T1FilterGetControlContext
);

typedef struct _T1FILTER_DEVICE_CONTEXT {
    PT1FILTER_CONTROL_CONTEXT control;
    USHORT collection;
    USHORT usage_page;
    WDFCOLLECTION sent_requests;
    WDFSPINLOCK sent_requests_lock;
    BOOLEAN registered;
} T1FILTER_DEVICE_CONTEXT, *PT1FILTER_DEVICE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(
    T1FILTER_DEVICE_CONTEXT,
    T1FilterGetDeviceContext
);

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD T1FilterEvtDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL T1FilterEvtHidDeviceControl;
EVT_WDF_IO_QUEUE_IO_READ T1FilterEvtHidRead;
EVT_WDF_IO_QUEUE_IO_STOP T1FilterEvtIoStop;
EVT_WDF_IO_QUEUE_IO_RESUME T1FilterEvtIoResume;
EVT_WDF_IO_QUEUE_IO_INTERNAL_DEVICE_CONTROL T1FilterEvtInternalDeviceControl;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL T1FilterEvtDeviceControl;
EVT_WDF_REQUEST_COMPLETION_ROUTINE T1FilterEvtReadCompletion;
EVT_WDF_REQUEST_COMPLETION_ROUTINE T1FilterEvtGetInputReportCompletion;
EVT_WDF_OBJECT_CONTEXT_CLEANUP T1FilterEvtDeviceCleanup;

NTSTATUS
T1FilterCreateControlDevice(
    _In_ WDFDRIVER Driver,
    _Out_ WDFDEVICE* ControlDevice
);

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
);

VOID
T1FilterQueueEvent(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _In_ USHORT UsagePage,
    _In_ USHORT Usage,
    _In_ USHORT Collection,
    _In_reads_bytes_(ReportLength) const UCHAR* Report,
    _In_ ULONG ReportLength
);

NTSTATUS
T1FilterPopEvent(
    _In_ PT1FILTER_CONTROL_CONTEXT Context,
    _Out_ T1BRIDGE_EVENT* Event
);
