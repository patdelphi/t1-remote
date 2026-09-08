# 程序说明：构建 T1 Remote Windows x64 用户态包和可选原生组件。
# 本脚本只生成新的输出目录，不安装驱动、不启用测试签名、不修改生产设备。

[CmdletBinding()]
param(
    [string]$OutputRoot = "dist",
    [switch]$SkipNative,
    [switch]$SourceBundleOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$packageName = "t1-remote-win-x64-$stamp"
$packageRoot = Join-Path (Join-Path $projectRoot $OutputRoot) $packageName
$buildRoot = Join-Path $packageRoot "native-build"

function Copy-SourceTree {
    param(
        [string]$Source,
        [string]$Destination
    )

    $sourceRoot = (Resolve-Path $Source).Path
    Get-ChildItem $sourceRoot -File -Recurse |
        Where-Object { $_.FullName -notmatch "\\__pycache__\\" -and $_.Extension -ne ".pyc" } |
        ForEach-Object {
            $relative = $_.FullName.Substring($sourceRoot.Length + 1)
            $target = Join-Path $Destination $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item $_.FullName -Destination $target
        }
}

Set-Location $projectRoot
python -m pytest -q
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

if (-not $SourceBundleOnly -and -not $SkipNative) {
    if (Get-Command cmake -ErrorAction SilentlyContinue) {
        cmake -S "native/t1bridge" -B (Join-Path $buildRoot "t1bridge") -A x64
        cmake --build (Join-Path $buildRoot "t1bridge") --config Release
    }
    if (Get-Command msbuild -ErrorAction SilentlyContinue) {
        msbuild "native/t1filter/t1filter.vcxproj" /p:Configuration=Release /p:Platform=x64
    }
}

if (-not $SourceBundleOnly -and (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    $distPath = Join-Path $packageRoot "app"
    $workPath = Join-Path $packageRoot "pyinstaller-work"
    $specPath = Join-Path $packageRoot "pyinstaller-spec"
    pyinstaller --noconfirm --clean --windowed `
        --name "T1Remote" `
        --distpath $distPath `
        --workpath $workPath `
        --specpath $specPath `
        --add-data "config;config" `
        --add-data "assets;assets" `
        "tools/t1_app.py"
} else {
    Copy-SourceTree "t1remote" (Join-Path $packageRoot "t1remote")
    Copy-SourceTree "tools" (Join-Path $packageRoot "tools")
    Copy-SourceTree "config" (Join-Path $packageRoot "config")
    Copy-SourceTree "assets" (Join-Path $packageRoot "assets")
    Copy-Item "pyproject.toml" -Destination $packageRoot
}

$hashPath = Join-Path $packageRoot "SHA256SUMS.txt"
Get-ChildItem -File -Recurse $packageRoot |
    Where-Object { $_.FullName -ne $hashPath } |
    Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash)  $($_.Path.Substring($packageRoot.Length + 1))" } |
    Set-Content -Encoding utf8 $hashPath

$archivePath = Join-Path (Split-Path -Parent $packageRoot) "$packageName.zip"
Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $archivePath
Get-FileHash -Algorithm SHA256 $archivePath |
    ForEach-Object { "$($_.Hash)  $([IO.Path]::GetFileName($archivePath))" } |
    Add-Content -Encoding utf8 $hashPath

Write-Host "已生成包：$packageRoot"
Write-Host "压缩包：$archivePath"
Write-Host "校验文件：$hashPath"
