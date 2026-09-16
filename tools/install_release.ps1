# 程序说明：安装已解压的 T1 Remote Release 包。
# 脚本默认按交互菜单分步安装驱动、App 和可选的 VB-CABLE 虚拟声卡；
# 指定 -NonInteractive 时退化为静默参数模式，供 CI 或无人值守使用。
# 驱动安装需要管理员权限；脚本不会默认开启 Windows 测试签名。
# VB-CABLE 安装器（VBCABLE_Setup*.exe）由发布者放入发布包 VBCable/ 目录，
# 脚本不联网下载。

[CmdletBinding()]
param(
    # App 与录音文件放在当前用户可写目录，避免 Program Files 的权限限制。
    [string]$InstallRoot = (Join-Path ${env:LOCALAPPDATA} "T1 Remote"),
    [switch]$SkipDriver,
    [switch]$SkipApp,
    [switch]$SkipVbCable,
    [switch]$EnableTestSigning,
    [switch]$AllowUnsignedDriver,
    # 电源键由 T1 接管后，系统电源按钮动作被设为“不采取任何操作”；此开关可跳过。
    [switch]$SkipPowerButton,
    # 跳过交互菜单，使用参数静默安装（默认 false = 交互引导）。
    [switch]$NonInteractive,
    # VB-CABLE 安装器是 GUI 安装包；指定 -Gui 时不带 /S 参数，由用户手动点击完成。
    [switch]$Gui
)

$ErrorActionPreference = "Stop"
$releaseRoot = $PSScriptRoot

Write-Host ""
Write-Host "================================================"
Write-Host "  T1 Remote 安装引导"
Write-Host "================================================"
Write-Host ""
Write-Host "  T1 Remote 是一个 Windows 蓝牙 HID 遥控助手："
Write-Host "  · KMDF 过滤驱动拦截 T1 遥控按键并转给 App"
Write-Host "  · App 负责按键映射、捕获和语音测试"
Write-Host "  · 可选安装 VB-CABLE 虚拟声卡，让其他应用接收语音"
Write-Host ""
Write-Host "  环境要求：Windows 10/11 x64、管理员权限；"
Write-Host "  驱动安装需要签名（正式签名或测试签名）。"
Write-Host ""

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "请以管理员身份运行 Install-T1Remote.ps1。"
    }
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "发布包缺少目录：$Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Recurse -Force
    }
}

function Set-PowerButtonActionDoNothing {
    # 电源键的系统动作在过滤器之外执行，驱动拦不住（2026-09-13 真机验证）。
    # 安装时把它设为“不采取任何操作”，改由 App 的 mapping 决定行为；
    # 原值备份到 ProgramData，卸载时恢复。
    param([string]$BackupPath)

    $subgroup = '4f971e89-eebd-4455-a8de-9e59040e7347'
    $setting = '7648efa3-dd9c-4e3e-b566-50f929386280'
    $schemeLine = (& powercfg.exe /getactivescheme | Out-String)
    $match = [regex]::Match($schemeLine, '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})')
    if (-not $match.Success) {
        Write-Warning "无法确定当前电源方案，跳过电源按钮设置。"
        return
    }
    $scheme = $match.Groups[1].Value
    $regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\' + $scheme + '\' + $subgroup + '\' + $setting
    $current = Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue
    if ($null -eq $current) {
        Write-Warning "当前电源方案没有电源按钮设置，跳过。"
        return
    }

    $backupDirectory = Split-Path -Parent $BackupPath
    New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
    $hasBackup = Test-Path -LiteralPath $BackupPath
    if ($hasBackup) {
        $existing = Get-Content -LiteralPath $BackupPath -Raw | ConvertFrom-Json
        if ($existing.PSObject.Properties.Name -contains 'Restored' -and $existing.Restored) {
            $hasBackup = $false
        }
    }
    if (-not $hasBackup) {
        [pscustomobject]@{
            ACSettingIndex = [int]$current.ACSettingIndex
            DCSettingIndex = [int]$current.DCSettingIndex
            Scheme         = $scheme
            Restored       = $false
        } | ConvertTo-Json | Set-Content -LiteralPath $BackupPath -Encoding utf8
        Write-Host ("已备份电源按钮动作：AC=" + $current.ACSettingIndex + " DC=" + $current.DCSettingIndex)
    }

    & powercfg.exe /setacvalueindex SCHEME_CURRENT $subgroup $setting 0 | Out-Null
    & powercfg.exe /setdcvalueindex SCHEME_CURRENT $subgroup $setting 0 | Out-Null
    & powercfg.exe /setactive SCHEME_CURRENT | Out-Null
    Write-Host "电源按钮动作已设为“不采取任何操作”，Power 键交给 T1 Remote 映射。"
}

