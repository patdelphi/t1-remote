# T1 Windows HID 官方文档对照审查

程序说明：按 Microsoft Learn 的 HID、KMDF 请求队列和电源管理文档审查当前 T1 过滤器实现。本文区分现场已证实的问题、已经补上的代码和仍需后续实现的能力，不把未验证的设备协议写成结论。

## 1. 对照结论

| 编号 | 问题或遗漏 | 当前证据 | 处理状态 |
| --- | --- | --- | --- |
| P0-1 | 过滤器没有处理 BLE HID 的持续 `IRP_MJ_READ` 输入报告 | 设备栈为 `T1RemoteFilter -> mshidumdf`；建立持续 HID ReadFile 后现场收到 94 条报告，丢包 0 | 已补 `EvtIoRead`，系统重启后已确认 `oem351.inf` 活动镜像生效；持续输入链路已现场验证 |
| P0-2 | 异步转发请求的停止/取消路径没有保护请求句柄 | Read 请求被转发到下层，完成回调可能与 `EvtIoStop` 并发 | 已补 `EvtIoStop`、已发送请求集合、临时引用和 `EvtIoResume`，已通过构建与静态契约测试 |
| P0-3 | `GET_INPUT_REPORT`/`UMDF_HID_GET_INPUT_REPORT` 误用持续 Read 的报告完成回调 | 这两类请求的缓冲契约不同，不能直接按输出报告缓冲区读取；Power 仍能触发原生动作 | 已增加 HID_XFER_PACKET 专用完成回调，从 reportBuffer 读取、匹配并清零输入报告；待安装新包后现场验证 |
| P1-1 | 报告解析依赖固定 `Report[1]`、`Report[2]` | 当前 T1 preparsed data 可取得，原始 Descriptor 仍不可用 | 内核已接入 `HidP_GetData` 与 Button/Value Caps 的 DataIndex 映射；能力缺失或 parser 拒绝报告时才回退固定偏移 |
| P1-2 | 原始采集仍先解析 Usage，再决定是否入队 | `drop_unmapped` 仍经过当前解析分支；未知长度或未知布局可能直接漏报 | 待增加独立 raw-capture 分支，先保存原始报告，再由 Python 解析 |
| P1-3 | 报告缓冲区上限为 64 字节 | 本机 `COL05` 的 `InputReportByteLength=256` 已由 `HidP_GetCaps` 确认 | 当前只过滤 COL02/COL03；扩展到 COL05 前必须扩大 ABI 和队列事件缓冲区 |
| P1-4 | 字段规则只支持 1/2 字节，不能表达任意 bit field、数组和多个 Report ID | 当前 `T1BRIDGE_FIELD_RULE` 使用 byte offset/byte length | 待以 HID capability/data index 模型重做字段规则 |
| P2-1 | 用户态不能直接发送内核专用 `IOCTL_HID_GET_REPORT_DESCRIPTOR` 获取原始 Descriptor | 过滤器经普通 `WdfIoTargetSendIoctlSynchronously` 查询，COL02/COL03 均返回 Win32 `1`，并记录 `0xC0000010 (STATUS_INVALID_DEVICE_REQUEST)` | 已移除用户态错误路径；原始 Descriptor 在当前 T1 `mshidumdf` 下层路径不可用，保留 `descriptor_unavailable`，不把失败当作成功 |
| P2-2 | 需要按 HID 官方顺序取得 Collection 的 preparsed data | 两个 Collection 均返回 268 字节；`HidP_GetCaps` 成功，COL02 输入报告 3 字节、COL03 输入报告 2 字节 | 已按官方顺序完成并现场验证；后续可用 opaque preparsed data 调用 `HidP_` 例程，不把它当作原始 Descriptor |
| P2-3 | Raw Input 不能作为 Consumer/System Control 的唯一输入源 | 当前 Raw Input 只列出 T1 的 COL01/COL04/COL05；COL02/COL03 不在列表中 | 已确定：Consumer/System 采用驱动队列路径，Raw Input 仅作辅助观察 |
| P1-5 | 控制面权限过宽 | 控制设备 SDDL 给 `BU` 读写权限，所有自定义 IOCTL 又使用 `FILE_ANY_ACCESS` | 未改；正式发布前应按受信任服务/管理员模型收紧 ACL，并为读写操作区分 Access bits |
| P1-6 | 报告和事件能力仍有限定 | 字段规则只支持 byte-aligned 的 1/2 字节值，事件报告上限 64 字节，队列容量 64；溢出会丢事件 | 未改；当前只针对 T1 COL02/COL03 夹具使用，扩展 Collection 前必须重新设计 ABI |
| P1-7 | INF 与实际 HID/UMDF 栈的架构边界未完全证明 | 当前设备栈显示 `mshidumdf`，驱动包可加载，但尚未用官方 minidriver/filter 安装模型完成独立验收 | 未改；需要基于目标栈确认 `MsHidKmdf`/UMDF 依赖和过滤器位置，不能只凭服务 Started 判定正确 |

