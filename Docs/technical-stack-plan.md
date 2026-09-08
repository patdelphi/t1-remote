
# T1 Remote Windows 技术栈规划

## 结论

本项目针对 Windows 和当前 T1 硬件，推荐采用 Python-first、Windows 原生 API 兜底的技术栈：

- 主应用：Python 3.13 + Tkinter + 系统托盘；
- 业务层：纯 Python 模块，保持 Windows 无关；
- Windows 平台层：`pywin32`、`ctypes`、WinRT Python bindings；
- BLE/GATT：仅在语音服务验证通过后引入 `bleak`；
- 诊断工具：独立的 Python Inspector；
- 完整设备级拦截：后续按需增加 C++/KMDF 过滤驱动或虚拟 HID 驱动；
- 安装包：首版使用 PyInstaller one-folder 或 MSIX；
- 配置：标准库 `json` 写入 `%LOCALAPPDATA%\T1Remote`，不引入数据库。

参考 macOS 项目的业务结构、设备生命周期、按键手势和语音会话设计，不沿用其 Swift 技术，也不把已有 Windows 参考项目的 Rust/Tauri/Vue 作为本项目的固定技术栈。当前机器已有 Python 3.13 和 `pywin32`，可以直接开始 Inspector 和用户态 MVP 验证。

## 为什么 Python 合适

T1 首版的核心工作是监听 Windows 输入设备、读取 HID 信息、注入键鼠事件、管理托盘常驻和保存映射配置。这些能力可以通过 `pywin32`、`ctypes` 和少量 WinRT bindings 调用；Python 也适合快速迭代 T1 报文解析和按键映射规则。

推荐 Python 的原因：

- `pywin32` 提供 Windows API 和 COM 访问能力；
- `ctypes` 可以补齐 Raw Input、HID、`SendInput` 等没有高层封装的结构体和函数；
- Tkinter 随官方 Python Windows 发行版提供，足够承载首版设置页和 Inspector；
- 标准库可以完成配置、日志、线程队列和大部分测试，依赖数量少；
- 未来增加 C++ 驱动时，Python 主应用可以通过命令行、命名管道或 DLL 接口对接。

Python 的边界也很明确：Raw Input 只有观察能力，`SendInput` 只能生成输出；如果验收要求 59 键完全吞键且任何应用都不泄漏原始输入，仍需 C++/KMDF 或虚拟 HID 方案。Python 适合先确认报文、映射和产品交互，不能替代内核级输入边界。

如果目标从第一天就是大规模交付、完整驱动拦截和高复杂度设置界面，再选择 C++ Win32/WinUI 或 .NET 会更稳。当前 T1 仍处于硬件报文和语音链路验证阶段，Python 的收益更大。

## 轻量化取舍

推荐按下面的依赖边界开始：

| 能力 | 首选 | 引入时机 |
| --- | --- | --- |
| UI | Tkinter/`ttk` | 首版就有 |
| Win32、托盘、COM | `pywin32` | 首版就有 |
| Raw Input、HID、`SendInput` 补充 API | 标准库 `ctypes` | 首版就有 |
| BLE/GATT | `bleak` | 确认语音服务后 |
| 音频验证 | `sounddevice` | 语音阶段 |
| 测试 | `unittest`，必要时 `pytest` | 首版就有 |

这样可以先用 Python 标准库加当前环境已有的 `pywin32` 开始工作，不为语音、2.4G 或复杂 UI 提前安装依赖。Tkinter 是 Python 在 Windows 上的标准 GUI 接口，`pywin32` 提供 Windows API 和 COM 访问；Bleak 的 Windows 后端基于 WinRT，适合放在 BLE 语音验证阶段。

Python 源码开发轻量，打包后的体积则取决于解释器和依赖。首版优先使用 PyInstaller one-folder，便于诊断和更新；确认功能稳定后再评估 one-file 或 MSIX。不要把“单文件”当作“最小体积”，也不要为了体积过早压缩调试信息。

## 解决方案结构

```text
t1-remote/
├── t1remote/core/                 事件模型、手势、映射、协议解码
├── t1remote/windows/              Raw Input、HID、GATT、SendInput、音频
├── t1remote/ui/                   Tkinter 设置窗口和托盘菜单
├── tools/t1_inspector.py          HID/Raw Input/GATT 诊断与报文采集
├── tests/                         无硬件单元测试和报文夹具回放
├── fixtures/                      经脱敏的 T1 报文夹具
└── driver/                        可选 C++/KMDF 驱动，独立生命周期
```

