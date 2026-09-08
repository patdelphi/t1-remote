# T1 Windows HID 官方文档对照审查

程序说明：按 Microsoft Learn 的 HID、KMDF 请求队列和电源管理文档审查当前 T1 过滤器实现。本文区分现场已证实的问题、已经补上的代码和仍需后续实现的能力，不把未验证的设备协议写成结论。

## 1. 对照结论

| 编号 | 问题或遗漏 | 当前证据 | 处理状态 |
| --- | --- | --- | --- |
| P0-1 | 过滤器没有处理 BLE HID 的持续 `IRP_MJ_READ` 输入报告 | 现场驱动统计为 `received_reports=0`、普通/内部 DeviceControl 均为 0；设备栈为 `T1RemoteFilter -> mshidumdf` | 已补 `EvtIoRead`，新包已构建，尚未安装 |
| P0-2 | 异步转发 Read 请求没有 `EvtIoStop` | Read 请求被转发到下层，原队列没有电源停止/移除处理 | 已补 `EvtIoStop`，待随新包安装验证 |
| P1-1 | 报告解析依赖固定 `Report[1]`、`Report[2]` | 当前 `T1FilterShouldBlockReport` 对 COL02/COL03 使用硬编码布局 | 待改为 Descriptor/Preparsed Data + `HidP_*` |
| P1-2 | 原始采集仍先解析 Usage，再决定是否入队 | `drop_unmapped` 仍经过当前解析分支；未知长度或未知布局可能直接漏报 | 待增加独立 raw-capture 分支，先保存原始报告，再由 Python 解析 |
| P1-3 | 报告缓冲区上限为 64 字节 | 本机 `COL05` 的 `InputReportByteLength=256` 已由 `HidP_GetCaps` 确认 | 当前只过滤 COL02/COL03；扩展到 COL05 前必须扩大 ABI 和队列事件缓冲区 |
| P1-4 | 字段规则只支持 1/2 字节，不能表达任意 bit field、数组和多个 Report ID | 当前 `T1BRIDGE_FIELD_RULE` 使用 byte offset/byte length | 待以 HID capability/data index 模型重做字段规则 |
| P2-1 | `COL02/COL03` 的用户态 `HidD_GetPreparsedData` 和直接 Descriptor 查询返回 `ERROR_INVALID_FUNCTION (1)` | 已现场复现 | 待在内核过滤器中通过 `IOCTL_HID_GET_COLLECTION_INFORMATION` 和 `IOCTL_HID_GET_COLLECTION_DESCRIPTOR` 获取 preparsed data，或先完成 raw capture |
| P2-2 | Raw Input 不能作为 Consumer/System Control 的唯一输入源 | 当前 Raw Input 只列出 T1 的 COL01/COL04/COL05；COL02/COL03 不在列表中 | 已确定：Consumer/System 采用驱动队列路径，Raw Input 仅作辅助观察 |

## 2. 已按官方模型补上的部分

### 2.1 Read 请求

当前过滤器默认队列已注册 `EvtIoRead`，使用 `WdfRequestFormatRequestUsingCurrentType`、`WdfRequestSetCompletionRoutine` 和 `WdfRequestSend` 将请求转发到下层，并复用完成回调读取报告缓冲区。

### 2.2 睡眠和设备移除

当前队列已注册 `EvtIoStop`：

- `WdfRequestStopActionPurge` 时调用 `WdfRequestCancelSentRequest`。
- Suspend 等待场景调用 `WdfRequestStopAcknowledge`。

这对应 KMDF 对电源管理队列中“已转发请求”的处理要求。

## 3. 必须遵守的解析原则

Microsoft 的 HID 模型以 Top-Level Collection 的 Report Descriptor 为输入。驱动或应用应取得该 Collection 的 preparsed data，再使用 `HidP_GetUsages`、`HidP_GetUsagesEx`、`HidP_GetUsageValue`、`HidP_SetUsages` 等 HID parser API 读写控制数据。

因此后续实现应遵循：

1. 过滤器先保留完整原始报告和 Report ID。
2. 通过 Collection Descriptor 建立 Usage、Report ID、bit offset、bit size、Report count 和 data index 映射。
3. Python 负责把原始报告转换为物理按键和逻辑动作。
4. 驱动只有在字段规则已经由 Descriptor 验证后才做阻断或重映射。

固定 `Report[1]`/`Report[2]` 只能作为当前真机的临时诊断兼容，不应作为最终协议实现。

## 4. 官方依据

- [Top-Level Collections](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/top-level-collections)
- [Obtaining HID Reports](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/obtaining-hid-reports)
- [Obtaining Preparsed Data](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/obtaining-preparsed-data)
- [Interpreting HID Reports](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/interpreting-hid-reports)
- [HID Architecture](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hid-architecture)
- [Request Handlers](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/request-handlers)
- [Completing I/O Requests](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/completing-i-o-requests)
- [Using Power-Managed I/O Queues](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/using-power-managed-i-o-queues)

## 5. 当前执行顺序

1. 安装已经构建的新驱动并重启一次设备栈，确认 `received_reports` 开始增长。
2. 每次只按一个遥控键，保存完整 Read 报告。
3. 将固定偏移解析降级为临时兼容，先实现 raw-capture 入队。
4. 取得 COL02/COL03 的 preparsed data 后，再实现正式 Usage 和字段重映射。
5. 在扩展到 COL05、Air Mouse 或麦克风前，先把报告缓冲区从 64 字节扩展到覆盖实测报告长度。