## 2. 已按官方模型补上的部分

### 2.1 Read 请求

当前过滤器默认队列已注册 `EvtIoRead`，使用 `WdfRequestFormatRequestUsingCurrentType`、`WdfRequestSetCompletionRoutine` 和 `WdfRequestSend` 将请求转发到下层，并复用完成回调读取报告缓冲区。

`IOCTL_HID_READ_REPORT` 走持续 Read 报告完成处理；`IOCTL_HID_GET_INPUT_REPORT` 和 `IOCTL_UMDF_HID_GET_INPUT_REPORT` 走专用完成回调。后两者通过 `IRP->UserBuffer` 提供 `HID_XFER_PACKET`，完成回调从 `reportBuffer` 取得报告，不能把嵌入指针当成持续 Read 的 WDF 输出缓冲区。

### 2.2 睡眠和设备移除

当前队列已注册 `EvtIoStop`：

- 每个下发请求先加入受自旋锁保护的 `WDFCOLLECTION`；完成回调移除集合项。
- `WdfRequestStopActionPurge` 时在锁内确认请求仍在集合，再临时增加请求引用后调用 `WdfRequestCancelSentRequest`。
- Suspend 等待场景调用 `WdfRequestStopAcknowledge`。
- 使用 `WdfRequestStopAcknowledge(FALSE)` 的两个队列均注册 `EvtIoResume`。

这对应 KMDF 对电源管理队列中“已转发请求”的处理要求。

### 2.3 同步查询与 ABI 边界

- 控制队列运行在 `PASSIVE_LEVEL`，Report Descriptor 和 preparsed data 查询使用固定 5 秒相对超时。
- C 桥接层检查 `DeviceIoControl` 的最小返回长度；Descriptor/preparsed data 还检查返回头、Collection、长度字段和实际返回字节数。
- Python 桥接层检查所有固定 ABI 输出的 `size`、`abi_version` 和 Collection 范围，拒绝不完整响应。

现场验证的 preparsed data 只作为当前打开句柄生命周期内的 HID parser 输入：COL02、COL03 各 268 字节，`HidP_GetCaps` 返回成功（`0x00110000`）。COL02 为 Usage Page `0x0C`/Usage `0x01`、输入报告 3 字节；COL03 为 Usage Page `0x01`/Usage `0x80`、输入报告 2 字节。新驱动会在桥接查询成功后缓存该 opaque 数据及输入 Button/Value Caps，Read 完成回调用 `HidP_GetData` 解码唯一非零 DataIndex；按钮型报告再兼容调用 `HidP_GetUsagesEx`，多控制或 parser 错误时保留兼容回退。

### 2.4 DataIndex 与 Usage 的安全映射

`HidP_GetData` 返回当前报告中处于活动状态的 `DataIndex` 和原始值；`HidP_GetButtonCaps` 提供按钮能力及其 DataIndex/Usage 范围。Microsoft 定义范围型能力的两个范围按顺序一一对应，因此 Python 侧新增 `describe_input_data()`，只按该关系生成 Usage 候选。找不到候选或出现多个候选时保留空集合或全部候选，不猜测字节偏移、Report ID 或业务名称。

用户态辅助层仍用于现场证据；内核已把 parser 结果接入 `T1FilterShouldBlockReport` 的源 Usage 解码。`T1BRIDGE_FIELD_RULE` 的 byte offset 改写 ABI 尚未扩展，改写仍只在既有规则命中时执行。

