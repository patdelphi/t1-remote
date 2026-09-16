@echo off
chcp 65001 >nul
rem 程序说明：启动已解压或已安装的 T1 Remote Release App。
setlocal

set "APP_ROOT=%~dp0App"
rem 源码 App 优先，便于在没有 PyInstaller 时用最新源码更新已有安装目录。
if exist "%APP_ROOT%\Source\tools\t1_app.py" (
    where pyw.exe >nul 2>nul
    if not errorlevel 1 (
        powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'pyw.exe' -ArgumentList @('-3','%APP_ROOT%\Source\tools\t1_app.py') -WorkingDirectory '%APP_ROOT%\Source' -Verb RunAs"
        exit /b %errorlevel%
    )
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'pythonw.exe' -ArgumentList @('%APP_ROOT%\Source\tools\t1_app.py') -WorkingDirectory '%APP_ROOT%\Source' -Verb RunAs"
        exit /b %errorlevel%
    )
)

if exist "%APP_ROOT%\T1Remote\T1Remote.exe" (
    powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%APP_ROOT%\T1Remote\T1Remote.exe' -WorkingDirectory '%APP_ROOT%\T1Remote' -Verb RunAs"
    exit /b %errorlevel%
)

echo 未找到 T1Remote.exe 或 Python 源码 App。
echo 请确认已完整解压 Release 包，并安装 Python 3.11 或更高版本。
pause
exit /b 1
