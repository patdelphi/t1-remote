#pragma once

/* 程序说明：定义 Python、t1bridge.dll 与 T1 HID 过滤驱动共享的 ABI。 */

#ifdef _KERNEL_MODE
#include <ntddk.h>
typedef UCHAR T1BRIDGE_UINT8;
typedef USHORT T1BRIDGE_UINT16;
typedef ULONG T1BRIDGE_UINT32;
typedef ULONGLONG T1BRIDGE_UINT64;
typedef LONG T1BRIDGE_INT32;
#else
#include <stdint.h>
#include <windows.h>
#include <winioctl.h>
typedef uint8_t T1BRIDGE_UINT8;
typedef uint16_t T1BRIDGE_UINT16;
typedef uint32_t T1BRIDGE_UINT32;
typedef uint64_t T1BRIDGE_UINT64;
typedef int32_t T1BRIDGE_INT32;
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define T1BRIDGE_ABI_VERSION 2u
#define T1BRIDGE_MAX_BLOCKED_USAGES 32u
#define T1BRIDGE_MAX_TARGET_COLLECTIONS 8u
#define T1BRIDGE_MAX_FIELD_RULES 32u
#define T1BRIDGE_MAX_REPORT_BYTES 64u
#define T1BRIDGE_DEFAULT_LEASE_TIMEOUT_MS 3000u
#define T1BRIDGE_MIN_LEASE_TIMEOUT_MS 250u
#define T1BRIDGE_MAX_LEASE_TIMEOUT_MS 60000u
#define T1BRIDGE_DEVICE_PATH L"\\\\.\\T1RemoteFilter"

#define T1BRIDGE_FLAG_ENABLED 0x0001u
#define T1BRIDGE_FLAG_DROP_UNMAPPED 0x0002u
#define T1BRIDGE_FLAG_REMAP 0x0004u
#define T1BRIDGE_FLAG_LEASE_REQUIRED 0x0008u

#define T1BRIDGE_FIELD_RULE_FLAG_REMAP 0x0001u
#define T1BRIDGE_FIELD_RULE_FLAG_DROP 0x0002u

#define T1BRIDGE_CAPABILITY_RUNTIME_POLICY 0x00000001u
#define T1BRIDGE_CAPABILITY_STATS 0x00000002u
#define T1BRIDGE_CAPABILITY_FLUSH_EVENTS 0x00000004u
#define T1BRIDGE_CAPABILITY_REPORT_REMAP 0x00000008u
#define T1BRIDGE_CAPABILITY_SESSION_LEASE 0x00000010u
#define T1BRIDGE_CAPABILITY_DIAGNOSTICS 0x00000020u
#define T1BRIDGE_CAPABILITY_DESCRIPTOR_RULES 0x00000040u

#define IOCTL_T1FILTER_SET_POLICY \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_START \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_STOP \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_GET_STATUS \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_READ_EVENT \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x804, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_GET_CAPABILITIES \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x805, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_GET_STATS \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x806, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_FLUSH_EVENTS \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x807, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_T1FILTER_HEARTBEAT \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x808, METHOD_BUFFERED, FILE_ANY_ACCESS)

#pragma pack(push, 1)
typedef struct T1BRIDGE_HID_USAGE {
    T1BRIDGE_UINT16 usage_page;
    T1BRIDGE_UINT16 usage;
    T1BRIDGE_UINT16 collection;
    T1BRIDGE_UINT16 mapped_usage;
} T1BRIDGE_HID_USAGE;

typedef struct T1BRIDGE_FIELD_RULE {
    T1BRIDGE_UINT16 usage_page;
    T1BRIDGE_UINT16 collection;
    T1BRIDGE_UINT16 usage;
    T1BRIDGE_UINT16 mapped_usage;
    T1BRIDGE_UINT8 report_id;
    T1BRIDGE_UINT8 byte_offset;
    T1BRIDGE_UINT8 byte_length;
    T1BRIDGE_UINT8 flags;
} T1BRIDGE_FIELD_RULE;