主应用保持普通用户权限。驱动、安装器和语音虚拟音频设备都不能成为普通按键映射的硬依赖。UI、设备会话和解析逻辑通过线程队列通信，避免在 Windows 回调中执行耗时工作。

## 各层职责

### `t1remote.core`

只包含可在任意平台运行的逻辑：

- `InputEvent`、`DeviceCollection`、`TransportKind` 等统一事件模型；
- 单击、双击、长按、重复和释放状态机；
- 语义按键到键盘、媒体键、鼠标动作和快捷键的映射；
- HID 报文解析器和已确认的 T1 报文夹具；
- ATVV/ADPCM 解码器，只有在 T1 真实报文确认兼容后启用；
- 配置版本迁移、输入校验和失败关闭策略。

核心层不依赖 Tkinter、WinRT、Windows 句柄、注册表或第三方输入法。

### `t1remote.windows`

#### HID 与 Raw Input

- 用 `ctypes` 调用 `RegisterRawInputDevices` 注册键盘、Consumer Control、鼠标和 HID 输入；
- 独立 STA 线程创建隐藏消息窗口，处理 `WM_INPUT` 和设备到达/移除消息；
- `GetRawInputDeviceInfo` 获取设备类型、路径和预解析数据；
- `HidD_*`、`HidP_*`、SetupAPI 读取 HID 能力、Report Descriptor 和 Usage；
- 只匹配 T1 的 BLE HID 路径：`VID=0x620A`、`PID=0x0407`；
- 2.4G 接收器先通过 Inspector 识别，再作为独立的 transport profile 加入；
- 日志只保存脱敏的 Collection、Usage 和计数，不保存蓝牙地址、完整设备路径或无关设备信息。

Windows Raw Input 能提供来源设备和原始报告，但它本身不负责阻止普通系统输入，因此“监听到”与“拦截成功”必须分开设计。微软文档说明了 `WM_INPUT`、`RAWHID` 和 `RegisterRawInputDevices` 的使用方式。

#### BLE/GATT 语音层

- 首选 `bleak` 的 Windows WinRT backend 获取已配对 T1；必要时用 `winrt` Python bindings 直接调用 `Windows.Devices.Bluetooth`；
- 使用 GATT service、characteristic 完成服务和特征发现；
- 订阅通知前写入 CCCD，并在 `ValueChanged` 中只投递轻量数据；
- 用会话 generation 丢弃断连后的旧回调；
- 连接、发现、能力协商、流式接收、排空、断开和重连由单一会话状态机管理；
- 普通 HID 按键与 GATT 语音会话分开，语音失败不能关闭按键映射。

Windows GATT API 能完成服务发现、特征读写和通知回调，但 T1 的自定义音频语义仍需由项目自己验证。微软的 GATT Client 文档也明确要求应用先确定目标服务、特征和数据解释方式。

#### 按键输出

- MVP 使用 `SendInput` 生成键盘、媒体键、鼠标点击和鼠标移动；
- 统一批量提交、按键释放回滚和注入事件标记；
- 所有输出动作经过映射引擎，不在 Raw Input 回调中直接执行复杂动作；
- 对管理员权限窗口、UIPI 阻断和目标应用拒绝注入提供可见诊断。

`SendInput` 可以串行插入键盘和鼠标事件，但受 UIPI 约束，也不会自动消除已经进入系统的原始事件。因此它适合输出层，不足以单独解决完整设备级吞键。

#### 音频层

- GATT 音频通知进入有界 `Channel` 或环形缓冲区；
- 解析 T1 实际音频帧，确认编码后再启用 ADPCM 解码；
- 首版使用 `sounddevice` 做音频验证；需要精确控制端点时用 `comtypes` 或 `ctypes` 封装 WASAPI COM；
- 先采用共享模式写入用户选择的虚拟音频端点；
- 语音结束时等待缓冲排空，再结束会话；
- 首个版本不自研虚拟麦克风驱动，优先支持用户安装 VB-CABLE。