function Remove-KeyboardCollectionFilter {
    # 键盘集合（Col01）的过滤器由 INF 声明（Include=keyboard.inf 继承键盘安装
    # 步骤 + AddFilter）。早期版本曾在键盘类设备实例子键手写 LowerFilters，
    # 现代 Windows 的声明式过滤器不读取该值（2026-09-14 实测），这里清理遗留。
    $classRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96B-E325-11CE-BFC1-08002BE10318}'
    $instances = @(Get-ChildItem $classRoot -ErrorAction SilentlyContinue | Where-Object {
        $deviceId = (Get-ItemProperty -Path $_.PSPath -Name MatchingDeviceId -ErrorAction SilentlyContinue).MatchingDeviceId
        $null -ne $deviceId -and $deviceId -match 'HID_DEVICE_SYSTEM_KEYBOARD'
    })
    foreach ($instance in $instances) {
        $current = (Get-ItemProperty -Path $instance.PSPath -Name LowerFilters -ErrorAction SilentlyContinue).LowerFilters
        if ($null -eq $current) {
            continue
        }
        $values = @($current | Where-Object { $_ -ne 'T1RemoteFilter' })
        if ($values.Count -gt 0) {
            Set-ItemProperty -Path $instance.PSPath -Name LowerFilters -Value $values -Type MultiString
        } else {
            Remove-ItemProperty -Path $instance.PSPath -Name LowerFilters -ErrorAction SilentlyContinue
        }
    }
}

function Test-VbCableInstalled {
    # 检测 VB-CABLE 是否已安装：音频端点设备或卸载注册表任一路命中即视为已装。
    $endpoint = Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue |
        Where-Object { $_.FriendlyName -match 'CABLE (Input|Output)' }
    if ($null -ne $endpoint) {
        return $true
    }
    $registry = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall' `
        -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath -Name DisplayName -ErrorAction SilentlyContinue).DisplayName -match 'VB-Audio Virtual Cable' }
    return $null -ne $registry
}

function Find-VbCableInstaller {
    # 在发布包 VBCable/ 目录找 VB-CABLE 安装器；找不到返回 $null。
    $directory = Join-Path $releaseRoot "VBCable"
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        return $null
    }
    $installer = Get-ChildItem -LiteralPath $directory -File -Filter "VBCABLE_Setup*.exe" |
        Select-Object -First 1
    return $installer
}

function Invoke-VbCableInstall {
    # 以管理员运行 VB-CABLE 安装器；GUI 模式由用户手动完成。
    param([System.IO.FileInfo]$Installer)

    if ($null -eq $Installer) {
        return
    }
    $arguments = if ($Gui) { @() } else { @("/S") }
    Write-Host ("运行 VB-CABLE 安装器：{0} {1}" -f $Installer.Name, ($arguments -join ' '))
    if (-not $Gui) {
        $process = Start-Process -FilePath $Installer.FullName -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) {
            Write-Warning ("VB-CABLE 安装器退出码：{0}，请检查是否安装成功。" -f $process.ExitCode)
        }
    } else {
        Start-Process -FilePath $Installer.FullName -ArgumentList $arguments
        Write-Host "请在 VB-CABLE 安装窗口中完成安装，完成后按回车继续……"
        Read-Host | Out-Null
    }
}

function Get-InstallerEnvironment {
    # 收集环境检测结果，供交互菜单展示：系统、权限、签名、已装状态。
    param(
        [System.IO.FileInfo]$DriverInf,
        [bool]$VbCableAvailable,
        [bool]$VbCableInstalled
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    if ($null -ne $osInfo) {
        $lines.Add(("系统：{0}（{1}）" -f $osInfo.Caption, $osInfo.OSArchitecture))
    }
    $lines.Add("权限：管理员（已确认）")

    if ($null -eq $DriverInf) {
        $lines.Add("驱动包：未找到 Driver/*.inf")
    } else {
        $driverSignature = "未签名"
        $driverFiles = @(Get-ChildItem -LiteralPath $DriverInf.DirectoryName -File |
            Where-Object { $_.Extension -in @(".sys", ".cat") })
        if ($driverFiles.Count -gt 0) {
            $signature = Get-AuthenticodeSignature -LiteralPath $driverFiles[0].FullName
            $driverSignature = if ($signature.Status -eq "Valid") { "签名有效" } else { "签名无效/测试签名" }
        }
        $lines.Add("驱动包：$($DriverInf.Name)（$driverSignature）")
    }

    if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
        $lines.Add("App：已安装于 $InstallRoot")
    } else {
        $lines.Add("App：未安装，将复制到 $InstallRoot")
    }

    if ($VbCableInstalled) {
        $lines.Add("VB-CABLE：已安装")
    } elseif ($VbCableAvailable) {
        $lines.Add("VB-CABLE：未安装，发布包已自带安装器")
    } else {
        $lines.Add("VB-CABLE：未安装，发布包未附带安装器")
    }

    if (-not $NonInteractive) {
        $appEntry = Get-ChildItem -LiteralPath (Join-Path $releaseRoot "App") -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -in @("T1Remote.exe", "t1_app.py") } | Select-Object -First 1
        if ($null -eq $appEntry) {
            $lines.Add("App 启动入口：未找到 T1Remote.exe 或 t1_app.py")
        } else {
            $lines.Add("App 启动入口：$($appEntry.Name)")
        }
    }
    return $lines
}

