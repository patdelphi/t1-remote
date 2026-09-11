# 程序说明：卸载 T1 Remote Release 包注册的驱动、App 文件和开始菜单快捷方式。
# 驱动删除通过 pnputil 定位 Original Name=t1filter.inf 对应的 oem*.inf。

[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path ${env:ProgramFiles} "T1 Remote"),
    [switch]$SkipDriver
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "请以管理员身份运行 Uninstall-T1Remote.ps1。"
    }
}

function Find-PublishedDriverName {
    $output = (& pnputil.exe /enum-drivers 2>&1 | Out-String)
    $blocks = $output -split "(?m)(?=Published Name\s*:|发布名称\s*:)"
    foreach ($block in $blocks) {
        if ($block -match "(?i)t1filter\.inf" -and $block -match "(?im)(?:Published Name|发布名称)\s*:\s*(oem\d+\.inf)") {
            return $Matches[1]
        }
    }
    return $null
}

Assert-Administrator
if (-not $SkipDriver) {
    $publishedName = Find-PublishedDriverName
    if (-not [string]::IsNullOrWhiteSpace($publishedName)) {
        & pnputil.exe /delete-driver $publishedName /uninstall /force
        if ($LASTEXITCODE -notin @(0, 3010)) {
            throw "pnputil 删除驱动失败，退出码：$LASTEXITCODE"
        }
        Write-Host "已删除驱动包：$publishedName"
    } else {
        Write-Warning "未找到已发布的 t1filter.inf，跳过驱动删除。"
    }
}

$shortcutPath = Join-Path ${env:ProgramData} "Microsoft\Windows\Start Menu\Programs\T1 Remote\T1 Remote.lnk"
if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
}
$shortcutRoot = Split-Path -Parent $shortcutPath
if (Test-Path -LiteralPath $shortcutRoot) {
    $remaining = Get-ChildItem -LiteralPath $shortcutRoot -Force
    if ($remaining.Count -eq 0) {
        Remove-Item -LiteralPath $shortcutRoot -Force
    }
}

if (Test-Path -LiteralPath $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Host "已删除 App 文件：$InstallRoot"
}