WASAPI 共享模式适合把应用生成的 PCM 写入用户选择的端点；但要让其他应用把它当成麦克风读取，仍需要一个可见的虚拟音频设备。微软文档区分了 WASAPI 端点、共享模式和音频引擎边界。

### `t1remote.ui` 与设备会话协调

- Tkinter + `ttk` 承载设置页和 Inspector；
- `threading`、`queue.Queue` 管理后台设备会话和 UI 更新；
- 标准库 `json` 保存映射、设备选择和用户偏好；
- `pywin32` 调用 `Shell_NotifyIcon`，或使用极薄的托盘封装；
- 页面包含：设备状态、按键映射、HID Inspector、语音、诊断和关于；
- UI 显示真实状态，不能用“进程已启动”代替“设备已连接”或“语音可用”。

建议主进程保持单实例并驻留用户会话，不创建 Windows Service。输入钩子、托盘和 SendInput 都属于交互式桌面能力，服务进程会增加会话隔离问题。

## 设备识别模型

产品级身份和运输方式分开：

```text
T1Product
├── Bluetooth HID
│   ├── Keyboard Collection
│   ├── Consumer Control Collection
│   ├── Mouse Collection
│   └── Vendor Defined Collection
└── 2.4G Receiver HID（待确认）
```

T1 当前已观察到 BLE HID 的 `COL01`、`COL02`、`COL03`、`COL04`、`COL05`。项目不能假定所有按键都来自 Keyboard Collection，也不能把 Mouse Collection 的移动报告当成按键。

每条事件至少携带：

- transport；
- collection；
- usage page；
- usage；
- 按下/释放或相对移动值；
- 单调时钟时间戳；
- 内存中的设备路径归属。

## 设备级拦截策略

### 第一阶段：用户态兼容模式

Raw Input 负责来源识别，低级键盘/鼠标钩子只在 T1 事件到达后的有限窗口内做归因和抑制。该模式适合：

- Inspector；
- Consumer Control 和 Vendor Defined 按键；
- 不会泄漏原生事件的按键；
- 早期真机验证。

它不能承诺所有键盘面按键在所有应用、管理员窗口和高负载状态下都百分之百无泄漏。

### 第二阶段：可选 KMDF 驱动

如果产品要求 59 键完整改键、原键完全不漏出、普通键盘绝不受影响，应单独评估 C++/KMDF HID 过滤驱动或虚拟 HID 驱动：

- INF 只匹配 T1 的 HID Collection；
- 原始输入过滤和映射在设备边界完成；
- 应用通过稳定 IPC/IOCTL 下发映射；
- 驱动单独签名、安装、卸载和回滚；
- 驱动失败时主应用仍可退回观察模式；
- 不把未经签名或未经审计的驱动二进制放入仓库。

驱动不是第一阶段的前置条件，因为它会引入管理员权限、签名、安装失败恢复和系统崩溃风险。先用 Inspector 和用户态链路确认 T1 报文，再决定是否进入驱动阶段。

## 测试与发布

- `pytest` 或标准库 `unittest`：核心状态机、映射、配置迁移、HID 报文解析；
- Windows 集成测试：设备路径匹配、Report Descriptor 读取、SendInput 计划、GATT 状态机；
- Inspector 生成的 JSON 夹具回放，不把模拟夹具当成真机验收；
- 真机测试覆盖蓝牙、2.4G、键盘面、遥控器面、空中鼠标、翻面、休眠、重连和 Voice 键；
- 用户态 MVP 使用 x64 安装包；
- 含驱动版本使用带 UAC 的安装器、驱动签名和独立卸载流程；
- 交付文档明确哪些功能已经真机验证，哪些只有自动化测试。

## 推荐实施顺序

1. `t1remote.core` 事件模型、映射状态机和测试夹具。
2. `tools/t1_inspector.py` 枚举 T1 Collection、读取 Descriptor、采集按键。
3. `t1remote.windows` Raw Input 监听和设备级来源匹配。
4. Tkinter 映射界面、SendInput 输出和本地配置。
5. 双面键盘、鼠标、Consumer Control 的真机验收。
6. 验证 `AB5E0001` 语音服务和音频报文，再接 WASAPI/VB-CABLE。
7. 根据用户态泄漏测试结果决定是否实现 KMDF 驱动。
