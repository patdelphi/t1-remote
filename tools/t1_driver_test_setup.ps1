# 程序说明：以管理员权限配置 T1Remote KMDF 驱动的本机测试环境。
# 仅用于开发机：导入本项目测试证书、开启测试签名并安装已构建的驱动包。

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$packageInf = Join-Path $projectRoot "native\t1filter\x64\Release\t1filter\t1filter.inf"
$certificatePath = Join-Path $projectRoot "native\t1filter\x64\Release\t1filter.cer"
$logPath = Join-Path $env:TEMP "t1remote-driver-test-setup.log"

function Write-SetupLog {
    param([string]$Message)
    Write-Output $Message
    Add-Content -LiteralPath $logPath -Value $Message -Encoding utf8
}

if (-not (Test-Path -LiteralPath $packageInf)) {
    throw "Driver INF not found: $packageInf"
}
if (-not (Test-Path -LiteralPath $certificatePath)) {
    throw "Test certificate not found: $certificatePath"
}

Set-Content -LiteralPath $logPath -Value "T1Remote driver test setup $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')" -Encoding utf8
Write-SetupLog "Project root: $projectRoot"
Write-SetupLog "Driver INF: $packageInf"

try {
    $secureBoot = Confirm-SecureBootUEFI
    Write-SetupLog "Secure Boot: $secureBoot"
} catch {
    Write-SetupLog ("Secure Boot read failed: {0}" -f $_.Exception.Message)
}

$certificate = Get-PfxCertificate -LiteralPath $certificatePath
Write-SetupLog ("Test certificate thumbprint: {0}" -f $certificate.Thumbprint)

foreach ($storeName in @("Root", "TrustedPublisher")) {
    $storePath = "Cert:\LocalMachine\$storeName"
    $existing = Get-ChildItem -Path $storePath | Where-Object Thumbprint -eq $certificate.Thumbprint
    if ($null -eq $existing) {
        Import-Certificate -FilePath $certificatePath -CertStoreLocation $storePath | Out-Null
        Write-SetupLog "Imported certificate to LocalMachine\$storeName"
    } else {
        Write-SetupLog "Certificate already exists in LocalMachine\$storeName"
    }
}

$testSigningResult = & bcdedit.exe /set testsigning on 2>&1
$testSigningExitCode = $LASTEXITCODE
Write-SetupLog ("bcdedit /set testsigning on: {0}" -f ($testSigningResult -join ' '))
Write-SetupLog ("bcdedit exit code: {0}" -f $testSigningExitCode)
if ($testSigningExitCode -ne 0) {
    throw "Failed to enable test signing, exit code: $testSigningExitCode"
}

$pnputilResult = & pnputil.exe /add-driver $packageInf /install 2>&1
$pnputilExitCode = $LASTEXITCODE
Write-SetupLog ("pnputil: {0}" -f ($pnputilResult -join ' '))
Write-SetupLog ("pnputil exit code: {0}" -f $pnputilExitCode)
if ($pnputilExitCode -notin @(0, 3010)) {
    throw "Failed to install driver package, exit code: $pnputilExitCode"
}
if ($pnputilExitCode -eq 3010) {
    Write-SetupLog "Driver package staged successfully; Windows restart is required to reload the device stack."
}

$targetDevices = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -like 'HID\{00001812-0000-1000-8000-00805F9B34FB}_DEV_VID&01620A*'
}
foreach ($device in $targetDevices) {
    Write-SetupLog ("T1 HID device: {0} | {1} | {2}" -f $device.Status, $device.Class, $device.InstanceId)
}

Write-SetupLog "Setup complete. Restart Windows to activate test signing and the filter driver when required."
Write-Output ("Setup complete. Log: {0}" -f $logPath)
