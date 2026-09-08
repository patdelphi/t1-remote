
# T1 Remote Windows 项目实施清单

## 目标

参考 `HD838A/remote-mic-app` 的业务与技术设计思路，开发适配 `T1-Remote` 的 Windows 原生应用。第一阶段实现可靠的 HID 设备级按键识别、映射和输出；语音链路在确认 T1 的 GATT 音频报文兼容后再接入。技术栈采用 Python + Windows 原生 API，不照搬 macOS 或 Rust/Tauri/Vue 技术栈。

## 已确认的设备事实

- Windows 设备名称：`T1-Remote`。
- BLE HID 服务：`0x1812`。
- 设备标识：`VID=0x620A`、`PID=0x0407`。
- HID Collection：`COL01` 键盘、`COL02` Consumer Control、`COL04` 鼠标、`COL05` Vendor Defined。
- Raw Input 已发现 T1 的键盘、Consumer Control、鼠标和 Vendor Defined 路径。
- 设备还暴露 `AB5E0001-5A21-4F05-BC7D-AF01F617B664` GATT 服务；是否与参考项目的 ATVV 音频协议完全一致，必须通过特征发现和真实报文验证。

## 阶段 A：工程骨架

- [ ] 建立 Python 包、Tkinter 主程序和 Windows 平台层。
- [ ] 建立 `t1remote.core` 纯 Python 核心、`t1remote.windows` 平台层和独立 `t1_inspector.py`。
- [x] 建立遥控区域 14 键范围模型、T1 路径过滤和脱敏 JSON 采集工具。
- [x] 建立以遥控器正面产品图为中心的 Tkinter 采集窗口。
- [x] 将按下/抬起原始包配对为逻辑操作，同时保留原始包。
- [x] 采集界面禁用 Power 键，避免要求触发系统电源键。
- [x] 建立 T1 设备身份匹配函数，只接受 `VID_620A`、`PID_0407` 及对应 BLE 实例路径。
- [ ] 建立设置、日志、配置文件和错误处理边界。
- [ ] 创建 Windows x64 构建、`pytest`/`unittest` 测试和基础安装配置。

## 阶段 B：T1 遥控区域 HID Inspector

- [ ] 枚举 T1 的全部 Raw Input 路径和设备类型，但本轮只建立遥控区域按键采集流程。
- [ ] 读取每个 Collection 的 HID Report Descriptor。
- [ ] 只捕获 14 个遥控区域按键的原始 HID Report、Usage Page、Usage、按下/释放状态。
- [ ] 生成脱敏 JSON 夹具，不保存蓝牙地址和无关设备路径。
- [ ] 逐个物理按键建立遥控区域映射表，未知键保持可观察但不自动注入。
- [ ] 键盘面 45 个按键、Fn 组合和空中鼠标连续移动保持未采集状态。

## 阶段 C：按键映射 MVP

- [ ] 以设备路径为边界监听 T1，不拦截普通物理键盘；当前只处理遥控区域按键事件。
- [ ] 合并遥控区域在 Keyboard、Consumer Control、Mouse/Vendor Defined 中可确认的事件。
- [ ] 实现单击、释放、按住重复，以及可选双击/长按手势。
- [ ] 使用 Windows `SendInput` 输出标准键盘、媒体键和快捷键。
- [x] 定义 Python 到原生拦截桥接 DLL 的固定 ABI 和失败关闭策略。
- [x] 增加驱动原始事件队列和 `T1Bridge_ReadEvent` 桥接接口。
- [x] 增加 T1 `COL02` 设备专属 KMDF 过滤驱动源码、INF 和 WDK 项目文件。
- [x] 完成 KMDF HID 过滤驱动 x64 构建，生成开发测试签名的 `.sys/.inf/.cat` 包。
- [ ] 安装测试证书、启用 Windows 测试签名并在真实 T1 上回归设备级拦截。
- [ ] 映射保存即热加载，异常或非法配置失败关闭，不产生粘键。
- [ ] 提供按键测试、事件计数、最近事件和诊断信息。

## 阶段 D：设置界面与产品化

- [ ] 实现托盘常驻、设备状态、按键映射编辑和诊断页面。
- [ ] 支持 JSON 配置导入/导出和版本兼容。
- [ ] 增加启动自检、设备断开释放、睡眠恢复和单实例保护。
- [ ] 增加 Windows 10/11 x64 构建、安装包和 SHA-256 校验。
- [ ] 编写真实 T1 测试手册，明确自动化测试和真机测试边界。

## 阶段 E：语音与双模扩展

- [ ] 验证 T1 `AB5E0001` 服务的特征 UUID、能力协商、音频帧格式和采样率。
- [ ] 若与参考项目的 ATVV/IMA-DVI ADPCM 兼容，用 Python 实现独立解码模块并接入 WASAPI；性能不足时再下沉为 C/C++ 扩展。
- [ ] 验证 T1 的 2.4GHz 接收器是否暴露标准 HID；若是，复用同一映射引擎。
- [ ] 若 Wi-Fi 侧为私有协议，单独建立协议探测和适配边界，不混入 HID 基础路径。

## 完成标准

- T1 连接后能稳定识别目标 Collection 和物理按键。
- 每个已确认按键的按下、释放各产生一次语义事件。
- 映射输出不会重复触发，也不会影响普通键盘。
- 断开、重连、睡眠恢复和应用退出后不残留按键状态。
- 真实硬件未验证的能力在界面和文档中明确标记为未验收。

## 当前阻塞信息

需要先取得一轮 T1 每个物理按键的原始 HID 报文，才能确定 `RemoteButton` 和 Usage 映射。语音部分需要另外确认 T1 是否真的传输音频，以及是否完全兼容参考项目的 ATVV 协议。

设备级拦截已经完成 Python/C DLL 接口、原始事件队列、KMDF 驱动源码和 x64 开发构建。当前只完成静态构建、INF 校验和开发签名，尚未安装驱动、启用测试签名或在真实 T1 上验证 Home 等按键是否被阻断。

## 驱动长期规划（待确认后执行）

详细边界和阶段拆分见 [Docs/driver-capability-roadmap.md](Docs/driver-capability-roadmap.md)。执行顺序暂定为：

1. 先完成 `T1RemoteFilter` 真正挂载和 Home/Volume/Voice 的拦截闭环；
2. 增加策略租约、PnP/睡眠恢复、事件通知和诊断；
3. 增加 Report Descriptor 字段规则、多 Usage 和安全重映射；
4. 验收后移除 HidHide 依赖；
5. 语音保持独立的 Python/GATT/WASAPI 链路，不并入 HID 驱动。

这部分需要用户确认后再按清单继续实施。