## 3. 必须遵守的解析原则

Microsoft 的 HID 模型以 Top-Level Collection 的 Report Descriptor 为输入。驱动或应用应取得该 Collection 的 preparsed data，再使用 `HidP_GetUsages`、`HidP_GetUsagesEx`、`HidP_GetUsageValue`、`HidP_SetUsages` 等 HID parser API 读写控制数据。

因此后续实现应遵循：

1. 过滤器先保留完整原始报告和 Report ID。
2. 过滤器按官方顺序通过 `IOCTL_HID_GET_COLLECTION_INFORMATION` 和 `IOCTL_HID_GET_COLLECTION_DESCRIPTOR` 获取 opaque preparsed data；不能把它与原始 Report Descriptor 混用。
3. 过滤器通过 `IOCTL_HID_GET_REPORT_DESCRIPTOR` 从下层 HID minidriver 获取原始 Descriptor；不能从用户态 HID 句柄直接发送这个内核 IOCTL。
4. 在已有 preparsed data 时，过滤器优先使用 `HidP_GetData` 和 Button/Value Caps 的 DataIndex 映射；按钮型报告再使用 `HidP_GetUsagesEx` 兼容解析。Python 同步使用 `HidP_GetData`/`HidP_GetButtonCaps` 记录证据。
5. Python 解析原始 Descriptor，建立 Usage、Report ID、bit offset、bit size、Report count 和 data index 映射，再扩展字段改写规则。
6. 驱动只有在字段规则已经由 Descriptor 或 parser 能力验证后才做阻断或重映射。

当前可用的无 Descriptor 路径是内核 `HidP_GetData` 加 Button/Value Caps 的 DataIndex 映射，以及按钮型报告的 `HidP_GetUsagesEx` 兼容路径；用户态同步保留 `HidP_GetData` 加 `HidP_GetButtonCaps` 证据链。它们可以建立当前报告的 Usage 或 DataIndex 关系，但不能替代原始 Descriptor 的 bit offset 证据。

固定 `Report[1]`/`Report[2]` 只能作为当前真机的临时诊断兼容，不应作为最终协议实现。

## 4. 未闭环项的处理边界

- 原始 Descriptor 查询失败时，保持 `descriptor_unavailable`，不从报告首字节推断 Report ID，也不把业务别名写成标准 HID Usage；已经取得的 opaque preparsed data 只交给 `HidP_` 例程处理。
- 在字段级 capability/data index 和 Report ID 尚未由 HID parser 验证前，不把 byte-aligned 字段规则扩展成通用 HID 改写器。
- 事件队列溢出、设备移除、睡眠恢复和控制面 ACL 仍需分别做压力与权限验收；本地构建通过不等于这些运行时性质已被证明。

## 5. 官方依据

- [Top-Level Collections](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/top-level-collections)
- [Obtaining HID Reports](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/obtaining-hid-reports)
- [Obtaining Preparsed Data](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/obtaining-preparsed-data)
- [HIDP_BUTTON_CAPS structure](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidpi/ns-hidpi-_hidp_button_caps)
- [Data Indices](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/data-indices)
- [HidP_GetData function](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidpi/nf-hidpi-hidp_getdata)
- [Interpreting HID Reports](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/interpreting-hid-reports)
- [HID Architecture](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hid-architecture)
- [Request Handlers](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/request-handlers)
- [Completing I/O Requests](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/completing-i-o-requests)
- [Using Power-Managed I/O Queues](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/using-power-managed-i-o-queues)
- [Synchronizing Cancellation of Sent Requests](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/synchronizing-cancellation-of-sent-requests)
- [Framework Object Collections](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/framework-object-collections)
- [WdfIoTargetSendIoctlSynchronously](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdfiotarget/nf-wdfiotarget-wdfiotargetsendioctlsynchronously)

## 6. 当前执行顺序

