# T1 Remote Windows Release

## Release 内容

完整发行包由 `tools/build_release.ps1` 生成，目录结构固定为：

| 路径 | 内容 |
| --- | --- |
| `App/` | PyInstaller 单目录 App，或可回退的 Python 源码 App |
| `Native/t1bridge.dll` | 用户态驱动桥接 DLL |
| `Driver/` | `t1filter.inf`、`t1filter.sys` 和目录中的 `.cat` 驱动目录文件 |
| `VBCable/`（可选） | VB-CABLE 虚拟声卡安装器，由 `build_release.ps1 -VbCableInstaller` 或自动扫描打入 |
| `Install-T1Remote.ps1` | 交互引导安装 App、驱动和开始菜单快捷方式，可选手动装 VB-CABLE |
| `Uninstall-T1Remote.ps1` | 卸载驱动、App 和快捷方式 |
| `Start-T1Remote.bat` | 启动 EXE 或源码 App |
| `SHA256SUMS.txt` | 包内文件和 ZIP 的 SHA-256 校验值 |

## 构建

正式 App 包需要 PyInstaller；驱动重编译需要 Visual Studio C++、Windows SDK、WDK、MSBuild 和 CMake。构建脚本会优先使用本次构建产物，工具缺失时复用仓库中已有的 Release DLL/驱动包。

```powershell
pwsh -File .\tools\build_release.ps1 -PythonPath C:\Python313\python.exe
```

当前机器未安装 PyInstaller 时，明确使用源码 App 模式：

```powershell
pwsh -File .\tools\build_release.ps1 -SourceApp -PythonPath C:\Python313\python.exe
```

源码 App 需要目标机器安装 Python 3.11 或更高版本，以及项目的运行依赖。正式交付应生成 PyInstaller 单目录 App，并在目标 Windows 10/11 x64 机器上实测启动。

## 安装和卸载

解压 ZIP 后，在管理员 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1
```

安装脚本默认**交互引导**：先检测环境（管理员、驱动签名、VB-CABLE 是否已装），再分步选择安装项：

1. **HID 过滤驱动**——拦截遥控按键，需要管理员权限；
2. **App 本体**——复制到 `%ProgramFiles%\T1 Remote` 并创建开始菜单快捷方式；
3. **VB-CABLE 虚拟声卡**——可选，发布包 `VBCable/` 自带安装器时可直接引导运行；没打包则提示跳过。

直接回车默认全选，或输入 `1,2` 等数字选择。驱动安装完成后按提示重启 Windows，具体以 `pnputil` 返回码为准。CI 或无人值守使用静默模式：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1 -NonInteractive
```

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\Uninstall-T1Remote.ps1
```

安装脚本默认拒绝无效签名驱动。仅用于开发机的测试签名包可以显式使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1 -EnableTestSigning -AllowUnsignedDriver
```

测试签名会改变系统启动配置，可能要求重启；正式 Release 不应使用这两个参数。

## 语音测试

语音功能需要虚拟声卡：脚本在 `VBCable/` 目录存在安装器时可选择安装 VB-CABLE。安装完成后，在 App 的“语音测试”页启动会话，目标应用（录音机、微信等）的麦克风选择 **CABLE Output** 即可接收 T1 的语音。

## 发布验收

1. 核对 `release-manifest.json` 的版本和 `app_mode`。
2. 使用 `SHA256SUMS.txt` 校验目录文件和 ZIP。
3. 检查 `Driver/SIGNATURES.txt`，正式包的 `.sys` 和 `.cat` 必须显示有效签名。
4. 在干净的 Windows 10/11 x64 环境解压，先安装驱动，再运行 `Start-T1Remote.bat`。
5. 确认主窗口、托盘、Mapping、HID 捕获和驱动状态显示正常。
6. 记录安装、设备重载、卸载和重启结果；生产部署需单独确认。
