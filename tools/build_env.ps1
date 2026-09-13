# 程序说明：为构建脚本解析原生工具链路径，不修改系统配置。
# 只负责查找，不安装工具；找不到时返回 $null，由调用方决定跳过还是报错。
# BuildTools 实例在部分 vswhere 版本下不带 product 标记，因此还要扫描固定路径。

function Resolve-MSBuild {
    # 顺序：PATH → vswhere → Visual Studio 固定安装路径。
    $command = Get-Command "msbuild.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $found = & $vswhere -latest -requires Microsoft.Component.MSBuild `
            -find "MSBuild\**\Bin\MSBuild.exe" 2>$null | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($found) -and (Test-Path -LiteralPath $found -PathType Leaf)) {
            return $found
        }
        $install = & $vswhere -latest -property installationPath 2>$null | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($install)) {
            $candidate = Join-Path $install "MSBuild\Current\Bin\MSBuild.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return $candidate
            }
        }
    }

    $patterns = @(
        (Join-Path ${env:ProgramFiles} "Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe")
    )
    foreach ($pattern in $patterns) {
        $match = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object -Property FullName -Descending |
            Select-Object -First 1
        if ($null -ne $match) {
            return $match.FullName
        }
    }
    return $null
}

function Resolve-CMake {
    # 顺序：PATH → Program Files 下的标准安装目录。
    $command = Get-Command "cmake.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    foreach ($root in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
        if ([string]::IsNullOrWhiteSpace($root)) {
            continue
        }
        $candidate = Join-Path $root "CMake\bin\cmake.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Resolve-CMakeGenerator {
    # 装了 Ninja 的机器上 CMake 会默认选 Ninja，而 Ninja 不接受 -A 平台参数，
    # 因此按 MSBuild 路径里的 Visual Studio 年份显式指定 VS 生成器。
    # vswhere 在只装 BuildTools 的实例上可能查不到安装信息，故不用它。
    param([string]$MSBuildPath)

    if ([string]::IsNullOrWhiteSpace($MSBuildPath)) {
        return $null
    }
    $match = [regex]::Match($MSBuildPath, "Microsoft Visual Studio\\(\d{4})\\")
    if (-not $match.Success) {
        return $null
    }
    switch ($match.Groups[1].Value) {
        "2019" { return "Visual Studio 16 2019" }
        "2022" { return "Visual Studio 17 2022" }
    }
    return $null
}

function Invoke-BridgeDllCompile {
    # CMake 的 Visual Studio 生成器依赖 Visual Studio 实例注册，只装 BuildTools
    # 的机器上会找不到实例，因此提供 cl.exe 直编兜底；返回生成的 DLL 路径，
    # 失败返回 $null。
    param(
        [string]$SourceDirectory,
        [string]$OutputDirectory,
        [string]$MSBuildPath
    )

    if ([string]::IsNullOrWhiteSpace($MSBuildPath)) {
        return $null
    }
    $pattern = '(?i)(.+\\Microsoft Visual Studio\\\d{4}\\[^\\]+)\\MSBuild\\'
    $match = [regex]::Match($MSBuildPath, $pattern)
    if (-not $match.Success) {
        return $null
    }
    $vcvars = Join-Path $match.Groups[1].Value "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path -LiteralPath $vcvars -PathType Leaf)) {
        return $null
    }

    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    # 在输出目录里编译：目标文件和 DLL 都落在构建目录，不污染源码目录。
    $source = Join-Path $SourceDirectory "t1bridge.c"
    $compile = 'call "{0}" >nul 2>&1 && cd /d "{1}" && cl /nologo /LD /O2 /W4 /WX /utf-8 "{2}"' -f `
        $vcvars, $OutputDirectory, $source
    Write-Host ("> cmd /c {0}" -f $compile)
    # 原生命令的输出必须只进主机，不能混进函数返回值。
    & cmd.exe /c $compile | Out-Host
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $dll = Join-Path $OutputDirectory "t1bridge.dll"
    if (Test-Path -LiteralPath $dll -PathType Leaf) {
        return $dll
    }
    return $null
}

function Get-DriverBuildArguments {
    param(
        [string]$ProjectPath,
        [switch]$SkipSpectreMitigation,
        [switch]$SkipDriverSigning
    )

    $arguments = @($ProjectPath, "/p:Configuration=Release", "/p:Platform=x64")
    if ($SkipSpectreMitigation) {
        # 未安装 Spectre 缓解库的机器做编译校验时使用；正式包不应带此项。
        $arguments += "/p:SpectreMitigation=false"
    }
    if ($SkipDriverSigning) {
        # 无签名证书或非管理员时的编译校验；生成的 SYS/CAT 未签名。
        $arguments += "/p:SignMode=Off"
    }
    return $arguments
}
