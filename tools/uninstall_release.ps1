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

function Restore-PowerButtonAction {
    # 恢复安装时备份的系统电源按钮动作，并把备份标记为已恢复（不删除文件）。
    param([string]$BackupPath)

    if (-not (Test-Path -LiteralPath $BackupPath)) {
        Write-Warning "未找到电源按钮备份，保持当前设置。"
        return
    }
    $backup = Get-Content -LiteralPath $BackupPath -Raw | ConvertFrom-Json
    if ($backup.PSObject.Properties.Name -contains 'Restored' -and $backup.Restored) {
        return
    }
    $subgroup = '4f971e89-eebd-4455-a8de-9e59040e7347'
    $setting = '7648efa3-dd9c-4e3e-b566-50f929386280'
    & powercfg.exe /setacvalueindex SCHEME_CURRENT $subgroup $setting ([int]$backup.ACSettingIndex) | Out-Null
    & powercfg.exe /setdcvalueindex SCHEME_CURRENT $subgroup $setting ([int]$backup.DCSettingIndex) | Out-Null
    & powercfg.exe /setactive SCHEME_CURRENT | Out-Null
    [pscustomobject]@{
        ACSettingIndex = [int]$backup.ACSettingIndex
        DCSettingIndex = [int]$backup.DCSettingIndex
        Scheme         = $backup.Scheme
        Restored       = $true
    } | ConvertTo-Json | Set-Content -LiteralPath $BackupPath -Encoding utf8
    Write-Host ("已恢复电源按钮动作：AC=" + $backup.ACSettingIndex + " DC=" + $backup.DCSettingIndex)
}

function Remove-KeyboardCollectionFilter {
    # 移除安装时写入键盘集合设备实例子键的 LowerFilters 绑定。
    $classRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}'
    $instances = @(Get-ChildItem $classRoot -ErrorAction SilentlyContinue | Where-Object {
        $deviceId = (Get-ItemProperty -Path $_.PSPath -Name MatchingDeviceId -ErrorAction SilentlyContinue).MatchingDeviceId
        $null -ne $deviceId -and $deviceId -match 'Col01'
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
    Write-Host "键盘集合过滤器绑定已移除。"
}

Assert-Administrator
Restore-PowerButtonAction -BackupPath (Join-Path ${env:ProgramData} "T1 Remote\power-button-backup.json")
Remove-KeyboardCollectionFilter
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
