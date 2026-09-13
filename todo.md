
# T1 Remote Windows 项目实施清单

## 目标

参考 `HD838A/remote-mic-app` 的业务与技术设计思路，开发适配 `T1-Remote` 的 Windows 原生应用。第一阶段实现可靠的 HID 设备级按键识别、映射和输出；语音链路在确认 T1 的 GATT 音频报文兼容后再接入。技术栈采用 Python + Windows 原生 API，不照搬 macOS 或 Rust/Tauri/Vue 技术栈。

## 已确认的设备事实

- Windows 设备名称：`T1-Remote`。
- BLE HID 服务：`0x1812`。
- 设备标识：`VID=0x620A`、`PID=0x0407`。
- HID Collection：`COL01` 键盘、`COL02` Consumer Control、`COL03` System Control、`COL04` 鼠标、`COL05` Vendor Defined；`COL02/COL03` 的 Top-Level Usage 已由 PnP Hardware ID 确认。
- Raw Input 已发现 T1 的键盘、鼠标和 Vendor Defined 路径；Home/Power 的 `COL02/COL03` 归属已有驱动行为证据，其他按键仍需逐键采集确认。
- 设备还暴露 `AB5E0001-5A21-4F05-BC7D-AF01F617B664` GATT 服务；当前 T1 的 ATVV 特征、能力响应和真实音频帧已完成验证。

## 阶段 A：工程骨架

- [x] 建立 Python 包、Tkinter 主程序和 Windows 平台层。
- [x] 建立 `t1remote.core` 纯 Python 核心、`t1remote.windows` 平台层和独立 `t1_inspector.py`。
- [x] 建立遥控区域 14 键范围模型、T1 路径过滤和脱敏 JSON 采集工具。
- [x] 建立以遥控器正面产品图为中心的 Tkinter 采集窗口。
- [x] 将按下/抬起原始包配对为逻辑操作，同时保留原始包。
- [x] 采集界面禁用 Air Mouse 键；Power 已允许在驱动拦截后进入采集流程。
- [x] 建立 T1 设备身份匹配函数，只接受 `VID_620A`、`PID_0407` 及对应 BLE 实例路径。
- [x] 建立设置、日志、配置文件和错误处理边界。
- [x] 创建 Windows x64 源码包构建、`pytest`/`unittest` 测试和基础安装配置。

## 阶段 B：T1 遥控区域 HID Inspector

- [x] 枚举当前 Raw Input 路径和设备类型，并提供 T1 过滤与脱敏清单；本轮只建立遥控区域按键采集流程。
- [x] 提供只读 HID Collection/Report 能力探测入口；完整报告描述符仍需 HID 子设备可枚举后读取。
- [x] 已实现只捕获流程，并已通过 GUI Raw Input 采集 13 个启用遥控按键的原始 HID Report、Usage Page、Usage、按下/释放状态；Air Mouse 按规则禁用。
- [x] 已实现脱敏 JSON 夹具生成，不保存蓝牙地址和完整设备路径；至少一套真实夹具仍待生成。
- [x] 已实现物理按键观察映射表，未知键保持可观察但不自动注入；13 个启用按键已完成现场逐键确认。
- [x] 采集界面默认禁用 Air Mouse，按键面与 Fn 组合不进入主动标注流程；现场仍需复核无误采集。

## 阶段 C：按键映射 MVP

- [x] 以设备路径为边界监听 T1，不拦截普通物理键盘；当前只处理遥控区域按键事件。
- [x] 合并遥控区域在 Keyboard、Consumer Control、Mouse/Vendor Defined 中可确认的事件。
- [x] 实现单击、释放、按住重复，以及可选双击/长按手势。
- [x] 使用 Windows `SendInput` 输出标准键盘、媒体键和快捷键。
- [x] 定义 Python 到原生拦截桥接 DLL 的固定 ABI 和失败关闭策略。
- [x] 增加驱动原始事件队列和 `T1Bridge_ReadEvent` 桥接接口。
- [x] 增加 ABI v2、会话租约/心跳、诊断统计、事件时间戳、PnP 清理和通用字段规则。
- [x] 增加 T1 `COL02`/`COL03` 设备专属 KMDF 过滤驱动源码、INF 和 WDK 项目文件。
- [x] 完成 KMDF HID 过滤驱动 x64 构建，生成开发测试签名的 `.sys/.inf/.cat` 包。
- [x] 安装测试证书、启用 Windows 测试签名并在真实 T1 上回归设备级拦截（仍需逐键确认默认动作）。
- [x] 映射保存即热加载，异常或非法配置保留旧配置，不产生粘键。
- [x] 提供按键测试、事件计数、最近事件和诊断信息。

## 阶段 D：设置界面与产品化

- [x] 实现托盘常驻、设备状态、按键映射编辑和诊断页面。
- [x] 支持 JSON 配置导入/导出和版本兼容。
- [x] 增加启动自检、设备断开释放、睡眠恢复和单实例保护。
- [x] 增加 Windows 10/11 x64 源码包、ZIP 安装包和 SHA-256 校验；PyInstaller 单文件/目录包依赖本机可选工具。
- [x] 编写真实 T1 测试手册，明确自动化测试和真机测试边界。

## 阶段 E：语音与双模扩展

- [x] 验证 T1 `AB5E0001` 服务的特征 UUID、能力协商、134 字节音频帧格式和 16 kHz 采样率；探测和夹具工具已完成。
- [x] 已实现协议无关的 IMA-DVI ADPCM、PCM 管线和 WASAPI 输出端点，并完成 T1 实机兼容性验证。
- [ ] 验证 T1 的 2.4GHz 接收器是否暴露标准 HID；若是，复用同一映射引擎。
- [ ] 若 Wi-Fi 侧为私有协议，单独建立协议探测和适配边界，不混入 HID 基础路径。

