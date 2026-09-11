@echo off
chcp 65001 >nul
rem 程序说明：启动已解压或已安装的 T1 Remote Release App。
setlocal

set "APP_ROOT=%~dp0App"
if exist "%APP_ROOT%\T1Remote\T1Remote.exe" (
    start "" /d "%APP_ROOT%\T1Remote" "%APP_ROOT%\T1Remote\T1Remote.exe"
    exit /b %errorlevel%
)

if exist "%APP_ROOT%\Source\tools\t1_app.py" (
    where pyw.exe >nul 2>nul
    if not errorlevel 1 (
        start "" /d "%APP_ROOT%\Source" pyw.exe -3 "%APP_ROOT%\Source\tools\t1_app.py"
        exit /b %errorlevel%
    )
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        start "" /d "%APP_ROOT%\Source" pythonw.exe "%APP_ROOT%\Source\tools\t1_app.py"
        exit /b %errorlevel%
    )
)

echo 未找到 T1Remote.exe 或 Python 源码 App。
echo 请确认已完整解压 Release 包，并安装 Python 3.11 或更高版本。
pause
exit /b 1
