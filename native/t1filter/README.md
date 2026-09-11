# T1RemoteFilter

程序说明：T1 `COL02` Consumer Control 和 `COL03` System Control 的设备专属 KMDF 下层过滤驱动能力层。

驱动匹配以下两个硬件 ID：

```text
HID\{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_REV&0000&Col02
HID\{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_REV&0000&Col03
```

驱动不保存 Home、音量等最终业务配置。Python 通过 `T1Bridge_SetPolicy` 运行时下发源 Usage、目标 Usage、字段规则和拦截范围。命中后，驱动把原始报告写入带序号和时间戳的 `T1BRIDGE_EVENT` 队列；没有目标 Usage 时清零报告，有目标 Usage 时按已校验的字段规则或当前 T1 报文布局改写。Python 通过 `T1Bridge_ReadEvent` 读取原始事件并执行更高层的动作映射。

ABI v2 已包含：

- Python 会话心跳租约；租约失效后自动停止吞键，回到 Windows 透传；
- Collection 附着状态、Read/普通/内部 HID 请求路径、完成错误和设备重连统计；
- 通过 `T1Bridge_GetReportDescriptor` 由过滤器向下层 HID minidriver 读取原始 Report Descriptor；
- 通过 `T1Bridge_GetPreparsedData` 按 HID 官方顺序查询 `HID_COLLECTION_INFORMATION`，再读取下层返回的 opaque Collection Descriptor；
- 在桥接查询成功后缓存 preparsed data 以及输入 Button/Value Caps 的 DataIndex 映射，Read 完成回调优先调用 `HidP_GetData` 解码唯一非零控制值；按钮型能力再兼容调用 `HidP_GetUsagesEx`；parser 不可用、映射不唯一或返回多个控制时回退当前 T1 兼容解码；
- Report Descriptor 编译出的字段规则，支持 Report ID、字段偏移、1/2 字节值和基础改写；
- 策略代数、事件时间戳、队列溢出和运行能力查询。

因此，驱动解决的是“能否在 HIDClass 之前拦截和改写”的能力问题，前端 Python 才是最终配置的来源。双击、长按、宏、跨 Usage Page 的业务动作、鼠标手势和应用快捷键仍由 Python 处理。

## 请求生命周期

COL02/COL03 的 HID 默认队列和内部控制队列均使用并行分发；持续输入不会阻塞同一设备的其他 HID 请求。所有转发请求进入设备上下文的 `WDFCOLLECTION`，完成回调负责移除集合项。设备移除时，`EvtIoStop` 在受自旋锁保护的集合中确认请求后临时增加引用，再调用 `WdfRequestCancelSentRequest`，避免完成回调并发删除请求句柄。Suspend 路径使用 `WdfRequestStopAcknowledge(FALSE)`，两个队列都注册了 `EvtIoResume`。

控制设备队列固定运行在 `PASSIVE_LEVEL`，向下层 HID minidriver 查询 Report Descriptor 和 preparsed data 时使用 5 秒非零超时，避免同步查询永久占住控制队列。缓存后的 parser 输入、Usage 列表、Data 列表和 DataIndex 映射位于 NonPagedPool，供 Read 完成回调在 `DISPATCH_LEVEL` 使用。

持续 `IOCTL_HID_READ_REPORT`/`IRP_MJ_READ` 使用 Read 完成回调解析和改写输入报告。`IOCTL_HID_GET_INPUT_REPORT` 与 `IOCTL_UMDF_HID_GET_INPUT_REPORT` 使用独立完成回调，从 `IRP->UserBuffer` 中的 `HID_XFER_PACKET.reportBuffer` 取得报告，再复用同一套 Usage、租约和清零逻辑；它们不能直接复用 Read 回调的 WDF 输出缓冲区契约。

## 构建条件

需要 Visual Studio C++ 驱动工具集、Windows Driver Kit 和匹配版本的 KMDF。当前构建环境已经具备这些组件；原始 Report Descriptor 查询在当前 T1 `mshidumdf` 下层返回 `STATUS_INVALID_DEVICE_REQUEST`，preparsed data 查询可用。value control 的 DataIndex 映射已完成 Release 构建和契约测试；现场重新连接后，`oem354.inf`（版本 `11.52.24.706`）已成为 COL02/COL03 的活动包，已有真实报告样本通过 `HidP_GetData` 与 Button Caps 唯一映射。全实体按键逐键验收仍未完成。

## 安装边界

`t1filter.inf` 只向 T1 的 `COL02`、`COL03` 添加设备级下层过滤器，不修改 HIDClass 全局过滤器。Windows 10 1903 及更高版本使用 INF `AddFilter` 和 `FilterPosition = Lower` 声明过滤位置，确保服务真正附着到 HID/UMDF 栈。

- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/installing-a-filter-driver
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/inf-addfilter-directive

驱动包已经生成 `.sys`、`.cat` 和 INF 安装包，并通过本机测试签名和 Inf2Cat 校验。正式发布仍需要正式代码签名证书。

## 现场 parser/DataIndex 验收更新（2026-09-11）

重新连接后，oem354.inf（版本 11.52.24.706）已成为 COL02/COL03 的活动包；已有真实报告样本通过 HidP_GetData 与 Button Caps 唯一映射。全实体按键逐键验收仍未完成。原始 Report Descriptor 在当前 mshidumdf 下层仍返回 STATUS_INVALID_DEVICE_REQUEST，preparsed data 查询可用。

## Power 输入报告清零

被拦截的 HID 报告保留首字节 Report ID，只清零后续 payload。该处理同时用于持续 `IOCTL_HID_READ_REPORT` 和 `IOCTL_HID_GET_INPUT_REPORT` 完成回调，避免把合法的 Report ID 一起清除。

Power 的真实源 Usage 是 `COL03` 的 System Control `0x01:0x0081`（System Power Down）。默认拦截策略在 Python 桥接层下发该 Usage；不要使用报告 payload 中的 `0x01` 代替 HID Usage。

捕获页启动桥接时必须先请求 `COL02` 和 `COL03` 的 preparsed data，让驱动缓存 `HidP_GetData` 所需的能力映射。缺少这一步时，驱动会回退到当前 T1 报告布局中的 payload 值，Power 的 `0x01` 会与真实 Usage `0x0081` 不一致。
