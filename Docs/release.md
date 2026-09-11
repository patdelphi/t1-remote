# T1 Remote Windows Release

## Release 内容

完整发行包由 `tools/build_release.ps1` 生成，目录结构固定为：

| 路径 | 内容 |
| --- | --- |
| `App/` | PyInstaller 单目录 App，或可回退的 Python 源码 App |
| `Native/t1bridge.dll` | 用户态驱动桥接 DLL |
| `Driver/` | `t1filter.inf`、`t1filter.sys` 和目录中的 `.cat` 驱动目录文件 |
| `Install-T1Remote.ps1` | 安装 App、驱动和开始菜单快捷方式 |
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

安装脚本会把 App 放到 `%ProgramFiles%\T1 Remote`，调用 `pnputil` 安装 `Driver` 中的 HID 过滤驱动，并创建开始菜单快捷方式。驱动安装完成后按提示重启 Windows，具体以 `pnputil` 返回码为准。

```powershell
powershell -ExecutionPolicy Bypass -File .\Uninstall-T1Remote.ps1
```

安装脚本默认拒绝无效签名驱动。仅用于开发机的测试签名包可以显式使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-T1Remote.ps1 -EnableTestSigning -AllowUnsignedDriver
```

测试签名会改变系统启动配置，可能要求重启；正式 Release 不应使用这两个参数。

## 发布验收

1. 核对 `release-manifest.json` 的版本和 `app_mode`。
2. 使用 `SHA256SUMS.txt` 校验目录文件和 ZIP。
3. 检查 `Driver/SIGNATURES.txt`，正式包的 `.sys` 和 `.cat` 必须显示有效签名。
4. 在干净的 Windows 10/11 x64 环境解压，先安装驱动，再运行 `Start-T1Remote.bat`。
5. 确认主窗口、托盘、Mapping、HID 捕获和驱动状态显示正常。
6. 记录安装、设备重载、卸载和重启结果；生产部署需单独确认。
