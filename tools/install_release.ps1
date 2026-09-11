# 程序说明：安装已解压的 T1 Remote Release 包。
# 脚本复制 App/Native/Driver 到 Program Files，并用 pnputil 安装 HID 过滤驱动。
# 驱动安装需要管理员权限；脚本不会默认开启 Windows 测试签名。

[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path ${env:ProgramFiles} "T1 Remote"),
    [switch]$SkipDriver,
    [switch]$EnableTestSigning,
    [switch]$AllowUnsignedDriver
)

$ErrorActionPreference = "Stop"
$releaseRoot = $PSScriptRoot

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

Assert-Administrator
$driverRoot = Join-Path $releaseRoot "Driver"
$driverInf = Get-ChildItem -LiteralPath $driverRoot -File -Filter "*.inf" | Select-Object -First 1
if (-not $SkipDriver -and $null -eq $driverInf) {
    throw "发布包缺少 Driver/*.inf。"
}
if ($AllowUnsignedDriver -and -not $EnableTestSigning) {
    throw "AllowUnsignedDriver 仅能和 EnableTestSigning 一起使用。"
}

if ($EnableTestSigning) {
    & bcdedit.exe /set testsigning on
    if ($LASTEXITCODE -ne 0) {
        throw "开启 Windows 测试签名失败，退出码：$LASTEXITCODE"
    }
    Write-Warning "已请求开启测试签名；Windows 可能需要重启后驱动才能加载。"
}

if (-not $SkipDriver) {
    $driverFiles = @(Get-ChildItem -LiteralPath $driverRoot -File | Where-Object { $_.Extension -in @(".sys", ".cat") })
    $invalidSignatures = @($driverFiles | Where-Object {
        (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne "Valid"
    })
    if ($invalidSignatures.Count -gt 0 -and -not $AllowUnsignedDriver) {
        $names = ($invalidSignatures.Name -join ", ")
        throw "驱动签名无效：$names；正式包请提供有效签名，测试包需显式指定 -EnableTestSigning -AllowUnsignedDriver。"
    }

    & pnputil.exe /add-driver $driverInf.FullName /install
    if ($LASTEXITCODE -notin @(0, 3010)) {
        throw "pnputil 安装驱动失败，退出码：$LASTEXITCODE"
    }
    if ($LASTEXITCODE -eq 3010) {
        Write-Warning "驱动已暂存，Windows 要求重启后加载。"
    }
}

$installedApp = Join-Path $InstallRoot "App"
$installedNative = Join-Path $InstallRoot "Native"
$installedDriver = Join-Path $InstallRoot "Driver"
Copy-DirectoryContents (Join-Path $releaseRoot "App") $installedApp
Copy-DirectoryContents (Join-Path $releaseRoot "Native") $installedNative
Copy-DirectoryContents (Join-Path $releaseRoot "Driver") $installedDriver
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
if ($SkipDriver) {
    Write-Warning "已跳过驱动安装。"
}