### 2026-09-08 纯软件进度

- [x] 已实现协议无关的 IMA-DVI ADPCM 解码器、PCM 格式校验和有界 PCM 队列。
- [x] 已实现 ADPCM 到 PCM16LE 的纯 Python 管线，输入帧头仍由未来 T1 适配器负责剥离。
- [x] 已增加不覆盖已有文件的 WAV PCM 离线输出端点；WASAPI 输出端点已单独实现，虚拟麦克风仍未接入。
- [x] 已增加可选 sounddevice PCM 输出端点；未自动安装依赖，不宣称虚拟麦克风已完成。
- [x] 已增加基于 sounddevice WASAPI host 设置的 PCM 输出端点；仍不创建虚拟麦克风。
- [x] 已增加 PCM 队列到音频端点的后台输出泵，支持排空、统计和失败关闭。
- [x] 已增加 ATVV UUID、能力响应、命令编码和控制信号的纯 Python 协议层。
- [x] 已增加 ATVV v0.4 134 字节帧重组、IMA-DVI 高 nibble 优先解码和 PCM 队列处理器；T1 版本兼容性仍待确认。
- [x] 已增加虚拟音频线 PCM 路由端点和输入/输出设备枚举；依赖用户预先安装 VB-CABLE、VoiceMeeter 等虚拟音频驱动。
- [x] 已增加可选音频输出端点枚举命令，不连接设备、不保存系统设备路径。
- [x] 已实现 GATT 音频会话状态机，包含连接、发现、协商、流式接收、排空、断开和旧回调隔离。
- [x] 已增加 GATT 通知到 PCM 管线的组合入口，旧 generation 不进入解码器。
- [x] 已增加可选 Bleak GATT 传输适配器和只读服务/特征探测命令，未自动安装 Bleak。
- [x] 已增加真实 T1 语音测试入口：能力协商、MIC_OPEN、音频帧解码和 CABLE Output 回环均通过。
- [x] 已实现 GATT 音频控制器，把服务发现、显式协商门槛、通知订阅和 PCM 管线串联；未猜测 T1 私有命令。
- [x] 已增加限定时长的 GATT 通知帧夹具采集命令，保存服务摘要和脱敏十六进制载荷；未执行私有能力协商。
- [x] 已增加 Raw Input 传输候选探测，区分 BLE HID、USB HID 和未知 HID；2.4GHz 接收器是否属于 T1 仍需现场确认。
- [x] 已扩展 HID 只读探测，读取可用的输入按钮字段能力；完整 Report Descriptor 和逐键硬件夹具仍需现场确认。
- [x] 已增加只读 Report Descriptor 获取；当前会话没有可打开的 T1 HID 子接口时仍无法生成真实描述符。
- [x] 已增加纯 Python Report Descriptor 字段解析，输出位偏移和 Collection 路径；真实 T1 字段仍需现场描述符验证。
- [x] 已增加物理按键观察映射表，保留 Collection、Usage、状态和样本报告；不会自动覆盖动作配置。
- [x] 已增加采集夹具离线验收命令，可输出覆盖报告并按需强制 13 个启用按键全部完成。
- [x] 已增加只含键位、组合键和间隔的安全宏；编辑器支持步骤增删、上下移动和预览，宏不执行命令行。
- [x] 已增加最多 100 个字符的输入文字动作，并支持可选追加回车。
- [x] 映射编辑器支持多个 JSON 配置存档，可加载、修改、保存和另存为。
- [x] 已用当前 T1 实机确认 `AB5E0001/0002/0003/0004`、codec `0x0002`、134 字节帧和端到端 CABLE 音频输出。
- [x] 已将真实语音会话接入主前台“语音测试”页，支持启动、停止、BLE 地址和 PCM 输出状态显示。
- [x] 语音测试保存原始 PCM WAV 到固定文件，并支持在主前台用默认真实音频输出回放最近录音。
- [x] 语音测试页增加实时 PCM 波形，用于区分 T1 未发声和音频输出端点无声。

## 完成标准

- T1 连接后能稳定识别目标 Collection 和物理按键。
- 每个已确认按键的按下、释放各产生一次语义事件。
- 映射输出不会重复触发，也不会影响普通键盘。
- 断开、重连、睡眠恢复和应用退出后不残留按键状态。
- 真实硬件未验证的能力在界面和文档中明确标记为未验收。

## 当前阻塞信息

13 个启用遥控按键的原始 HID 报文和 Usage 映射已经通过 GUI Raw Input 采集确认；Air Mouse 仍按规则不进入主动标注流程。当前 T1 语音已确认传输 ATVV v0.4 音频；后续其他型号仍需单独验证 codec、帧长和控制时序。

设备级拦截的 Python/C DLL 接口、ABI v2、原始事件队列、会话租约、诊断统计、字段规则、KMDF 驱动源码和 x64 开发构建已经完成。当前仍需继续完成未验收 Collection 的逐键复核，并决定是否把运行时解析迁移到 HID parser 能力模型。

## 2026-09-08 本轮执行进度

