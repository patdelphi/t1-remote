# 程序说明：构建 T1 Remote Windows x64 用户态包和可选原生组件。
# 本脚本只生成新的输出目录，不安装驱动、不启用测试签名、不修改生产设备。

[CmdletBinding()]
param(
    [string]$OutputRoot = "dist",
    [string]$PythonPath = "python",
    [switch]$SkipNative,
    [switch]$SourceBundleOnly,
    # 本机 Tcl/Tk 初始化会偶发失败（见 Docs/build-windows.md），必要时可跳过测试门槛。
    [switch]$SkipTests,
    # 以下两项只用于本机编译校验：正式包不应关闭 Spectre 缓解或驱动签名。
    [switch]$SkipSpectreMitigation,
    [switch]$SkipDriverSigning
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "build_env.ps1")
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

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    Write-Host ("> {0} {1}" -f $FilePath, ($Arguments -join " "))
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("命令失败，退出码 {0}: {1}" -f $LASTEXITCODE, $FilePath)
    }
}

Set-Location $projectRoot
if (-not $SkipTests) {
    Invoke-Native $PythonPath @("-m", "pytest", "-q")
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

if (-not $SourceBundleOnly -and -not $SkipNative) {
    $msbuild = Resolve-MSBuild
    $cmake = Resolve-CMake
    $bridgeDll = $null
    if ($null -ne $cmake) {
        $bridgeBuildRoot = Join-Path $buildRoot "t1bridge"
        $cmakeArguments = @("-S", "native/t1bridge", "-B", $bridgeBuildRoot)
        $generator = Resolve-CMakeGenerator $msbuild
        if ($null -ne $generator) {
            $cmakeArguments += @("-G", $generator, "-A", "x64")
        } else {
            $cmakeArguments += @("-A", "x64")
        }
        try {
            Invoke-Native $cmake $cmakeArguments
            Invoke-Native $cmake @("--build", $bridgeBuildRoot, "--config", "Release")
            $candidate = Join-Path $bridgeBuildRoot "Release/t1bridge.dll"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $bridgeDll = $candidate
            }
        } catch {
            Write-Warning ("CMake 构建桥接 DLL 失败：{0}" -f $_.Exception.Message)
        }
    } else {
        Write-Warning "未找到 CMake，改用 cl.exe 直编桥接 DLL"
    }
    if ($null -eq $bridgeDll) {
        # 输出到运行时加载器搜索的目录，否则 App 启动时找不到 DLL。
        $bridgeDll = Invoke-BridgeDllCompile `
            -SourceDirectory (Join-Path $projectRoot "native/t1bridge") `
            -OutputDirectory (Join-Path $projectRoot "native/t1bridge/x64/Release") `
            -MSBuildPath $msbuild
    }
    if ($null -ne $bridgeDll) {
        Write-Host ("桥接 DLL：{0}" -f $bridgeDll)
    } else {
        Write-Warning "桥接 DLL 未构建"
    }
    if ($null -ne $msbuild) {
        Invoke-Native $msbuild (Get-DriverBuildArguments `
                -ProjectPath "native/t1filter/t1filter.vcxproj" `
                -SkipSpectreMitigation:$SkipSpectreMitigation `
                -SkipDriverSigning:$SkipDriverSigning)
    } else {
        Write-Warning "未找到 MSBuild 或 WDK 工具集，跳过过滤驱动构建"
    }
}

$pyinstaller = Get-Command "pyinstaller.exe" -ErrorAction SilentlyContinue
if (-not $SourceBundleOnly -and $null -ne $pyinstaller) {
    $distPath = Join-Path $packageRoot "app"
    $workPath = Join-Path $packageRoot "pyinstaller-work"
    $specPath = Join-Path $packageRoot "pyinstaller-spec"
    # --specpath 会改变 PyInstaller 的工作目录，--add-data 必须使用绝对路径。
    Invoke-Native $pyinstaller.Source @(
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "T1Remote",
        "--icon", (Join-Path $projectRoot "assets/t1-remote-icon.ico"),
        "--distpath", $distPath,
        "--workpath", $workPath,
        "--specpath", $specPath,
        "--add-data", ((Join-Path $projectRoot "config") + ";config"),
        "--add-data", ((Join-Path $projectRoot "assets") + ";assets"),
        (Join-Path $projectRoot "tools/t1_app.py")
    )
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