1. COL02/COL03 当前已绑定 `oem351.inf`、版本 `18.44.45.314`；系统重启后设备状态为 `OK/Present`，`T1RemoteFilter` 为 `RUNNING`，DriverStore 活动 SYS 与本地 Release 构建产物 SHA-256 一致。
2. 每次只按一个遥控键，保存完整 Read 报告。
3. 将固定偏移解析降级为临时兼容，先实现 raw-capture 入队。
4. 使用已验证的 preparsed data 调用 HID parser 例程取得 Usage/能力，再实现正式字段重映射；原始 Descriptor 仍只作为可选诊断证据。
5. 在扩展到 COL05、Air Mouse 或麦克风前，先把报告缓冲区从 64 字节扩展到覆盖实测报告长度。

## 7. 本轮 2026-09-10 Review 记录

- 已先写并运行回归测试，覆盖 HID 队列分发、控制队列 PASSIVE_LEVEL、下层 target 引用、请求取消生命周期、策略边界和 ABI 响应头。
- 已重建 `native/t1filter` KMDF Release x64 包和 `native/t1bridge` Release x64 DLL；构建、签名、Inf2Cat 和导出符号检查通过。
- 已运行 Python 回归：非 GUI 全量 `252` 项通过；完整套件 `259` 项通过、1 项因本机 Python 3.13 缺少 Tk 文件失败；运行时路径回归确保优先加载含最新导出的 CMake Release 桥接 DLL。
- 已完成真实设备安装和系统重启后的复核；COL02/COL03 当前已绑定 `oem351.inf`、版本 `18.44.45.314`，活动 SYS 与本轮构建产物 SHA-256 一致。
- 新增 `inspect_preparsed_data()`，将桥接返回的 opaque bytes 交给 `HidP_GetCaps`/`HidP_GetButtonCaps`，并以测试覆盖空输入、HIDP 状态和输入能力摘要；不自行解释保留结构。
- 新增 `parse_input_data()`，按 Microsoft 的 `HidP_MaxDataListLength`/`HidP_GetData` 路径读取真实报告的 DataIndex 和值；COL02 `02 21 02` 得到 `545/1`，COL03 `03 01` 得到 `0/1`，释放报告为空。
- 修正了查询 IOCTL 的同步发送方式，使用普通 `WdfIoTargetSendIoctlSynchronously`，并重建驱动与桥接 DLL；系统重启后，COL02/COL03 的 preparsed data 查询各返回 268 字节并通过 `HidP_GetCaps`，原始 Descriptor 查询仍返回 Win32 `1`，对应过滤器记录的 `0xC0000010 (STATUS_INVALID_DEVICE_REQUEST)`。
- 修正了 `GET_INPUT_REPORT`/`UMDF_HID_GET_INPUT_REPORT` 误用 Read 完成回调的问题；这两类请求现在透明转发，源码已重建并安装到 `oem351.inf`，系统重启后活动镜像已确认。
- 最终验证：定向测试 75 项通过，非 GUI 全量测试 252 项通过；完整测试 259 项通过、1 项失败，失败为本机 Python 3.13 缺少 Tk 的 `tk.tcl/menu.tcl`，与业务代码无关。
- 设备现场复核：桥接持续读链路收到 94 条报告并阻断 94 条，丢包 0；单独的 preparsed 能力验证对 COL02/COL03 均成功，原始 Descriptor 查询均被下层拒绝。
- `driverquery` 已确认活动模块路径为 `oem351.inf` 对应的 DriverStore 文件，服务为 `RUNNING`；系统重启已解除此前“驻留镜像是否替换”的疑问。本次没有修改 Usage 映射，也没有把业务别名写成标准 HID Usage。

## 8. 2026-09-11 DataIndex 映射契约

- [x] 先写测试覆盖范围型一一映射、非范围型精确匹配、未知 DataIndex 和多候选 DataIndex。
- [x] 新增 `HidInputButtonMatch`、`HidInputDataDescription` 和 `describe_input_data()`；输入只依赖 `HidP_GetData` 与 `HidP_GetButtonCaps` 的已抽象输出。
- [x] Microsoft 定义的 `DataIndex`/Usage 顺序对应关系已核对；不一致的范围长度会保持为未匹配，不生成猜测结果。
- [x] HID descriptor 定向测试 `8 passed`。
- [x] 非 GUI 全量测试 `258 passed`，GUI 测试单独运行 `8 passed`。
- [ ] 完整套件仍受 Python 3.13 Tk 资源偶发缺失影响；本次为 `265 passed, 1 failed`，失败未进入业务断言。
- [x] 已把 `HidP_GetUsagesEx` 接入内核阻断前的按钮型源 Usage 解码；parser 缓存不可用时保持固定偏移回退。
- [ ] 尚未扩展 `T1BRIDGE_FIELD_RULE` 的 bit/array/多 Report ID 改写 ABI。