function Show-InstallMenu {
    # 交互式安装菜单：先显示环境检测结果，再选择安装项。
    # 直接回车=全部默认；输入 1,2,3 数字多选；输入 0 或 q 退出。
    param(
        [string[]]$EnvironmentLines,
        [bool]$VbCableAvailable,
        [bool]$VbCableInstalled
    )

    Write-Host "==== 环境检测 ===="
    foreach ($line in $EnvironmentLines) {
        Write-Host "  $line"
    }
    Write-Host ""
    Write-Host "==== 选择安装项 ===="
    Write-Host "  1) HID 过滤驱动（拦截遥控按键，需要管理员权限）"
    Write-Host "  2) App 本体（复制到 $InstallRoot）"
    if ($VbCableAvailable) {
        if ($VbCableInstalled) {
            Write-Host "  3) VB-CABLE 虚拟声卡（已检测到已安装，跳过即可）"
        } else {
            Write-Host "  3) VB-CABLE 虚拟声卡（发布包自带安装器）"
        }
    } else {
        Write-Host "  3) VB-CABLE 虚拟声卡（发布包未附带安装器，跳过）"
    }
    Write-Host "  4) 全部默认安装（驱动 + App + VB-CABLE）"
    Write-Host "  0) 退出安装"
    Write-Host ""
    $selection = Read-Host "请选择安装项（输入 1,2,3 多选；直接回车选 4；0 或 q 退出）"
    if ([string]::IsNullOrWhiteSpace($selection)) {
        return [pscustomobject]@{ Driver = $true; App = $true; VbCable = $VbCableAvailable; Exit = $false }
    }
    $trimmed = $selection.Trim()
    if ($trimmed -in @('0', 'q', 'Q', 'quit')) {
        return [pscustomobject]@{ Driver = $false; App = $false; VbCable = $false; Exit = $true }
    }
    if ($trimmed -eq '4') {
        return [pscustomobject]@{ Driver = $true; App = $true; VbCable = $VbCableAvailable; Exit = $false }
    }
    $numbers = @($trimmed -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return [pscustomobject]@{
        Driver = $numbers -contains '1'
        App = $numbers -contains '2'
        VbCable = $numbers -contains '3' -and $VbCableAvailable
        Exit = $false
    }
}

Assert-Administrator
$driverRoot = Join-Path $releaseRoot "Driver"
$driverInfs = @(Get-ChildItem -LiteralPath $driverRoot -File -Filter "*.inf" | Sort-Object Name)
$driverInf = $driverInfs | Select-Object -First 1
if (-not $SkipDriver -and $driverInfs.Count -eq 0) {
    throw "发布包缺少 Driver/*.inf。"
}
if ($AllowUnsignedDriver -and -not $EnableTestSigning) {
    throw "AllowUnsignedDriver 仅能和 EnableTestSigning 一起使用。"
}

$vbCableInstaller = Find-VbCableInstaller
$vbCableInstalled = Test-VbCableInstalled
$environmentLines = Get-InstallerEnvironment `
    -DriverInf $driverInf `
    -VbCableAvailable ($null -ne $vbCableInstaller) `
    -VbCableInstalled $vbCableInstalled
$menuSelected = [pscustomobject]@{ Driver = $true; App = $true; VbCable = $null -ne $vbCableInstaller; Exit = $false }
if (-not $NonInteractive) {
    $menuSelected = Show-InstallMenu `
        -EnvironmentLines $environmentLines `
        -VbCableAvailable ($null -ne $vbCableInstaller) `
        -VbCableInstalled $vbCableInstalled
    if ($menuSelected.Exit) {
        Write-Host "已退出安装，未做任何更改。"
        exit 0
    }
}

if ($EnableTestSigning) {
    & bcdedit.exe /set testsigning on
    if ($LASTEXITCODE -ne 0) {
        throw "开启 Windows 测试签名失败，退出码：$LASTEXITCODE"
    }
    Write-Warning "已请求开启测试签名；Windows 可能需要重启后驱动才能加载。"
}