- [x] 管理员环境已启用 Windows 测试签名；T1 驱动包已存在并应用到 `COL02/COL03`，两个设备状态均为 `Started`。
- [x] 真实桥接 DLL 已启动默认策略并完成心跳；ABI v2、`COL02/COL03` 附着和租约状态正常，当前队列无丢失。
- [x] 已确认的 7 个 Consumer/System Usage 已固化为默认拦截策略；Power、Home、Return、Voice、Mute、音量加减均保留按下/抬起语义。
- [x] 新增纯 Python 输入解码和 `SendInput` 输出封装；Power、Voice、未知 Usage 默认不注入。
- [x] 完成 Key Mapping MVP：版本化 JSON 配置、键盘单键、组合键、HID 特殊功能键、命令行动作、按下/抬起状态机、热加载释放旧动作和运行时输出协调器。
- [x] 提供 `tools/t1_mapping_test.py` 和 `config/t1-key-mapping.json`，可以开始真实遥控器 Key Mapping 测试。
- [x] 完成 Tkinter Key Mapping 配置前台：物理按键选择、四类动作参数表单、动作预览、默认恢复、重载和安全保存；Inspector 已提供启动入口。
- [x] `python -m pytest -q`：66 项通过。
- [x] UI 表单转换、触发器、配置监视和诊断测试加入后，`python -m pytest -q`：82 项通过。
- [x] 已使用真实遥控器逐键采集并确认 13 个启用按键的按下/抬起事件；Windows 默认动作是否全部被驱动阻断仍需单独验收。
- [x] 增加长按、双击、按住重复和配置文件监视。
- [x] 提供 Windows x64 源码包构建脚本、SHA-256 校验文件和构建说明；已生成一次 `-SourceBundleOnly` 验证包。

## 驱动长期规划（待确认后执行）

详细边界和阶段拆分见 [Docs/driver-capability-roadmap.md](Docs/driver-capability-roadmap.md)。驱动侧的会话租约、PnP/睡眠安全边界、诊断、事件时间戳、字段规则和安全重映射已经提前实现；下一轮重点转为真机验收，之后移除 HidHide 依赖。语音保持独立的 Python/GATT/WASAPI 链路，不并入 HID 驱动。

## 2026-09-09 Mapping 实机修复

- [x] 确认捕获页可收到 T1 报文，设备和 Raw Input 路径正常。
- [x] 定位 Mapping 无输入的原因：会话只启动桥接控制通道，没有为 COL02/COL03 建立持续 HID ReadFile 请求。
- [x] 在 `T1MappingSession` 中加入后台 HID 读请求泵；原始报告仍由桥接队列统一交给映射运行时，避免重复输出。
- [x] 捕获页直读在 Mapping 运行期间暂停，Mapping 停止或失败后恢复。
- [x] 定向测试 19 项通过；真实会话验证 `input=12 / mapping=12 / output=12 / errors=0`。
- [x] 命令动作启动失败只记录诊断错误，不再停止 Mapping 输入捕获；补充运行时和会话回归测试。

## 2026-09-09 用户反馈：按键能力与 Mapping 诊断

- [x] 扩展“单键”列表：补齐标点、退格、锁定键、数字区、F13-F24、Win/Alt/Ctrl/Shift 等标准虚拟键，并补充输出测试。
- [x] 扩展媒体/系统键列表：覆盖标准媒体、浏览器、启动、Power/Sleep/Wake 等可由 `SendInput` 发送的虚拟键，并标注不支持的系统功能。
- [x] 允许 Ctrl、Shift、Alt、Win 作为单键动作；组合键继续使用这四个键作为修饰键。
- [x] 在主窗口 Mapping 服务页增加“退出应用”按钮，点击后弹出确认框，再执行完整清理。
- [x] 改进 Mapping 诊断：长按达到阈值时显示“长按触发”，同时保留释放事件，补充 Voice 长按测试。
- [x] 增加 Mapping 鼠标动作类型，至少支持左键、右键、中键按下/抬起；明确 APPS 键与鼠标右键的区别，并把 Menu 默认映射改为鼠标右键。
- [x] 更新相关文档、配置示例和测试，完成定向测试及全量回归。

### 验证

- [x] 定向测试 56 项通过。
- [x] 全量测试 197 项通过。
- [x] `config/t1-key-mapping.json` 可加载，Menu 为 `combo/SHIFT+F10`，Voice 为 `long_press`。

## 2026-09-10 Codex 输入框兼容性修复

- [x] 长按/双击使用驱动 `KeQueryInterruptTime()` 时间戳，修复队列连续消费导致 Voice 长按失效。
- [x] 主前台启动后自动启动 Mapping 服务，避免窗口启动但服务未接管输入。
- [x] 当前活动配置将 Menu 改为 `Shift+F10`，避开 Codex 输入框对合成鼠标右键和 `APPS` 的兼容性差异。

## 2026-09-10 Voice 提前释放修复

- [x] 驱动按 HID Collection 分离活动 Usage 状态，避免 COL03 零报告误清除 COL02 Voice 的长按状态。
- [x] 增加驱动状态隔离回归检查；`t1filter.c` 已通过本机 `cl.exe` 的 WDK 头文件编译检查。
- [ ] 重新构建并安装驱动包后，按住 Voice 直到手指松开，确认诊断只在对应 Voice `up` 报告到达时释放。

## 2026-09-10 HID 报告证据链

- [x] 增加纯 Python HID 报告分类和时序证据模型，记录 Report ID、Usage、原始报告、重复按下及按下到抬起时长。
- [x] 描述符缺失时明确标记 `descriptor_unavailable`，不从原始报告首字节猜测 Report ID，也不把 `0x0C:0x0221` 命名为 Voice。
- [x] HID 探针和 Driver Inspector 已输出描述符状态及 `hid_evidence`；真实 COL02/COL03 描述符仍待现场取得。

## 2026-09-10 下一轮：Driver Inspector 读请求与真机验收

### 当前结论

- [x] 已确认 `T1RemoteFilter` 服务运行，T1 的 COL02/COL03 已附着，桥接会话可显示 `running`、租约有效、Collection 掩码为 `12`。
- [x] 已确认 GUI 采集页的 Voice 报文来自 HID `ReadFile` 直读，现有历史样本为 `COL02`、原始包 `02 21 02`，配对时长约 54–135ms；GUI 时间戳不能单独证明驱动截断了长按。
- [x] 已确认本次 Driver Inspector 空采集不能作为驱动丢包证据：`tools/t1_driver_inspector.py` 只启动桥接会话，没有建立持续 HID `ReadFile` 请求。

