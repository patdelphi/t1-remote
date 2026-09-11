# T1 Windows 设备级输入拦截设计

## 1. 文档信息

- 日期：2026-09-07
- 目标设备：T1-Remote
- 平台：Windows 10/11 x64
- 当前阶段：ABI v2、桥接 DLL 和 KMDF 过滤驱动已完成构建验证；等待最终安装、一次重启和真机拦截回归

## 2. 问题定义

当前 Python 程序使用 Raw Input 读取 T1 的原始输入，并通过 VID/PID 判断来源设备。Raw Input 能提供观察和来源识别，但不能阻止 HIDClass、Shell 或其他应用继续处理同一条输入。

因此，按下 T1 的 Home 键时，Inspector 可以记录 `hid:02:2302`，Windows 仍会把它解释成浏览器主页键。监听回调里增加 `return`、不执行 `SendInput` 或修改 Tkinter 逻辑，都不能从系统输入链路中撤回这条事件。

本项目的拦截目标是：

1. 只匹配 T1 的 `VID=0x620A`、`PID=0x0407`。
2. 只在已确认的 T1 HID Collection 和 Usage 上做过滤。
3. 过滤发生在 Windows HID 输入进入常规键盘/Consumer Control 处理之前。
4. Python 负责配置、状态显示、报文解析和映射输出。
5. 普通键盘、鼠标和其他蓝牙设备不受影响。

## 3. 方案结论

采用三层结构：

```text
Tkinter/Python 主程序
    │ ctypes
    ▼
t1bridge.dll（用户态轻量桥接）
    │ DeviceIoControl / \.\T1RemoteFilter
    ▼
t1filter.sys（KMDF HID 过滤驱动）
    │ 过滤 IRP_MJ_READ / IOCTL_HID_READ_REPORT
    ▼
T1 HID Collection → HIDClass → Windows 应用
```

职责边界：

| 组件 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| Python | UI、设备状态、按键映射、配置、SendInput、日志 | 抢占 HID 读请求 |
| `t1bridge.dll` | 校验 ABI、打开设备、下发策略、读取状态和原始事件队列 | 安装驱动、解析 Windows 消息、全局钩子 |
| `t1filter.sys` | 匹配 T1、读取报告、保存原始事件、按 Python 策略清零或改写命中报告、统计状态 | 最终键位配置、业务 UI、复杂快捷键编排 |
| Windows 安装器 | 安装/卸载/回滚驱动和 DLL | 改写用户映射 |

`t1bridge.dll` 已经按这个边界实现。Python 的 `T1BridgeClient` 通过 `ctypes` 调用导出函数；没有 DLL 或驱动时会抛出明确异常，应用不进入“伪拦截”状态。

## 4. 设备匹配

### 4.1 主匹配条件

驱动初始化和策略下发都强制校验：

- VID：`0x620A`
- PID：`0x0407`
- Collection：当前过滤 `COL02` Consumer Control 和 `COL03` System Control；键盘、鼠标及 Vendor Defined 保持透传

本项目默认只连接一台 T1，因此不增加蓝牙地址选择逻辑。VID/PID 已经足够排除普通键盘和普通 Consumer Control 设备；Collection 用来限制过滤范围，避免误处理 T1 的其他接口。

### 4.2 过滤粒度

策略以 `(Usage Page, Usage, Collection)` 为键：

```text
HidUsage(
    usage_page=0x0C,
    usage=0x223,
    collection="COL02",
)
```

`collection` 为 0 表示同一 Usage Page/Usage 在目标 T1 Collection 中都适用。策略最多 32 项源 Usage 和 32 项字段规则。每项 Usage 可以带一个同一 Usage Page 内的 `mapped_usage`；字段规则支持 1/2 字节字段的重映射或丢弃。固定结构通过 IOCTL 传递，避免 DLL 和 Python 之间引入 JSON 解析依赖。驱动只执行这张运行时策略表，不内置 Home、音量或其他业务键位。

### 4.3 已采集事实与待确认项

当前夹具已经确认：

- Home：Consumer Control，报文签名 `02 23 02`
- Voice（业务别名；HID 官方 Usage 为 AC Search）：Consumer Control，报文签名 `02 21 02`
- Mute：Consumer Control，报文签名 `02 E2 00`
- Volume Plus：Consumer Control，报文签名 `02 E9 00`
- Volume Minus：Consumer Control，报文签名 `02 EA 00`
- Return：Consumer Control，报文签名 `02 24 02`
- 方向键、OK：当前 Raw Input 表现为 Keyboard 事件

驱动真正匹配时应依据 Report Descriptor 得出的 Usage，不应长期依赖 Raw Input 的 `RAWKEYBOARD.VKey` 或固定报文字节。Menu 的当前夹具包含多组键盘和 Consumer Control 记录，需要重新确认 Report Descriptor、Collection 和物理按键标签后才写入生产策略。

Power 由驱动层拦截后允许 Python 采集，避免触发系统电源行为；Air Mouse 继续保持禁用采集，避免把连续鼠标移动混入按键夹具。两者仍需分别验证电源键和鼠标 Collection 的完整生命周期。