## 9. 2026-09-11 parser-first 内核路径

- [x] 过滤器缓存桥接查询得到的 preparsed data，并预分配 `HidP_GetUsagesEx` 的 Usage 列表，避免 Read 完成回调分配内存。
- [x] Read 完成回调在 parser 缓存可用时优先读取唯一活动 Usage；释放报告由每 Collection 活动状态恢复；多 Usage 不猜测。
- [x] Mapping 会话和 Driver Inspector 启动时主动请求 COL02/COL03 preparsed data。
- [x] KMDF Release x64 构建成功，`hidparse.lib` 链接、签名和 Inf2Cat 均通过。
- [x] 已通过 UAC 安装最新包 `oem353.inf`，版本 `09/11/2026 11.39.34.575`；当前 T1 HID 子设备未在 Present 列表中，尚未确认活动镜像；未重启系统。

## 10. 2026-09-11 value control DataIndex 内核路径

- [x] 先写内核契约测试，要求缓存 `HidP_MaxDataListLength`/`HidP_GetData`，并使用 `HidP_GetButtonCaps`/`HidP_GetValueCaps` 建立 DataIndex→Usage 映射。
- [x] 过滤器在 preparsed data 缓存阶段读取输入 Button/Value Caps；范围长度不一致、能力冲突或未知 DataIndex 保持未映射，不猜测 Usage。
- [x] Read 完成回调优先用 `HidP_GetData` 解码唯一非零控制值；多控制、映射缺失或 parser 错误时才回退原有固定报文布局。
- [x] DataIndex、能力数组、preparsed data 均使用 NonPagedPool，并在 Collection 清理和缓存替换时释放；Release x64 构建 0 警告/0 错误，签名和 Inf2Cat 通过。
- [x] Python 定向测试 `59 passed`，内核静态契约测试 `28 passed`。
- [x] 新包已暂存为 `oem354.inf`，版本 `09/11/2026 11.52.24.706`；当前 T1 仍为 `Disconnected`，COL02/COL03 仍显示活动包 `oem351.inf`。
- [ ] 未重启系统、未重载设备栈、未做新的物理按键现场验收；待 T1 重新连接后再核对活动镜像和 DataIndex 解码。

## 2026-09-11 最终构建与 Tk 环境复核

- [x] 重新构建 `native/t1bridge` Release x64 DLL 和 `native/t1filter` KMDF Release x64 包；导出函数完整，MSBuild 0 警告/0 错误，Inf2Cat 无错误/警告，SYS/CAT 签名验证通过。
- [x] 检查 Python 3.13.15 的 Tcl/Tk 文件：`C:\Python313\tcl\tcl8.6` 和 `C:\Python313\tcl\tk8.6` 均存在，所需脚本没有缺失；已为当前用户设置 `TCL_LIBRARY=C:/Python313/tcl/tcl8.6` 和 `TK_LIBRARY=C:/Python313/tcl/tk8.6`。
- [x] 使用官方 Python 3.13.15 安装器按全局安装路径执行 Tcl/Tk 修复；修复事务已结束，文件仍完整。
- [x] 使用明确的 `C:\Python313\python.exe` 完成 Python 回归：非 GUI `255 passed`，GUI `8 passed`，完整 pytest `263 passed`；未改业务代码掩盖 Tk 环境问题。
- [ ] 未再次安装驱动、未重启设备栈、未做新的现场按键验收；这些结论沿用系统重启后的 `oem351.inf`/preparsed data 证据。

## 11. 2026-09-11 现场 parser/DataIndex 验收更新