### 下一步执行顺序

- [x] 先写测试：为 Driver Inspector 增加 HID 读请求监听器的注入接口，覆盖启动、停止、异常清理和驱动事件读取线程不会重复消费。
- [x] 最小修复 `tools/t1_driver_inspector.py`：在桥接会话启动后创建 `HidInputListener(target_collections=("COL02", "COL03"))`，保持 COL02/COL03 的 ReadFile 请求；退出或异常时先停止监听器，再释放桥接会话。
- [x] 运行 Driver Inspector 定向测试和全量 `pytest`，确认不影响 Mapping 会话、捕获页和单实例清理；全量 233 项通过。
- [x] 按 Microsoft `CancelIoEx`/overlapped I/O 生命周期约定修正 `HidInputListener.stop`：取消后无超时等待读取线程退出，再关闭句柄；不再手动提前触发 I/O 完成事件。
- [x] 重新启动 Driver Inspector，选择 Voice，按住超过 1 秒后松开；已保存包含 `timestamp_100ns`、原始报告和 `hid_evidence` 的采集结果。
- [x] 按内核时间戳判断 Voice：本次 4 组 `COL02` 原始 `02 21 02` → `02 00 00` 均为独立短脉冲，持续 101–123 ms；驱动收到的零报告与对应 Voice 状态一致，当前证据不支持过滤器截断长按。
- [ ] 分 Collection 验收其他按键：COL01 通过 Raw Input 验证方向/Menu/OK，COL02 验证 Home/Return/Mute/音量，COL03 验证 Power，COL04 Air Mouse 按现有规则禁用，COL05 只记录为 Vendor Defined，不绑定业务名称。
- [ ] 若 Driver Inspector 仍无事件，读取桥接统计 `received_reports`、`queued_events`、`dropped_events`、`completion_errors`，并复核当前安装的 `.sys` 与设备栈；在证据明确前不修改 Usage 映射。

### 2026-09-10 链路 Review 结果

- [x] 驱动完成回调按 KMDF 约定从仍未完成的请求对象取得报告缓冲区，只使用 `Params->IoStatus` 读取完成状态和字节数。
- [x] Python 重叠 `ReadFile` 按 Win32 约定传入 `NULL` 字节数指针，统一通过 `GetOverlappedResult` 取得实际长度。
- [x] Driver Inspector 与 Mapping 会话均保持 COL02/COL03 的持续读请求，驱动事件仍只从桥接队列消费一次，避免重复映射。
- [x] 本机 Python 全量回归通过；已使用官方 VS2022 + WDK 10.0.26100 工具链构建驱动和桥接 DLL，Inf2Cat/签名无错误；已用 `pnputil` 暂存驱动包（退出码 3010），等待重启后真机验收。
- [x] 复核补充：普通 HID 读队列和内部 HID 请求队列均注册 `EvtIoStop`，设备移除时统一取消已转发请求；定向测试 36 项、全量测试 233 项通过。

### 完成标准

- [ ] Driver Inspector 能稳定收到 COL02/COL03 原始报告，并在退出时生成不覆盖已有文件的 JSON。
- [ ] Voice 长按的内核时间戳与 GUI 显示结果一致，释放只由对应的 Voice `up` 报告完成；当前设备只提供约 100 ms 短脉冲，需决定是否增加设备专属脉冲聚合策略。
- [ ] 其他按键按 Collection 分别有可复现的采集结果；未知或未验收能力保留 `unknown`，不自动写入映射配置。

### 2026-09-10 Driver Inspector 原始证据

- [x] 保存 [captures/t1-driver-control-voice-20260910.json](captures/t1-driver-control-voice-20260910.json)，包含 8 条事件、内核 `timestamp_100ns`、原始报文和 `hid_evidence`。
- [x] 4 次 `down/up` 持续时间为 123、112、113、101 ms；每次 `up` 都是 `02 00 00`，没有出现超过 500 ms 的连续按下。
- [ ] 不自动把连续短脉冲解释为长按；该策略会改变快速连按的语义，需用户确认后再实现。

## 2026-09-10 HID 官方 Usage 与描述符链路修正

- [x] 按 USB HID Usage Tables 1.7 更正协议结论：`0x0C:0x0221` 是 `AC Search`，T1 的 `Voice` 仅为业务别名；标准 `Voice Command` 是 `0x0C:0x00CF`。
- [x] 移除用户态直接发送内核专用 `IOCTL_HID_GET_REPORT_DESCRIPTOR` 的错误路径。
- [x] 增加过滤器到下层 HID minidriver 的 Report Descriptor 查询 IOCTL，并让 Driver Inspector 保存描述符及其解析证据。
- [x] 新增桥接、驱动静态契约和 Inspector 回归测试；Python 非 GUI 测试 251 项通过，驱动/桥接构建和 Inf2Cat 校验通过。
- [x] 按官方 HID 顺序补齐 `T1Bridge_GetPreparsedData`：过滤器先查询 `HID_COLLECTION_INFORMATION`，再读取 `IOCTL_HID_GET_COLLECTION_DESCRIPTOR` 的 opaque 数据；修复 METHOD_BUFFERED 输入/输出共享缓冲区下的 Collection 保存顺序。
- [x] 补齐桥接/驱动契约测试并重新构建：定向测试 75 项通过；完整 `pytest` 259 项通过；驱动和桥接 DLL 均为 0 警告、0 错误，Inf2Cat 无错误或警告。
- [x] 已安装新驱动包 `oem347.inf`；COL02/COL03 均显示 `Best Ranked / Installed`，当前设备状态为 `Started`。
- [x] 已重启设备栈并重新枚举 COL02/COL03；真实 Report Descriptor/preparsed data 查询返回 Win32 错误码 `1`，描述符证据仍待补齐。
- [ ] 基于真实 Descriptor 复测 Voice 短按/持续按住，确认设备是否发送持续按下状态。