if ($menuSelected.Driver -and -not $SkipDriver) {
    $driverFiles = @(Get-ChildItem -LiteralPath $driverRoot -File | Where-Object { $_.Extension -in @(".sys", ".cat") })
    $invalidSignatures = @($driverFiles | Where-Object {
        (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne "Valid"
    })
    if ($invalidSignatures.Count -gt 0 -and -not $AllowUnsignedDriver) {
        $names = ($invalidSignatures.Name -join ", ")
        throw "驱动签名无效：$names；正式包请提供有效签名，测试包需显式指定 -EnableTestSigning -AllowUnsignedDriver。"
    }

    # 同一个 SYS 可能对应多个设备类 INF；逐个安装，确保键盘集合与
    # Consumer/System Control 集合都建立各自的设备栈绑定。
    foreach ($driverInfEntry in $driverInfs) {
        & pnputil.exe /add-driver $driverInfEntry.FullName /install
        if ($LASTEXITCODE -notin @(0, 3010)) {
            throw "pnputil 安装驱动失败（$($driverInfEntry.Name)），退出码：$LASTEXITCODE"
        }
        if ($LASTEXITCODE -eq 3010) {
            Write-Warning "驱动已暂存，Windows 要求重启后加载：$($driverInfEntry.Name)"
        }
        Write-Host "驱动安装完成：$($driverInfEntry.Name)"
    }
    # 键盘集合的过滤器由 INF 声明，这里只清理早期版本手写的遗留值。
    Remove-KeyboardCollectionFilter
    Write-Host "驱动安装完成。"
} else {
    Write-Warning "已跳过 HID 过滤驱动安装。"
}

if ($menuSelected.App -and -not $SkipApp) {
    $installedApp = Join-Path $InstallRoot "App"
    $installedNative = Join-Path $InstallRoot "Native"
    $installedDriver = Join-Path $InstallRoot "Driver"
    Copy-DirectoryContents (Join-Path $releaseRoot "App") $installedApp
    Copy-DirectoryContents (Join-Path $releaseRoot "Native") $installedNative
    Copy-DirectoryContents (Join-Path $releaseRoot "Driver") $installedDriver
    if (-not $SkipPowerButton) {
        Set-PowerButtonActionDoNothing -BackupPath (Join-Path ${env:ProgramData} "T1 Remote\power-button-backup.json")
    }
    Copy-Item -LiteralPath (Join-Path $releaseRoot "Start-T1Remote.bat") -Destination (Join-Path $InstallRoot "Start-T1Remote.bat") -Force
    Copy-Item -LiteralPath (Join-Path $releaseRoot "RELEASE.md") -Destination (Join-Path $InstallRoot "RELEASE.md") -Force

    $startMenuRoot = Join-Path ${env:ProgramData} "Microsoft\Windows\Start Menu\Programs\T1 Remote"
    New-Item -ItemType Directory -Force -Path $startMenuRoot | Out-Null
    $shortcutPath = Join-Path $startMenuRoot "T1 Remote.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $InstallRoot "Start-T1Remote.bat"
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Description = "T1 Remote Mapping"
    $shortcut.Save()

    Write-Host "T1 Remote App 已安装到：$InstallRoot"
    Write-Host "开始菜单快捷方式：$shortcutPath"
} else {
    Write-Warning "已跳过 App 本体安装。"
}

if ($menuSelected.VbCable -and -not $SkipVbCable) {
    if ($null -eq $vbCableInstaller) {
        Write-Warning "发布包未附带 VB-CABLE 安装器，跳过。"
    } elseif ($vbCableInstalled) {
        Write-Host "检测到 VB-CABLE 已安装，跳过。"
    } else {
        Invoke-VbCableInstall -Installer $vbCableInstaller
        Write-Host "VB-CABLE 安装完成。其他应用的麦克风请选择 CABLE Output。"
    }
} else {
    Write-Warning "已跳过 VB-CABLE 虚拟声卡安装。"
}

Write-Host ""
Write-Host "==== 安装汇总 ===="
Write-Host ("  HID 过滤驱动：{0}" -f $(if ($menuSelected.Driver -and -not $SkipDriver) { "已安装" } else { "跳过" }))
Write-Host ("  App 本体：{0}" -f $(if ($menuSelected.App -and -not $SkipApp) { "已安装到 $InstallRoot" } else { "跳过" }))
Write-Host ("  VB-CABLE：{0}" -f $(if ($menuSelected.VbCable -and -not $SkipVbCable) { "已安装" } else { "跳过" }))
if ($menuSelected.Driver -and -not $SkipDriver) {
    Write-Host "  下一步：运行开始菜单中的 T1 Remote 启动 App；语音测试前把目标应用麦克风设为 CABLE Output。"
}
