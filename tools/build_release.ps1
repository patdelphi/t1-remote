# 程序说明：构建可交付的 T1 Remote Windows x64 Release 包。
# 包含 App、桥接 DLL、HID 过滤驱动、安装/卸载脚本、启动入口和 SHA-256 清单。
# 本脚本只生成带时间戳的新目录，不安装驱动、不启用测试签名、不覆盖旧包。

[CmdletBinding()]
param(
    [string]$OutputRoot = "dist",
    [string]$Version = "",
    [string]$PythonPath = "python",
    [switch]$SourceApp,
    [switch]$SkipTests,
    [switch]$SkipNativeBuild,
    # 以下两项只用于本机编译校验：正式包不应关闭 Spectre 缓解或驱动签名。
    [switch]$SkipSpectreMitigation,
    [switch]$SkipDriverSigning,
    # 显式指定要打包进 VBCable/ 的 VB-CABLE 安装器；缺省时自动扫描仓库根/tools/native。
    [string]$VbCableInstaller = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "build_env.ps1")

function Invoke-External {
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

function Copy-SourceTree {
    param(
        [string]$Source,
        [string]$Destination
    )

    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path
    Get-ChildItem -LiteralPath $sourceRoot -File -Recurse |
        Where-Object {
            $_.FullName -notmatch "\\__pycache__\\" -and
            $_.Extension -ne ".pyc"
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($sourceRoot.Length + 1)
            $target = Join-Path $Destination $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
}

function Read-ProjectVersion {
    param([string]$Path)

    $content = Get-Content -LiteralPath $Path -Raw
    $match = [regex]::Match($content, '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
    if (-not $match.Success) {
        throw "pyproject.toml 中未找到三段式 project.version"
    }
    return $match.Groups[1].Value
}

function Find-DriverPackage {
    param([string[]]$Candidates)

    foreach ($candidate in $Candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
            continue
        }
        $files = @(Get-ChildItem -LiteralPath $candidate -File)
        # 一个驱动服务可能由多个设备类 INF 共用同一个 SYS；必须把所有
        # INF 和 CAT 一起放入发布包，否则第二个 INF 的 CatalogFile 会失配。
        $infs = @($files | Where-Object Extension -ieq ".inf")
        $sys = $files | Where-Object Extension -ieq ".sys" | Select-Object -First 1
        $cats = @($files | Where-Object Extension -ieq ".cat")
        if ($infs.Count -gt 0 -and $null -ne $sys -and $cats.Count -gt 0) {
            return [pscustomobject]@{
                Root = $candidate
                Infs = $infs
                Sys = $sys
                Cats = $cats
            }
        }
    }
    return $null
}

Set-Location -LiteralPath $projectRoot
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Read-ProjectVersion (Join-Path $projectRoot "pyproject.toml")
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$packageName = "T1Remote-v{0}-win-x64-{1}" -f $Version, $stamp
$releaseOutputRoot = Join-Path $projectRoot $OutputRoot
$packageRoot = Join-Path $releaseOutputRoot $packageName
$appRoot = Join-Path $packageRoot "App"
$nativeRoot = Join-Path $packageRoot "Native"
$driverRoot = Join-Path $packageRoot "Driver"
$buildRoot = Join-Path $releaseOutputRoot ".build-$packageName"

if (-not $SkipTests) {
    # 项目测试全部使用 unittest.TestCase；pytest 环境下 tkinter 测试
    # 会因 Tcl 库初始化失败而误报，因此发布构建统一走 unittest discover。
    Invoke-External $PythonPath @("-m", "unittest", "discover", "-s", "tests", "-q")
}

New-Item -ItemType Directory -Force -Path $appRoot, $nativeRoot, $driverRoot | Out-Null

# 优先在本次包的临时目录构建桥接 DLL；编译工具缺失时复用已存在的 Release DLL。
$bridgeSource = $null
if (-not $SkipNativeBuild) {
    $cmake = Resolve-CMake
    if ($null -ne $cmake) {
        $bridgeBuildRoot = Join-Path $buildRoot "t1bridge"
        $cmakeArguments = @(
            "-S", (Join-Path $projectRoot "native/t1bridge"),
            "-B", $bridgeBuildRoot
        )
        $generator = Resolve-CMakeGenerator (Resolve-MSBuild)
        if ($null -ne $generator) {
            $cmakeArguments += @("-G", $generator, "-A", "x64")
        } else {
            $cmakeArguments += @("-A", "x64")
        }
        try {
            Invoke-External $cmake $cmakeArguments
            Invoke-External $cmake @("--build", $bridgeBuildRoot, "--config", "Release")
        } catch {
            # Strawberry 等工具链自带的 cmake 不支持 VS 生成器时会在这里失败；
            # 桥接 DLL 不是本包核心，失败时降级到 cl.exe 直编或复用已有 DLL。
            Write-Warning "CMake 构建桥接 DLL 失败：$($_.Exception.Message)"
        }
        $builtBridge = Join-Path $bridgeBuildRoot "Release/t1bridge.dll"
        if (Test-Path -LiteralPath $builtBridge -PathType Leaf) {
            $bridgeSource = $builtBridge
        }
    } else {
        Write-Warning "未找到 CMake，改用 cl.exe 直编桥接 DLL"
    }
    if ($null -eq $bridgeSource) {
        # 输出到运行时加载器搜索的目录，同时作为后续候选来源。
        $bridgeSource = Invoke-BridgeDllCompile `
            -SourceDirectory (Join-Path $projectRoot "native/t1bridge") `
            -OutputDirectory (Join-Path $projectRoot "native/t1bridge/x64/Release") `
            -MSBuildPath (Resolve-MSBuild)
    }
}
if ($null -eq $bridgeSource) {
    $bridgeCandidates = @(
        (Join-Path $projectRoot "native/t1bridge/x64/Release/t1bridge.dll"),
        (Join-Path $projectRoot "native/t1bridge/build-vs2022/Release/t1bridge.dll"),
        (Join-Path $projectRoot "native/t1bridge.dll")
    )
    $bridgeSource = $bridgeCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($bridgeSource)) {
    throw "找不到 t1bridge.dll；请安装 CMake/MSVC 构建，或提供已有 Release DLL。"
}
Copy-Item -LiteralPath $bridgeSource -Destination (Join-Path $nativeRoot "t1bridge.dll") -Force

# 过滤驱动需要全部 INF、SYS 和 CAT 文件；没有完整包时才尝试调用 MSBuild。
$driverPackage = $null
if (-not $SkipNativeBuild) {
    $msbuild = Resolve-MSBuild
    if ($null -ne $msbuild) {
        Invoke-External $msbuild (Get-DriverBuildArguments `
                -ProjectPath (Join-Path $projectRoot "native/t1filter/t1filter.vcxproj") `
                -SkipSpectreMitigation:$SkipSpectreMitigation `
                -SkipDriverSigning:$SkipDriverSigning)
    } else {
        Write-Warning "未找到 MSBuild 或 WDK 工具集，改为复用已有驱动包"
    }
}
$driverPackage = Find-DriverPackage @(
    (Join-Path $projectRoot "native/t1filter/x64/Release/t1filter"),
    (Join-Path $projectRoot "native/t1filter/Release/t1filter"),
    (Join-Path $projectRoot "native/t1filter/package")
)
if ($null -eq $driverPackage) {
    throw "找不到完整驱动包；需要 t1filter.inf、t1filter.sys 和 .cat。"
}
foreach ($inf in @($driverPackage.Infs)) {
    Copy-Item -LiteralPath $inf.FullName -Destination (Join-Path $driverRoot $inf.Name) -Force
}
Copy-Item -LiteralPath $driverPackage.Sys.FullName -Destination (Join-Path $driverRoot "t1filter.sys") -Force
foreach ($cat in @($driverPackage.Cats)) {
    Copy-Item -LiteralPath $cat.FullName -Destination (Join-Path $driverRoot $cat.Name) -Force
}

$signatureReport = @(
    $driverRoot | Get-ChildItem -File | Where-Object { $_.Extension -in @(".sys", ".cat") } |
        ForEach-Object {
            $signature = Get-AuthenticodeSignature -LiteralPath $_.FullName
            "{0}`t{1}`t{2}" -f $_.Name, $signature.Status, $signature.StatusMessage
        }
)
$signatureReport | Set-Content -LiteralPath (Join-Path $driverRoot "SIGNATURES.txt") -Encoding utf8

$pyinstaller = Get-Command "pyinstaller.exe" -ErrorAction SilentlyContinue
if ($null -eq $pyinstaller) {
    # 用户级 pip 安装的脚本目录通常不在 PATH，按 PythonPath 反查一次。
    $pythonCommand = Get-Command $PythonPath -ErrorAction SilentlyContinue
    $pyinstallerCandidates = @()
    if ($null -ne $pythonCommand) {
        $pythonBin = Split-Path -Parent $pythonCommand.Source
        $pyinstallerCandidates += Join-Path $pythonBin "Scripts/pyinstaller.exe"
    }
    if (Test-Path -LiteralPath $PythonPath -PathType Leaf) {
        $pythonVersion = (& $PythonPath -c "import sys; print(f'Python{sys.version_info.major}{sys.version_info.minor}')").Trim()
        $pyinstallerCandidates += Join-Path ${env:APPDATA} "Python/$pythonVersion/Scripts/pyinstaller.exe"
    }
    foreach ($candidate in $pyinstallerCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $pyinstaller = Get-Command $candidate
            break
        }
    }
}
$appMode = "source"
if (-not $SourceApp -and $null -ne $pyinstaller) {
    $appMode = "pyinstaller-onedir"
    $pyWork = Join-Path $buildRoot "pyinstaller-work"
    $pySpec = Join-Path $buildRoot "pyinstaller-spec"
    $pyinstallerArguments = @(
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "T1Remote",
        "--uac-admin",
        "--icon", (Join-Path $projectRoot "assets/t1-remote-icon.ico"),
        "--distpath", $appRoot,
        "--workpath", $pyWork,
        "--specpath", $pySpec,
        "--add-data", ((Join-Path $projectRoot "config") + ";config"),
        "--add-data", ((Join-Path $projectRoot "assets") + ";assets"),
        "--add-binary", ((Join-Path $nativeRoot "t1bridge.dll") + ";native"),
        (Join-Path $projectRoot "tools/t1_app.py")
    )
    Invoke-External $pyinstaller.Source $pyinstallerArguments
} elseif (-not $SourceApp) {
    throw "未找到 PyInstaller；安装项目 package extra 后重试，或明确使用 -SourceApp 生成源码 App 包。"
} else {
    Write-Warning "使用源码 App 模式；目标机器需要 Python 3.11+ 和项目依赖。"
    $sourceAppRoot = Join-Path $appRoot "Source"
    Copy-SourceTree (Join-Path $projectRoot "t1remote") (Join-Path $sourceAppRoot "t1remote")
    Copy-SourceTree (Join-Path $projectRoot "tools") (Join-Path $sourceAppRoot "tools")
    Copy-SourceTree (Join-Path $projectRoot "config") (Join-Path $sourceAppRoot "config")
    Copy-SourceTree (Join-Path $projectRoot "assets") (Join-Path $sourceAppRoot "assets")
    Copy-Item -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Destination $sourceAppRoot -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $sourceAppRoot "native") | Out-Null
    Copy-Item -LiteralPath (Join-Path $nativeRoot "t1bridge.dll") -Destination (Join-Path $sourceAppRoot "native/t1bridge.dll") -Force
}