### 2026-09-10 用户复测结果

- [ ] 用户在 16:06 复测 Voice 长按；Mapping 诊断收到 5 组 `COL02/hid` 的 Voice `down/up`，配对时长约 62–125 ms，均小于配置的 500 ms 长按阈值，未产生 `mapping Voice long_press`。
- [x] Python 长按状态机、驱动时间戳路径和会话轮询定向测试通过（44 项）；当前日志不能证明过滤器截断了长按。
- [ ] 仍需用 Driver Inspector 保存带 `timestamp_100ns`、`raw_data_hex` 和 `hid_evidence` 的同一次长按原始证据，再决定是否需要设备脉冲聚合策略；在证据明确前不改变 HID Usage 映射。

## 2026-09-10 KMDF/ABI Review 追加

- [x] 先写并运行回归测试：覆盖转发请求集合、`EvtIoStop` 临时请求引用、`EvtIoResume`、同步 HID 查询超时、桥接输出长度和 Collection 范围。
- [x] 驱动为所有异步下发请求维护受自旋锁保护的 `WDFCOLLECTION`；完成回调移除集合项，设备移除时按 Microsoft 文档临时引用后再取消。
- [x] Report Descriptor/preparsed data 同步查询使用 5 秒非零超时；桥接 C/Python 两侧校验固定 ABI 输出头和可变长度字段。
- [x] 本轮重建 KMDF Release x64 驱动包和桥接 DLL；构建 0 警告/0 错误，Inf2Cat 和签名校验通过。
- [x] Python 非 GUI 回归 251 项通过；完整回归 259 项通过。
- [ ] 本轮新构建包已安装并尝试重载设备栈；未重启系统，真实设备验收尚未完成。

### 2026-09-10 新构建包暂存

- [x] 已用管理员权限将本轮包加入驱动仓库，发布名为 `oem333.inf`。
- [x] 已重启 T1 HID 实例和 BLE 父设备实例，并执行设备扫描；未重启系统。
- [x] T1 父设备和 HID GATT 子设备已重新出现；后续连接后 COL02/COL03 已绑定新包 `oem348.inf`。

### 2026-09-10 唤醒后复核

- [x] 唤醒后的第一次检查仍为 `CM_PROB_PHANTOM`；重新连接后 COL02/COL03 恢复 Present/OK，并切换到 `oem348.inf`。

### 2026-09-10 新驱动现场验收

- [x] 唤醒并重新连接后，COL02/COL03 均绑定 `oem348.inf`，版本 `17.56.11.338`；活动 SYS 与 Release 构建产物 SHA-256 一致。
- [x] 修正 Python 桥接 DLL 搜索顺序，优先加载 `native/t1bridge/build-vs2022/Release/t1bridge.dll`，并补充回归测试。
- [ ] 新版桥接 DLL 已成功加载，但真实 T1 的 Report Descriptor 和 preparsed data 查询均返回 Win32 错误码 `1`；尚未取得真实描述符证据。

## 2026-09-10 查询 IOCTL 修正与最终构建复核

- [x] 先写测试并修正过滤器三处 HID 查询：`IOCTL_HID_GET_REPORT_DESCRIPTOR`、`IOCTL_HID_GET_COLLECTION_INFORMATION`、`IOCTL_HID_GET_COLLECTION_DESCRIPTOR` 使用普通 `WdfIoTargetSendIoctlSynchronously`，不再误用 `WdfIoTargetSendInternalIoctlSynchronously`。
- [x] 重建 `native/t1bridge` Release x64 DLL 和 `native/t1filter` KMDF Release x64 驱动；MSBuild 0 警告/0 错误，Inf2Cat 无错误/警告，SYS/CAT 签名校验通过，桥接 DLL 14 个导出符号存在。
- [x] 定向测试 58 项通过，非 GUI 全量测试 251 项通过。
- [x] 完整 pytest 结果为 258 项通过、1 项失败；失败为本机 Python 3.13 Tk 环境缺少 `C:/Python313/tcl/tk8.6/ttk/defaults.tcl`，测试未进入业务断言。
- [ ] 现场仍未取得真实 Report Descriptor/preparsed data：COL02/COL03 查询继续返回 Win32 错误码 `1`。下一步应记录过滤器内部精确 NTSTATUS，并核查 `WdfDeviceGetIoTarget` 对应的下层 HID target；在证据明确前不修改 Usage 映射。
- [x] 修正 `GET_INPUT_REPORT`/`UMDF_HID_GET_INPUT_REPORT` 与持续 Read 共用完成回调的问题：前者现在透明转发，避免把 `HID_XFER_PACKET` 当作报告输出缓冲区。
- [x] P0-3 修正后的新驱动包已安装为 `oem351.inf`、版本 `18.44.45.314`，COL02/COL03 均为 `OK`；未重启 Windows。
- [ ] 服务级已加载镜像仍需确认：`T1RemoteFilter` 保持 `RUNNING`，状态接口 `last_error` 仍为 `0`，而 Descriptor/preparsed 查询继续返回错误码 `1`；设备级重载可能没有卸载全局控制设备持有的驱动镜像。

### 2026-09-10 oem351 现场复核

- [x] 设备级复核：COL02/COL03 均绑定 `oem351.inf`、版本 `18.44.45.314`，设备状态 `OK/Present`。
- [x] 桥接会话复核：`received_reports=94`、`blocked_reports=94`、`dropped_events=0`、`buffer_errors=0`；持续 HID Read 链路正常工作。
- [ ] `T1Bridge_GetReportDescriptor` 和 `T1Bridge_GetPreparsedData` 对 COL02/COL03 仍返回 Win32 错误码 `1`；新增的精确 `last_error` 仍显示 `0`，需在服务级卸载/系统重启后再次判断。
- [x] `driverquery` 确认活动模块路径为 `oem351.inf` 对应 DriverStore 文件；尝试 SCM 停止 `T1RemoteFilter` 返回 1052，服务仍为 `RUNNING`。
- [ ] 若要证明最新内核镜像实际替换并读取精确 NTSTATUS，需要系统重启，或后续设计可卸载的全局控制设备生命周期；本轮未重启系统。

