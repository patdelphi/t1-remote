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
- Collection 附着状态、普通/内部 HID 请求路径、完成错误和设备重连统计；
- Report Descriptor 编译出的字段规则，支持 Report ID、字段偏移、1/2 字节值和基础改写；
- 策略代数、事件时间戳、队列溢出和运行能力查询。

因此，驱动解决的是“能否在 HIDClass 之前拦截和改写”的能力问题，前端 Python 才是最终配置的来源。双击、长按、宏、跨 Usage Page 的业务动作、鼠标手势和应用快捷键仍由 Python 处理。

## 构建条件

需要 Visual Studio C++ 驱动工具集、Windows Driver Kit 和匹配版本的 KMDF。当前构建环境已经具备这些组件；安装后的设备栈仍需重启一次并完成真机回归。

## 安装边界

`t1filter.inf` 只向 T1 的 `COL02`、`COL03` 添加设备级下层过滤器，不修改 HIDClass 全局过滤器。当前安装使用设备级 `LowerFilters`，用于兼容本机 HID/UMDF 栈并确保服务真正附着。

- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/installing-a-filter-driver
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/inf-addfilter-directive

驱动包已经生成 `.sys`、`.cat` 和 INF 安装包，并通过本机测试签名和 Inf2Cat 校验。正式发布仍需要正式代码签名证书。