typedef struct T1BRIDGE_POLICY {
    T1BRIDGE_UINT32 size;
    T1BRIDGE_UINT32 abi_version;
    T1BRIDGE_UINT16 vid;
    T1BRIDGE_UINT16 pid;
    T1BRIDGE_UINT32 flags;
    T1BRIDGE_UINT32 usage_count;
    T1BRIDGE_UINT32 target_collection_count;
    T1BRIDGE_UINT16 target_collections[T1BRIDGE_MAX_TARGET_COLLECTIONS];
    T1BRIDGE_HID_USAGE usages[T1BRIDGE_MAX_BLOCKED_USAGES];
    T1BRIDGE_UINT32 lease_timeout_ms;
    T1BRIDGE_UINT32 field_rule_count;
    T1BRIDGE_FIELD_RULE field_rules[T1BRIDGE_MAX_FIELD_RULES];
} T1BRIDGE_POLICY;

typedef struct T1BRIDGE_STATUS {
    T1BRIDGE_UINT32 size;
    T1BRIDGE_UINT32 abi_version;
    T1BRIDGE_UINT32 state;
    T1BRIDGE_INT32 last_error;
    T1BRIDGE_UINT64 dropped_reports;
    T1BRIDGE_UINT32 policy_generation;
    T1BRIDGE_UINT32 attached_collections;
    T1BRIDGE_UINT32 lease_remaining_ms;
    T1BRIDGE_UINT32 lease_active;
} T1BRIDGE_STATUS;

typedef struct T1BRIDGE_EVENT {
    T1BRIDGE_UINT32 size;
    T1BRIDGE_UINT32 abi_version;
    T1BRIDGE_UINT64 sequence;
    T1BRIDGE_UINT64 timestamp_100ns;
    T1BRIDGE_UINT16 usage_page;
    T1BRIDGE_UINT16 usage;
    T1BRIDGE_UINT16 collection;
    T1BRIDGE_UINT16 report_length;
    T1BRIDGE_UINT8 report[T1BRIDGE_MAX_REPORT_BYTES];
} T1BRIDGE_EVENT;

typedef struct T1BRIDGE_CAPABILITIES {
    T1BRIDGE_UINT32 size;
    T1BRIDGE_UINT32 abi_version;
    T1BRIDGE_UINT32 flags;
    T1BRIDGE_UINT32 max_blocked_usages;
    T1BRIDGE_UINT32 max_target_collections;
    T1BRIDGE_UINT32 max_report_bytes;
    T1BRIDGE_UINT32 event_queue_capacity;
    T1BRIDGE_UINT32 max_field_rules;
    T1BRIDGE_UINT32 min_lease_timeout_ms;
    T1BRIDGE_UINT32 max_lease_timeout_ms;
} T1BRIDGE_CAPABILITIES;

typedef struct T1BRIDGE_STATS {
    T1BRIDGE_UINT32 size;
    T1BRIDGE_UINT32 abi_version;
    T1BRIDGE_UINT64 received_reports;
    T1BRIDGE_UINT64 blocked_reports;
    T1BRIDGE_UINT64 queued_events;
    T1BRIDGE_UINT64 dropped_events;
    T1BRIDGE_UINT64 buffer_errors;
    T1BRIDGE_UINT32 queue_depth;
    T1BRIDGE_UINT32 reserved;
    T1BRIDGE_UINT64 forwarded_reports;
    T1BRIDGE_UINT64 completion_errors;
    T1BRIDGE_UINT64 lease_expirations;
    T1BRIDGE_UINT64 device_adds;
    T1BRIDGE_UINT64 device_removes;
    T1BRIDGE_UINT64 device_control_reports;
    T1BRIDGE_UINT64 internal_device_control_reports;
} T1BRIDGE_STATS;
#pragma pack(pop)

#ifndef _KERNEL_MODE
__declspec(dllexport) T1BRIDGE_UINT32 T1Bridge_GetAbiVersion(void);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_Open(
    const T1BRIDGE_POLICY* policy,
    void** out_handle
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_SetPolicy(
    void* handle,
    const T1BRIDGE_POLICY* policy
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_Start(void* handle);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_Heartbeat(void* handle);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_Stop(void* handle);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_GetStatus(
    void* handle,
    T1BRIDGE_STATUS* status
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_ReadEvent(
    void* handle,
    T1BRIDGE_EVENT* event
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_GetCapabilities(
    void* handle,
    T1BRIDGE_CAPABILITIES* capabilities
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_GetStats(
    void* handle,
    T1BRIDGE_STATS* stats
);
__declspec(dllexport) T1BRIDGE_INT32 T1Bridge_FlushEvents(void* handle);
__declspec(dllexport) void T1Bridge_Close(void* handle);
#endif

#ifdef __cplusplus
}
#endif
