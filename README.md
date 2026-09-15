# T1 Remote

T1 Remote 是面向 Windows 的 T1-Remote 无线遥控器助手，当前以 BLE HID 识别、按键采集、映射和设备诊断为主，并保留语音与 2.4G 适配入口。

## 当前能力

- 识别 T1-Remote 的 HID Collection，目标设备为 VID `0x620A`、PID `0x0407`；
- 采集 14 个遥控区域按键的原始报告，并生成按下/抬起逻辑操作；
- 通过 Keyboard、Consumer Control、System Control 和 Mouse Collection 区分输入来源；
- 使用 Python/Tkinter 提供主窗口、托盘、映射编辑和 Inspector；
- 使用 `t1bridge.dll` 与设备专属 KMDF 过滤器处理 `COL02`、`COL03` 的设备级拦截；
- 通过 preparsed data 和 HID parser 解码真实 Usage。Power 的源 Usage 是 `0x01:0x0081`；
- 保留 GATT、ATVV 音频和语音测试模块，未经真机验证的协议不会自动启用。

## 环境

- Windows 10/11 x64；
- Python 3.11 或更高版本；
- 测试：pytest；
- 可选：`pywin32`、`bleak`、`sounddevice`；
- 构建过滤器时需要 Visual Studio C++ 工具集、Windows SDK、WDK 和 MSBuild；
- 构建桥接 DLL 时需要 CMake。

安装 Python 项目及测试依赖：

```powershell
python -m pip install -e ".[test]"
```

## 启动

在项目根目录执行：

```powershell
python tools/t1_app.py
```

也可以使用安装后的入口：

```powershell
t1-remote
```

常用工具：

```powershell
python tools/t1_inspector.py --cli
python tools/t1_driver_inspector.py
python tools/t1_capture_validate.py captures/t1-remote-control.json
python tools/t1_hid_probe.py
python tools/t1_raw_input_probe.py
```

Power、Consumer Control 等目标键由过滤器桥接提供报告。使用捕获页时，必须确认界面显示驱动状态为 `running`、租约有效；捕获桥接会在启动前缓存 `COL02/COL03` 的 preparsed data。设备级拦截依赖已安装并正常附着的驱动，主 App 重启后才会重新下发策略。

## 测试

```powershell
python -m unittest discover -s tests
```

完整测试包含 Tkinter GUI 测试。若本机 Python 的 Tcl/Tk 安装缺少 `tk.tcl` 或 `ttk/ttk.tcl`，GUI 测试会在创建窗口阶段失败；这属于环境问题，不代表业务断言失败。

## Windows Release

完整发行包包含 App、`t1bridge.dll`、HID 过滤驱动、安装/卸载脚本、启动入口和 SHA-256 清单。构建与安装说明见 [Docs/release.md](Docs/release.md)：

```powershell
pwsh -File .\tools\build_release.ps1 -PythonPath C:\Python313\python.exe
```

本机缺少 PyInstaller 时，可用 `-SourceApp` 生成需要 Python 环境的源码 App 包。

## 发布包安装（最终用户）

发布包是带时间戳的 ZIP（如 `dist/T1Remote-v0.2.0-win-x64-20260915-140114.zip`），**不需要 Python 环境**，直接给最终用户安装。

### 所需条件

- Windows 10/11 x64；
- 管理员权限（驱动安装必需）；
- 驱动签名：正式包自带有效签名，可直接安装；测试签名包需在目标机器导入测试证书并开启测试签名；
- 语音功能可选：发布包 `VBCable/` 目录自带 VB-CABLE 安装器时，可一并安装虚拟声卡。

### 安装步骤

1. 解压 ZIP 到任意目录；
2. 右键 `Install-T1Remote.ps1` →「使用 PowerShell 运行」，或管理员 PowerShell 执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1
   ```

3. 脚本先打印项目说明和环境检测（系统、权限、驱动签名、App/VB-CABLE 状态），再显示安装项菜单：
   - `1` HID 过滤驱动　`2` App 本体　`3` VB-CABLE 虚拟声卡　`4` 全部默认　`0` 退出；
   - 直接回车=全部默认，或输入 `1,2` 等数字多选；
4. 逐项安装完成后打印汇总；驱动如提示 `pnputil` 返回码 3010，需重启 Windows 后驱动才加载；
5. 从开始菜单「T1 Remote」启动 App。

### 无人值守安装

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1 -NonInteractive
```

### 卸载

```powershell
powershell -ExecutionPolicy Bypass -File .\Uninstall-T1Remote.ps1
```

卸载会删除已安装的驱动、App 文件和开始菜单快捷方式，并恢复安装时备份的电源按钮设置。

### 语音测试（可选）

语音功能需要 VB-CABLE 虚拟声卡。安装完成后在 App 的「语音测试」页启动会话，**目标应用（录音机、微信等）的麦克风选择 `CABLE Output`** 即可接收 T1 的语音；`CABLE Input` 是 T1 语音写入端，本程序自动选择。

## Windows 原生组件

构建和安装边界见 [Docs/build-windows.md](Docs/build-windows.md)。过滤器源码和接口说明见 [native/t1filter/README.md](native/t1filter/README.md)。原生组件安装需要管理员权限，测试签名、设备重载和系统重启必须单独确认。

Power 的 HID 解析、驱动队列和现场证据见：

- [Docs/microsoft-hid-review.md](Docs/microsoft-hid-review.md)
- [Docs/hid-collection-analysis.md](Docs/hid-collection-analysis.md)
- [Docs/hid-evidence.md](Docs/hid-evidence.md)
- [Docs/device-interception-design.md](Docs/device-interception-design.md)

## 目录

| 目录 | 内容 |
| --- | --- |
| `tools/` | 主 App、Inspector、映射和诊断命令 |
| `t1remote/core/` | 设备报文、映射、音频和业务状态 |
| `t1remote/windows/` | Win32、HID、GATT、WASAPI 和桥接实现 |
| `native/t1bridge/` | 用户态桥接 DLL 和共享 ABI |
| `native/t1filter/` | T1 设备专属 KMDF HID 过滤器 |
| `tests/` | Python 回归测试和原生接口契约测试 |
| `Docs/` | 需求、设计、构建、验收和现场证据 |
| `captures/` | 脱敏的 HID/GATT 采集夹具 |

## 限制

- 当前只针对已确认的 T1-Remote 设备和 `COL02/COL03` 做设备级拦截；
- `COL01` 键盘面、`COL04` 鼠标和 `COL05` Vendor Defined 报文不自动加入按键映射；
- 原始 Report Descriptor 在当前 `mshidumdf` 下层不可用时，代码只使用已验证的 preparsed data，不凭猜测扩展字段规则；
- 语音、2.4G、完整 14 键逐键验收和正式驱动签名仍需目标设备现场验证。