## 2026-09-10 系统重启后最终复核

- [x] 用户已完成系统重启；COL02/COL03 均为 `OK/Present`，绑定 `oem351.inf`、版本 `18.44.45.314`。
- [x] `T1RemoteFilter` 为 `RUNNING`，`driverquery` 活动镜像路径指向 `oem351.inf` 对应的 DriverStore；活动 SYS SHA-256 与本地 Release 构建产物一致。
- [x] 过滤驱动桥接按普通 `WdfIoTargetSendIoctlSynchronously` 查询 preparsed data：COL02/COL03 均返回 268 字节；用户态 `HidP_GetCaps` 返回 `0x00110000`，COL02 输入报告 3 字节、COL03 输入报告 2 字节。
- [x] 原始 Report Descriptor 查询在两条 Collection 上均返回 Win32 错误码 `1`；过滤器记录的原始 NTSTATUS 为 `0xC0000010 (STATUS_INVALID_DEVICE_REQUEST)`。当前仍标记 `descriptor_unavailable`，没有据此猜测 Report ID。
- [x] 回归测试：核心定向 `75 passed`，非 GUI 全量 `252 passed`，完整套件 `259 passed / 1 failed`；唯一失败为本机 Python 3.13 缺少 Tk 的 `tk.tcl/menu.tcl`，未进入业务断言。
- [ ] 不再继续强行请求当前下层拒绝的原始 Descriptor；下一步使用已取得的 preparsed data 配合 `HidP_` 例程做只读能力/字段验证，再决定字段规则 ABI 是否需要调整。

## 2026-09-10 HIDP parser 接入

- [x] 先写测试，再新增 `t1remote.windows.hid_descriptor.inspect_preparsed_data()`；桥接返回的 opaque bytes 只在内存中临时包装。
- [x] 通过 `HidP_GetCaps` 和 `HidP_GetButtonCaps` 读取报告长度、Report ID、Usage 范围、ReportCount 和绝对/相对属性；不自行解析保留结构。
- [x] HID parser 相关回归：定向 46 项通过；非 GUI 全量 254 项通过。
- [x] 上一次 T1 连接窗口已取得能力证据：COL02 为 Report ID 2、输入长度 3、Usage 范围 `0x0000–0x028C`；COL03 为 Report ID 3、输入长度 2、Usage 范围 `0x0081–0x0083`。
- [ ] 当前 T1 已再次进入 `CM_PROB_PHANTOM`，最新桥接会话返回 `ERROR_NOT_READY (21)`；待用户唤醒/连接后，用新入口复测桥接返回的 preparsed data，并逐键核对 DataIndex 与按下/释放状态。

## 2026-09-11 T1 唤醒后 HIDP DataIndex 复核

- [x] T1 父设备重新出现后，通过管理员 `pnputil /scan-devices` 完成子设备重新枚举；桥接 `attached_collections=12`。
- [x] 新入口现场读取 COL02/COL03 preparsed data，各 268 字节；`HidP_GetCaps`、`HidP_GetButtonCaps` 均成功。
- [x] `HidP_GetData` 解析真实样本：COL02 `02 21 02` → DataIndex `545`/值 `1`，COL03 `03 01` → DataIndex `0`/值 `1`；对应释放报告没有 active DataIndex。
- [x] HID descriptor/parser 定向测试 47 项通过；非 GUI 全量测试 255 项通过；完整测试 262 项通过、1 项失败，失败仍为本机 Python 3.13 Tk 文件缺失。
- [ ] 尚未完成所有物理按键的逐键采集；当前证据只覆盖已有 Voice/Power 样本，不据此扩展通用字段规则或修改 Usage 映射。

## 2026-09-11 最终构建与 Tk 环境修复记录

- [x] 重新构建桥接 DLL 和 KMDF Release x64 包；导出、签名、Inf2Cat 校验通过，构建 0 警告/0 错误。
- [x] 确认 Tcl/Tk 脚本文件实际存在；为当前用户设置 `TCL_LIBRARY`、`TK_LIBRARY`，路径使用 Tcl 原生正斜杠格式。
- [x] 按全局安装路径调用官方 Python 3.13.15 安装器修复 Tcl/Tk；安装事务已结束。
- [x] 使用明确的 `C:\Python313\python.exe` 回归：非 GUI 255 项通过，GUI 8 项通过，完整 pytest 263 项通过。
- [x] Tk 资源读取抖动在安装修复事务结束后消失；未修改测试或业务代码来掩盖环境问题。

## 2026-09-11 HIDP DataIndex 映射契约

- [x] 先写测试，覆盖 `HidP_GetData` 结果与 `HidP_GetButtonCaps` 范围的一一映射、非范围字段、未知 DataIndex 和多候选 DataIndex。
- [x] 新增 `HidInputButtonMatch`、`HidInputDataDescription` 和 `describe_input_data()`；只处理 parser 已返回的 DataIndex、RawValue、Usage 和 Report ID 能力，不自行拆解 opaque preparsed data。
- [x] 核对 Microsoft `HIDP_BUTTON_CAPS` 官方语义：范围型 DataIndex 与 Usage 按顺序一一对应；范围长度不一致时保持未匹配。
- [x] HID descriptor 定向测试 `8 passed`；非 GUI 全量 `258 passed`；GUI 单独测试 `8 passed`。
- [ ] 完整套件本轮为 `265 passed, 1 failed`，唯一失败是 Python 3.13 Tk 资源文件偶发缺失，未进入业务断言。
- [ ] 尚未修改 `T1BRIDGE_FIELD_RULE`、`T1FilterShouldBlockReport` 或内核 Report 改写路径；仍需真实逐键 DataIndex 证据后再决定 ABI 迁移。