## 5. 过滤驱动工作方式

### 5.1 驱动设备接口

过滤驱动提供设备名 `\\.\\T1RemoteFilter`，接受以下 IOCTL：

| IOCTL | 作用 |
| --- | --- |
| `IOCTL_T1FILTER_SET_POLICY` | 设置 VID/PID、Collection 和 Usage 过滤表 |
| `IOCTL_T1FILTER_START` | 启用过滤 |
| `IOCTL_T1FILTER_STOP` | 停止过滤，回到透传 |
| `IOCTL_T1FILTER_GET_STATUS` | 读取运行状态、错误码和丢弃报告计数 |
| `IOCTL_T1FILTER_READ_EVENT` | 读取一条驱动保存的原始 T1 事件 |
| `IOCTL_T1FILTER_GET_CAPABILITIES` | 查询驱动支持的运行时能力（包含报文重映射）和固定容量 |
| `IOCTL_T1FILTER_GET_STATS` | 查询接收、拦截、队列和错误统计 |
| `IOCTL_T1FILTER_FLUSH_EVENTS` | 清空原始事件队列，不改变当前策略 |
| `IOCTL_T1FILTER_HEARTBEAT` | 刷新 Python 会话租约；租约过期后自动停止过滤 |
| `IOCTL_T1FILTER_GET_REPORT_DESCRIPTOR` | 由过滤器向下层 HID minidriver 读取指定 Collection 的原始 Report Descriptor |

共享 ABI v2 定义位于 `native/t1bridge/t1bridge_protocol.h`。结构使用固定宽度整数和 1 字节对齐，Python 与 C 端都携带 `size` 和 `abi_version`，发现版本不一致时立即停止。策略包含会话租约和字段规则；状态包含策略代数、已附着 Collection、租约剩余时间；统计包含透传、完成错误、设备增删和普通/内部请求路径计数。

### 5.2 报文处理

驱动附着到目标 T1 HID Collection 后，处理 HID 内部设备控制请求中的输入报告读取路径：

1. 保存原始 IRP 和完成上下文。
2. 等待下层返回输入报告。
3. 先按已确认的 T1 `COL02`/`COL03` 报告布局解析 Report ID、Usage 和按下/释放状态；字段规则为后续 Report Descriptor 解析结果提供稳定的偏移匹配入口。
4. 命中策略时把原始报告写入固定长度事件队列；没有 `mapped_usage` 时清零报告，有 `mapped_usage` 时按 Python 下发的目标 Usage 改写当前 Consumer Control 或 System Control 报告，再完成请求。这样 Windows 不会继续解释被拦截的源 Usage。
5. 未命中策略时原样完成 IRP，保证其他设备和未配置功能保持正常。
6. Python 通过 `IOCTL_T1FILTER_READ_EVENT` 读取带 `KeQueryInterruptTime` 时间戳的原始 T1 事件，不依赖 HidHide 或 Raw Input 白名单。
7. Python 运行期间每 500ms 发送一次心跳。启用租约时，如果应用崩溃、被强制结束或桥接断开，驱动在超时后自动停止过滤，让 Windows 恢复接收输入。
8. 统计拦截、透传、队列溢出、完成错误、设备变化、租约过期和普通/内部请求路径，供 UI 和诊断日志展示。

活动 Usage 状态按 Top-Level Collection 分开保存。COL03 的零报告不会清除 COL02
Consumer Control（包括 Voice）的活动按键，Voice 释放只由 COL02 对应的零报告结束。

驱动必须处理取消、设备拔出、休眠恢复、挂起 IRP、重复启动和停止竞态。不能在完成回调中执行用户态 IPC，也不能依赖 Python 进程始终在线。

### 5.3 输出链路

过滤驱动提供两种能力：拦截源 HID 报告，或按 Python 下发的规则改写同一 Consumer Control Usage Page 内的目标 Usage。最终键位表和业务动作仍由 Python 配置：

```text
Python 配置：Home(0x223) → Return(0x224)
  → t1filter.sys 保存原始 Home，并把送往 HIDClass 的按下报告改为 Return
  → Windows 只看到目标 Consumer Usage

Python 配置：Home(0x223) → 自定义应用动作
  → t1filter.sys 保存原始 Home，并清零送往 HIDClass 的报告
  → Python 通过桥接事件队列收到原始 Home
  → Python 按业务配置调用 SendInput 或执行应用动作
```

驱动不保存最终配置，也不决定业务动作。跨 Usage Page、键盘组合键、鼠标动作和应用快捷键仍交给 Python；这样改键位时只需要热更新策略，不需要重新编译或重装驱动。

## 6. 运行模式

### 6.1 Observe

当前默认模式：Raw Input 记录 T1 报文，Windows 仍可处理原始事件。用于报文采集和映射确认。

### 6.2 Bridge Prepared

Python 能加载 `t1bridge.dll` 并读取驱动状态，但过滤尚未启动。用于安装后自检和策略验证。

### 6.3 Intercept

驱动已启动，已确认的 T1 Usage 被丢弃，Python 负责输出映射。UI 必须同时显示：

