@echo off
chcp 65001 >nul
rem 程序说明：从项目目录启动 T1 Remote Mapping 主窗口。
setlocal

cd /d "%~dp0"

where pyw.exe >nul 2>nul
if not errorlevel 1 goto launch_pyw

where pythonw.exe >nul 2>nul
if not errorlevel 1 goto launch_pythonw

echo 未找到 Python 启动器，请先安装 Python 3.11 或更高版本。
pause
exit /b 1

:launch_pyw
start "" "%SystemRoot%\pyw.exe" -3 -m tools.t1_app
exit /b %errorlevel%

:launch_pythonw
start "" pythonw.exe -m tools.t1_app
exit /b %errorlevel%