## 2026-09-11 parser-first 内核解码

- [x] WDK 已确认 kernel-mode `HidP_GetUsagesEx` 和 `hidparse.lib` 可用。
- [x] 过滤器缓存 `T1Bridge_GetPreparsedData` 返回的 opaque 数据，并预分配 Usage 列表；Read 完成回调优先用 parser 解码唯一活动 Usage。
- [x] parser 返回多个 Usage、错误或尚未缓存时回退原有固定 Report 解码，避免错误选择第一个 Usage。
- [x] Mapping 会话和 Driver Inspector 启动时主动请求 COL02/COL03 preparsed data。
- [x] KMDF Release 构建、签名和 Inf2Cat 通过；定向契约测试 50 项通过。
- [x] 已通过 UAC 安装 parser-first 驱动包 `oem353.inf`（版本 `09/11/2026 11.39.34.575`）；当前 T1 HID 子设备未 Present，尚未确认活动镜像，未重启系统。
- [ ] `T1BRIDGE_FIELD_RULE` 仍保持 byte offset/1-2 字节 ABI，bit field、数组和多 Report ID 改写待后续真实证据。

## 2026-09-11 value control DataIndex 内核迁移

- [x] 先写契约测试，覆盖 `HidP_GetData`、`HidP_MaxDataListLength`、`HidP_GetValueCaps` 和 DataIndex→Usage 映射缓存。
- [x] 过滤器在缓存 preparsed data 时读取输入 Button/Value Caps；范围长度不一致、能力冲突和未知 DataIndex 保持未映射。
- [x] Read 完成回调优先用 `HidP_GetData` 解码唯一非零控制值；多控制、能力缺失或 parser 失败时回退固定 T1 布局。
- [x] 清理 Collection 或替换缓存时释放 preparsed data、Usage/Data 列表和 DataIndex 映射，避免 NonPagedPool 泄漏。
- [x] 内核定向契约测试 `28 passed`，Python parser/mapping/inspector 定向测试 `59 passed`。
- [x] KMDF Release x64 构建 0 警告/0 错误，签名和 Inf2Cat 无错误/警告。
- [x] 新构建包已暂存为 `oem354.inf`（版本 `09/11/2026 11.52.24.706`），未重启系统。
- [ ] T1 当前为 `Disconnected`，COL02/COL03 仍绑定活动包 `oem351.inf`；待重新连接后核对新镜像并进行现场逐键验收。
- [ ] 完成现场验证前不修改 Usage 映射和 `T1BRIDGE_FIELD_RULE` ABI。

## 2026-09-11 现场 parser/DataIndex 验收

- [x] T1 已重新连接；COL02/COL03 均为 OK/Present，当前活动包为 oem354.inf，版本 11.52.24.706；T1RemoteFilter 为 RUNNING，活动 t1filter.sys SHA-256 为 2F1FFB0D966E7A1D8C6A235D97A0A86A341337BCDF46BED7F42CBDC785EC5204。
- [x] 使用 enabled=False 的只读桥接会话复核：COL02/COL03 preparsed data 各 268 字节；HidP_GetCaps 输入报告长度分别为 3/2；Button Caps 的 DataIndex 范围分别为 0–652、0–2。
- [x] 已有真实样本全部通过同一 parser：COL02 的 0x0221/0x0223/0x0224/0x00E2/0x00E9/0x00EA 分别得到唯一 DataIndex 545/547/548/226/233/234；COL03 已验证 0x0081 对应 DataIndex 0。对应释放报告均无 active DataIndex。
- [x] 现场复核没有改变拦截策略、Usage 映射、T1BRIDGE_FIELD_RULE ABI，也没有重启系统。
- [ ] 以上只覆盖仓库中已有的真实报告样本；尚未完成 T1 全部实体按键的逐键按下/释放采集，不能据此声明所有业务按键已验收。

## 2026-09-11 捕获页与托盘稳定性修复