- DLL 是否加载；
- 驱动是否打开；
- 驱动是否运行；
- 当前目标 VID/PID；
- 已过滤 Usage 数量；
- 最近错误和丢弃报告计数；
- 驱动能力、队列深度、策略代数、Collection 状态、租约状态和报告统计。

驱动不在场时不能把 Observe 模式标为 Intercept。应用退出或桥接断开前，必须先发送 STOP，再关闭设备句柄；即使应用未能正常退出，租约也会在超时后自动停止过滤。

## 7. 失败策略和安全边界

- DLL 缺失：保持 Observe 或提示安装驱动，不调用未定义的 Win32 钩子。
- 驱动打开失败：不修改系统注册表，不自动安装驱动，显示管理员/UAC 或签名错误。
- ABI 不匹配：拒绝打开，记录需要的版本和实际版本。
- 策略非法：拒绝下发，保留上一份有效策略。
- 心跳中断：租约过期后自动停止过滤，输入回到 Windows；重新启动应用后再下发策略即可恢复。
- 驱动异常：停止发送新映射，释放按键状态，允许用户回到透传模式。
- 未确认的 Usage：默认不加入拦截表；验收要求“完全不漏出”时，再将 `drop_unmapped` 设为 true，并先完成整套真机回归。
- 驱动卸载：先 STOP，再卸载服务；卸载失败保留可回滚状态，不强制删除正在使用的文件。

## 8. 已执行的代码变更

- `t1remote/windows/driver_bridge.py`
  - 定义 `HidUsage`、`HidFieldRule`、`InterceptionPolicy`、`BridgeStatus`、`BridgeCapabilities`、`BridgeStats`。
  - 固定 VID/PID、最多 32 项 Usage 策略和最多 32 项字段规则。
  - 实现 DLL 加载、ABI 校验、Open/SetPolicy/Start/Stop/Heartbeat/Status/ReadEvent/Capabilities/Stats/FlushEvents/Close 生命周期。
  - 支持 Python 下发的源 Usage → 目标 Usage 运行时重映射，并拒绝同一源 Usage 的歧义配置。
  - DLL 或驱动缺失时返回 `BridgeUnavailable`，不伪装成已拦截。
- `native/t1bridge/t1bridge_protocol.h`
  - 定义 Python、桥接 DLL、过滤驱动共用的 ABI v2、IOCTL、运行时映射字段、租约、能力和统计结构。
- `native/t1bridge/t1bridge.c`
  - 使用 Win32 `CreateFileW` 和 `DeviceIoControl` 实现轻量桥接 DLL。
  - 不引入 .NET、Qt、Tauri 或常驻服务。
- `native/t1bridge/CMakeLists.txt`
  - 提供 MSVC/Windows SDK 下的 DLL 构建入口。
- `tests/test_driver_bridge.py`
  - 使用 Python 假 DLL 验证协议、生命周期和原始事件读取，不依赖真实驱动。
- `native/t1filter/t1filter.c`、`t1filter.h`、`t1filter.inf`
  - 增加 T1 `COL02`/`COL03` 设备专属 KMDF 下层过滤器源码和安装匹配条件。
  - 实现按运行时策略拦截、Consumer/System Usage 改写、字段规则、原始事件队列、会话租约、PnP 清理、能力查询和运行统计。

## 9. 尚未执行的部分

当前机器已经具备 Visual Studio C++ Build Tools、MSBuild、WDK 和测试签名环境。用户态 DLL 已完成构建，KMDF 驱动包已完成构建、签名和 Inf2Cat 校验；本轮 ABI v2 功能已经合入，尚未在本机最终重启后完成设备栈回归。

剩余工作：

1. 安装本轮最终驱动包后重启一次，使设备专属下层过滤器替换当前旧驱动链。
2. 通过 Python 下发拦截策略，验证 Home 不再打开浏览器，检查租约和诊断计数。
3. 验证源 Usage → 目标 Usage 的运行时改写、字段规则，以及清零拦截后的 Python 业务动作。
4. 完成普通键盘、其他蓝牙设备、睡眠、重连、管理员窗口和应用异常退出回归。

重启完成后，日常改键只修改 Python 配置并调用 `SetPolicy`；不需要重新编译、重装或重启。

## 10. 验收标准

### 自动化

- Python 单元测试全部通过。
- 无 DLL 环境下桥接客户端明确失败关闭。
- 非 T1 VID/PID 的策略无法创建。
- ABI 结构大小、版本和 Usage 数量校验通过。

### 真机

- Home 原始 Consumer Control 不再打开浏览器。
- 其他 T1 按键的原始系统行为不泄漏。
- 普通键盘和其他蓝牙设备不受影响。
- 驱动停止后 T1 回到透传，驱动启动后只处理 T1。
- Python 心跳中断后驱动自动停止过滤，输入恢复透传。
- 应用崩溃、退出和蓝牙断开后没有卡住的键状态。
- 过滤计数、错误码、策略代数、Collection 状态、租约和当前模式在 UI/诊断接口中可见。
