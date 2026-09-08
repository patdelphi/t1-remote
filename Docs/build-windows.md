# T1 Remote Windows x64 构建说明

## 目标

构建脚本生成用户态应用、配置、产品图片、可选原生桥接 DLL/过滤驱动构建产物和 SHA-256 校验文件。脚本不安装驱动、不启用 Windows 测试签名，也不修改当前设备。

## 构建前检查

- Windows 10/11 x64；
- Python 3.11 或更高版本；
- Visual Studio/MSBuild、Windows SDK 和 WDK（需要构建过滤驱动时）；
- CMake（需要构建 `t1bridge.dll` 时）；
- `pyinstaller`（需要生成单目录 GUI 包时）。

先执行测试：

```powershell
python -m pytest -q
```

## 生成包

在项目根目录执行：

```powershell
pwsh -File .\tools\build_windows.ps1
```

只生成 Python 源码包：

```powershell
pwsh -File .\tools\build_windows.ps1 -SourceBundleOnly
```

跳过原生组件构建：

```powershell
pwsh -File .\tools\build_windows.ps1 -SkipNative
```

每次构建使用新的时间戳目录，不覆盖已有包，同时生成同名 ZIP 压缩包。输出目录包含 `SHA256SUMS.txt`，安装或交付前应核对目录文件和 ZIP 的校验值。

## 安装边界

本脚本不执行驱动安装、证书安装、测试签名切换、重启或生产部署。驱动安装需要管理员确认，并必须按真实 T1 设备的 Collection 和签名状态单独回归。

## 应用入口

- `t1-remote`：主前台、托盘、会话状态和诊断；
- `t1-remote-mapping-gui`：映射配置前台；
- `t1-remote-inspector`：T1 原始报文采集；
- `t1-remote-mapping-test`：命令行 Mapping 测试。
- `t1-remote-hid-probe`：只读 HID Collection 能力探测。
- `t1-remote-raw-input-probe`：只读 Raw Input 设备清单。
- `t1-remote-transport-probe`：BLE/USB/未知 HID 传输候选清单。
- `t1-remote-capture-validate`：离线验收遥控区域 JSON 夹具。
- `t1-remote-gatt-probe`：只读 GATT 服务和特征探测。
- `t1-remote-gatt-capture`：限定时长采集脱敏 GATT 通知帧。
- `t1-remote-audio-devices`：只读音频输出端点清单。