- [x] 捕获页增加默认开启的“拦截原生键位”开关；桥接会话以租约策略读取驱动保存的完整原始报告，关闭后保留 HID 直读。
- [x] Mapping 服务运行时停止捕获桥接和 HID 直读，禁用捕获开关并显示“Mapping 服务运行中，捕获不可用”；服务停止或启动失败后按状态恢复。
- [x] 修复托盘单击不响应、右键菜单结束后消息窗口被关闭、图标句柄过早释放，以及主窗口隐藏后任务栏项消失的问题。
- [x] 新增捕获桥接、托盘消息和最小化行为回归测试；完整 `pytest` 通过 `273 passed`。
- [x] 只读核对 T1 COL02/COL03 为 Started、活动包为 `oem354.inf`，过滤服务为 RUNNING。
- [x] 短时 enabled 桥接自检成功：状态为 running、租约有效，驱动统计 `blocked_reports=115`、`lease_expirations=0`。
- [x] 修复直接执行 `tools\\t1_app.py` 时的 `ModuleNotFoundError`，现在双击/直接执行也能进入最新主 App。
- [ ] 用户仍需在当前已重启的主 App 中实际按一次 Voice/Home/Power，确认目标按键的系统原生动作已被阻断。
- [ ] Windows Computer Use 当前无法连接桌面观察接口，运行态验收依据为进程窗口标题、响应状态和自动化测试；未执行重启或提交/推送。
- [x] 复核并修复桥接开启时被动 Raw Input 与主动报文重复计数；新增去重回归测试。
- [x] 定向测试 40 项通过；非 GUI 全量测试 265 项通过。
- [ ] GUI 测试受当前 Python 3.13 缺失 `C:\Python313\tcl\tk8.6\icons.tcl` 阻塞，未进入业务断言。
- [x] 修复 Mapping 与捕获监听共用进程时 Raw Input 注册被覆盖的问题；Mapping 停止后重新注册捕获窗口，恢复 COL01/Menu 报文接收。
- [x] 新增 Raw Input 生命周期组件及重复启停回归测试；相关定向测试 24 项通过，非 GUI 全量测试 266 项通过。
- [x] 修复 Raw Input 监听器重启时 `RegisterClass` 返回 1410 的窗口类残留问题；Raw Input/捕获/Mapping/托盘定向测试 28 项通过，非 GUI 全量测试 267 项通过。
- [x] 捕获桥接启动后核验驱动 `running + lease_active`；心跳期间重复核验租约，失效时停止显示“拦截已启用”并报告异常。
- [x] 为失效租约增加回归测试；相关定向测试 62 项通过，扩大测试集 97 项通过。
- [ ] 当前主 App 仍需正常重启后现场按 Power；未重启当前进程，避免丢失用户未保存的捕获记录。
- [x] 针对 Power 仍可触发原生动作，新增 `GET_INPUT_REPORT`/`UMDF_HID_GET_INPUT_REPORT` 专用完成回调；不再透明放行这两类输入报告。
- [x] 新 KMDF Release 包构建、测试签名和 Inf2Cat 通过，0 警告/0 错误；驱动契约与桥接相关测试 90 项通过。
- [ ] 新驱动包尚未安装或重载设备栈；待用户确认后现场验证 COL03/Power。
- [x] 用户确认后以管理员权限安装为 `oem355.inf`；COL02/COL03 恢复 `OK`，T1RemoteFilter 为 Running，活动 SYS 哈希与新构建一致。
- [x] 重载 COL03 成功；COL02 返回 50 后恢复 `OK`，未执行整个 Windows 重启。
- [x] 启动最新主 App（PID 1804），等待用户按 Power 做最终原生动作验证。

## 2026-09-11 Power 报告 ID 保留修复

- [x] 现场只读统计确认 Mapping 会话为 `running`、租约有效；已有输入报告均通过 `IRP_MJ_READ`，普通/内部 DeviceControl 计数为 0。
- [x] 按 Microsoft `IOCTL_HID_GET_INPUT_REPORT` 缓冲区约定复核：被拦截输入报告的首字节是 Report ID，不能和 payload 一起清零。
- [x] 先写驱动契约测试，再让 `T1FilterClearReportPayload` 保留 Report ID、仅清零 payload；覆盖持续 Read 和 GET 输入报告路径。
- [x] `tests/test_filter_dispatch_contract.py`、桥接、Mapping、Inspector 定向测试 `76 passed`；完整 `pytest` `278 passed`。
- [x] KMDF Release x64 重建、测试签名和 Inf2Cat 通过，0 警告/0 错误；新 `t1filter.sys` SHA-256 为 `D2E02B83A23BEBBDA3F8A71FCA0D261D1D7442A7934BCD9478F7E3FD82FE8E6D`。
- [ ] 新包尚未安装或重载设备栈；待用户确认后，安装新包并在 Mapping 状态为 running、租约有效时再按 Power 验证。

## 2026-09-11 Power Usage 策略修复

- [x] 根据 `COL03 / 03 01` 的 HID parser 证据确认 Power 源 Usage 为 `0x01:0x0081`。
- [x] 修复 `build_default_interception_policy()` 中误写的 `0x01:0x0001`，改为 `0x01:0x0081`。
- [x] 先更新回归断言；修复前定向测试按预期失败，修复后桥接、输入解码、过滤器契约和驱动采集测试 `73 passed`。
- [ ] 需要重启主 App 使新策略生效，再在驱动状态为 `running`、租约有效时按一次 Power；本轮未重新安装驱动、未重启系统、未提交或推送。

## 2026-09-11 Power parser 初始化修复

- [x] 确认捕获页 `CaptureBridgeSession` 原先没有请求 preparsed data，驱动可能回退到 `COL03` 报告 payload `0x01`。
- [x] 捕获桥接启动前请求并缓存 `COL02/COL03` parser 数据；失败时不显示有效拦截状态。
- [x] 新增捕获桥接初始化回归断言；相关定向测试 `78 passed`。
- [ ] 需要正常重启主 App 后再次按 Power；本轮未重新安装驱动、未重启系统、未提交或推送。

## 2026-09-12 代码评审首批优化

- [x] 更新 Docs/code-logic-review.md：纠正 C-7 容量语义、C-3 耗时未测量和释放包元数据优先级；保留历史审查内容。
- [x] 新增 Docs/optimization-plan.md，记录修改边界、验收和待决事项；同步 microsoft-hid-review.md。
- [x] C-1：GET 报告入口拒绝 UserMode，完成辅助函数先检查来源再解引用；暂不支持用户页锁定路径。
- [x] C-4：属性查询内存以 Device 为父对象。
- [x] C/Python 已确认 Power 兼容报文统一 Usage 0x0081，未知布局不外推；禁用无 parser 的 System Control 改写。
- [x] Mapping 启动与重连均先准备 parser，失败清理；增加顺序、失败及释放语义回归。
- [ ] C-2/C-6：确定管理员 App 或服务代理方案后，修改权限与控制会话归属。
- [ ] C-3/C-5：请求所有权设计、parser 工作区隔离、锁耗时测量和内核并发验证。
- [ ] C-8/L-4/A-1：计数语义、独立 raw-capture 与真机系统动作对照。
- [ ] 原生 MSVC/WDK 编译与设备验收；当前未找到 MSBuild/MSVC，未安装依赖或驱动。
- [ ] 完整测试中的 Tcl/Tk 初始化失败待修复环境后验证；不改无关 GUI 代码。