Copy-Item -LiteralPath (Join-Path $projectRoot "tools/start_release.bat") -Destination (Join-Path $packageRoot "Start-T1Remote.bat") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "tools/install_release.ps1") -Destination (Join-Path $packageRoot "Install-T1Remote.ps1") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "tools/uninstall_release.ps1") -Destination (Join-Path $packageRoot "Uninstall-T1Remote.ps1") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "Docs/release.md") -Destination (Join-Path $packageRoot "RELEASE.md") -Force

# VB-CABLE 安装器可选打包：显式参数优先，否则扫描仓库根/tools/native。
$vbCableSource = $null
if (-not [string]::IsNullOrWhiteSpace($VbCableInstaller)) {
    if (-not (Test-Path -LiteralPath $VbCableInstaller -PathType Leaf)) {
        throw "VbCableInstaller 指定的文件不存在：$VbCableInstaller"
    }
    $vbCableSource = Get-Item -LiteralPath $VbCableInstaller
} else {
    foreach ($scanRoot in @($projectRoot, (Join-Path $projectRoot "tools"), (Join-Path $projectRoot "native"))) {
        $found = Get-ChildItem -LiteralPath $scanRoot -File -Filter "VBCABLE_Setup*.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $found) {
            $vbCableSource = $found
            break
        }
    }
}
$vbCableInstallerName = $null
if ($null -ne $vbCableSource) {
    $vbCableDir = Join-Path $packageRoot "VBCable"
    New-Item -ItemType Directory -Force -Path $vbCableDir | Out-Null
    $vbCableInstallerName = "VBCABLE_Setup_x64.exe"
    Copy-Item -LiteralPath $vbCableSource.FullName -Destination (Join-Path $vbCableDir $vbCableInstallerName) -Force
    Write-Host ("已打包 VB-CABLE 安装器：{0}" -f $vbCableInstallerName)
} else {
    Write-Warning "未找到 VB-CABLE 安装器，跳过 VBCable/ 目录；语音功能需要用户自行安装虚拟声卡。"
}