- T1 重新连接后，COL02/COL03 均为 OK/Present，两者当前绑定 oem354.inf、版本 11.52.24.706；T1RemoteFilter 为 RUNNING，活动 t1filter.sys SHA-256 与本轮 Release 构建产物一致（2F1FFB0D966E7A1D8C6A235D97A0A86A341337BCDF46BED7F42CBDC785EC5204）。
- 使用 enabled=False、lease_required=False 的只读桥接会话重新读取能力：COL02/COL03 preparsed data 均为 268 字节；HidP_GetCaps 输入报告长度为 3/2；Button Caps 为范围型能力，DataIndex/Usage 范围分别为 0–652→0x0000–0x028C 和 0–2→0x0081–0x0083。
- 对仓库已有的 COL02 样本逐一调用 HidP_GetData 并按 Button Caps 映射，0x0221/0x0223/0x0224/0x00E2/0x00E9/0x00EA 均得到唯一候选，DataIndex 分别为 545/547/548/226/233/234。COL03 已有样本 03 01 得到 DataIndex 0、Usage 0x0081；02 00 00、03 00 释放样本没有 active DataIndex。
- 这证明当前已取得样本的 parser/DataIndex 链路可用，不证明全部 14 个实体按键的逐键语义。仍需逐键采集前，不扩展 T1BRIDGE_FIELD_RULE 的 bit/array/multi-Report-ID ABI，也不修改 Usage 映射。
- 本次现场复核只读且未重启系统；原始 Report Descriptor 在当前 mshidumdf 下层仍保持 STATUS_INVALID_DEVICE_REQUEST，继续标记为 descriptor_unavailable。

## 12. 2026-09-11 Power 输入报告清零修复

Microsoft 的 `IOCTL_HID_GET_INPUT_REPORT` 约定要求输入报告首字节的 Report ID 保持不变，报告内容从后续字节返回。过滤器此前在命中拦截策略时把整个报告清零，可能让 HID 栈收到非法的 Report ID。现改为只清零 payload，保留首字节 Report ID；持续 Read 和 GET 输入报告两个完成回调共用这一处理规则。

本轮新增驱动契约测试，定向测试 76 项、完整 Python 测试 278 项通过。KMDF Release 构建、测试签名和 Inf2Cat 均通过，构建产物 `t1filter.sys` SHA-256 为 `D2E02B83A23BEBBDA3F8A71FCA0D261D1D7442A7934BCD9478F7E3FD82FE8E6D`。新包尚未安装，等待现场确认后再更新设备栈。

## 13. 2026-09-11 Power Usage 策略修复

现场报告 `COL03 / 03 01` 经 HID parser 解码为 System Control Usage `0x01:0x0081`（System Power Down）。默认策略此前误写为 `0x01:0x0001`，导致 Power 能进入采集链路，但不能命中驱动拦截规则。现已将 Python 默认策略改为 `0x0081`，并新增桥接策略回归断言。该修复只涉及策略下发值，不需要重建 `t1filter.sys`；主 App 需要重启后才能加载新策略。

捕获页还有一处初始化缺口：它原先没有请求 `COL02/COL03` 的 preparsed data，驱动因此可能继续使用报告 payload 的兼容解码值 `0x0001`。现已在捕获桥接启动前请求并缓存两个 Collection 的 parser 数据；接口不可用时会拒绝进入有效拦截态。相关定向测试 `78 passed`。

## 14. 2026-09-12 Mapping 与内核边界修复

Mapping 会话现也在 START 前准备 COL02/COL03 parser，缺接口、空数据或检查失败直接报错并清理；重连自动 START 前再次查询。dry-run 不要求 parser。这里的就绪只代表初始化完成，不保证每条报告的唯一 Usage 解析。

Power 的 C/Python 兼容解码统一为 0x0081，仅接受已确认的 03 01 与 03 00 布局；释放包优先于残留 Usage，未知位域不猜测。无 parser 的 System Control 字段重映射被拒绝，保留调用方失败后清零报告的既有行为。

GET_INPUT_REPORT 用户发起路径当前返回 STATUS_NOT_SUPPORTED，避免在完成回调访问未验证用户指针；内核包路径保留。属性查询内存归属 Device。此轮修改了内核源码，需要重新编译和验收驱动，与第 13 节仅更新策略的发布要求不同。

未修改 ACL、IOCTL 数值或拦截层。后续权限、会话归属和并发方案见 [optimization-plan.md](optimization-plan.md)。
