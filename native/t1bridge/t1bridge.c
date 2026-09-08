/* 程序说明：实现 T1 用户态桥接 DLL，负责校验策略并转发 IOCTL。
 *
 * 该 DLL 不安装钩子、不读取键盘消息，也不负责拦截 Windows 输入。
 * 真正的设备级过滤由独立的 T1 HID 过滤驱动完成。
 */

#include "t1bridge_protocol.h"

#include <stdlib.h>

typedef struct T1BRIDGE_SESSION {
    HANDLE device;
    uint32_t magic;
} T1BRIDGE_SESSION;

#define T1BRIDGE_SESSION_MAGIC 0x54425247u /* TBRG */

static int32_t validate_policy(const T1BRIDGE_POLICY* policy) {
    if (policy == NULL) {
        return ERROR_INVALID_PARAMETER;
    }
    if (policy->size != sizeof(T1BRIDGE_POLICY) ||
        policy->abi_version != T1BRIDGE_ABI_VERSION ||
        policy->vid != 0x620Au ||
        policy->pid != 0x0407u ||
        policy->usage_count > T1BRIDGE_MAX_BLOCKED_USAGES ||
        policy->target_collection_count > T1BRIDGE_MAX_TARGET_COLLECTIONS) {
        return ERROR_REVISION_MISMATCH;
    }
    return ERROR_SUCCESS;
}

static T1BRIDGE_SESSION* session_from_handle(void* handle) {
    T1BRIDGE_SESSION* session = (T1BRIDGE_SESSION*)handle;
    if (session == NULL || session->magic != T1BRIDGE_SESSION_MAGIC) {
        return NULL;
    }
    return session;
}

static int32_t send_ioctl(
    T1BRIDGE_SESSION* session,
    DWORD ioctl_code,
    const void* input,
    DWORD input_size,
    void* output,
    DWORD output_size
) {
    DWORD bytes_returned = 0;
    if (!DeviceIoControl(
            session->device,
            ioctl_code,
            (LPVOID)input,
            input_size,
            output,
            output_size,
            &bytes_returned,
            NULL)) {
        return (int32_t)GetLastError();
    }
    return ERROR_SUCCESS;
}

uint32_t T1Bridge_GetAbiVersion(void) {
    return T1BRIDGE_ABI_VERSION;
}

int32_t T1Bridge_Open(const T1BRIDGE_POLICY* policy, void** out_handle) {
    T1BRIDGE_SESSION* session;
    int32_t result;

    if (out_handle == NULL) {
        return ERROR_INVALID_PARAMETER;
    }
    *out_handle = NULL;
    result = validate_policy(policy);
    if (result != ERROR_SUCCESS) {
        return result;
    }

    session = (T1BRIDGE_SESSION*)calloc(1, sizeof(T1BRIDGE_SESSION));
    if (session == NULL) {
        return ERROR_NOT_ENOUGH_MEMORY;
    }

    session->device = CreateFileW(
        T1BRIDGE_DEVICE_PATH,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (session->device == INVALID_HANDLE_VALUE) {
        result = (int32_t)GetLastError();
        free(session);
        return result;
    }
    session->magic = T1BRIDGE_SESSION_MAGIC;

    result = send_ioctl(
        session,
        IOCTL_T1FILTER_SET_POLICY,
        policy,
        sizeof(T1BRIDGE_POLICY),
        NULL,
        0
    );
    if (result != ERROR_SUCCESS) {
        T1Bridge_Close(session);
        return result;
    }

    *out_handle = session;
    return ERROR_SUCCESS;
}

int32_t T1Bridge_SetPolicy(
    void* handle,
    const T1BRIDGE_POLICY* policy
) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    int32_t result = validate_policy(policy);
    if (session == NULL) {
        return ERROR_INVALID_HANDLE;
    }
    if (result != ERROR_SUCCESS) {
        return result;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_SET_POLICY,
        policy,
        sizeof(T1BRIDGE_POLICY),
        NULL,
        0
    );
}

int32_t T1Bridge_Start(void* handle) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL) {
        return ERROR_INVALID_HANDLE;
    }
    return send_ioctl(session, IOCTL_T1FILTER_START, NULL, 0, NULL, 0);
}

int32_t T1Bridge_Heartbeat(void* handle) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL) {
        return ERROR_INVALID_HANDLE;
    }
    return send_ioctl(session, IOCTL_T1FILTER_HEARTBEAT, NULL, 0, NULL, 0);
}

int32_t T1Bridge_Stop(void* handle) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL) {
        return ERROR_INVALID_HANDLE;
    }
    return send_ioctl(session, IOCTL_T1FILTER_STOP, NULL, 0, NULL, 0);
}

int32_t T1Bridge_GetStatus(void* handle, T1BRIDGE_STATUS* status) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL || status == NULL || status->size != sizeof(T1BRIDGE_STATUS)) {
        return ERROR_INVALID_PARAMETER;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_GET_STATUS,
        NULL,
        0,
        status,
        sizeof(T1BRIDGE_STATUS)
    );
}

int32_t T1Bridge_ReadEvent(void* handle, T1BRIDGE_EVENT* event) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL || event == NULL || event->size != sizeof(T1BRIDGE_EVENT)) {
        return ERROR_INVALID_PARAMETER;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_READ_EVENT,
        NULL,
        0,
        event,
        sizeof(T1BRIDGE_EVENT)
    );
}

int32_t T1Bridge_GetCapabilities(
    void* handle,
    T1BRIDGE_CAPABILITIES* capabilities
) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL || capabilities == NULL ||
        capabilities->size != sizeof(T1BRIDGE_CAPABILITIES)) {
        return ERROR_INVALID_PARAMETER;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_GET_CAPABILITIES,
        NULL,
        0,
        capabilities,
        sizeof(T1BRIDGE_CAPABILITIES)
    );
}

int32_t T1Bridge_GetStats(
    void* handle,
    T1BRIDGE_STATS* stats
) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL || stats == NULL ||
        stats->size != sizeof(T1BRIDGE_STATS)) {
        return ERROR_INVALID_PARAMETER;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_GET_STATS,
        NULL,
        0,
        stats,
        sizeof(T1BRIDGE_STATS)
    );
}

int32_t T1Bridge_FlushEvents(void* handle) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL) {
        return ERROR_INVALID_HANDLE;
    }
    return send_ioctl(
        session,
        IOCTL_T1FILTER_FLUSH_EVENTS,
        NULL,
        0,
        NULL,
        0
    );
}

void T1Bridge_Close(void* handle) {
    T1BRIDGE_SESSION* session = session_from_handle(handle);
    if (session == NULL) {
        return;
    }
    /* 关闭前尽力停止过滤；关闭句柄仍然是最终清理动作。 */
    (void)T1Bridge_Stop(session);
    session->magic = 0;
    CloseHandle(session->device);
    free(session);
}