$driverNames = @(
    @($driverPackage.Infs | ForEach-Object { $_.Name })
    $driverPackage.Sys.Name
    @($driverPackage.Cats | ForEach-Object { $_.Name })
)
$manifest = [ordered]@{
    project = "t1-remote"
    version = $Version
    package = $packageName
    architecture = "x64"
    app_mode = $appMode
    driver = $driverNames
    bridge = "t1bridge.dll"
    vb_cable_installer = $vbCableInstallerName
    built_at = (Get-Date).ToString("o")
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $packageRoot "release-manifest.json") -Encoding utf8

$hashPath = Join-Path $packageRoot "SHA256SUMS.txt"
$hashLines = Get-ChildItem -LiteralPath $packageRoot -File -Recurse |
    Where-Object { $_.FullName -ne $hashPath } |
    Sort-Object FullName |
    ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $relative = $_.FullName.Substring($packageRoot.Length + 1)
        "{0}  {1}" -f $hash, $relative
    }
$hashLines | Set-Content -LiteralPath $hashPath -Encoding utf8

$archivePath = Join-Path (Split-Path -Parent $packageRoot) "$packageName.zip"
Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
Add-Content -LiteralPath $hashPath -Value ("{0}  {1}" -f $archiveHash, (Split-Path -Leaf $archivePath)) -Encoding utf8

Write-Host "已生成 Release 包：$packageRoot"
Write-Host "压缩包：$archivePath"
Write-Host "校验文件：$hashPath"
