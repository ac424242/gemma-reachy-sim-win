@echo off
REM Double-click launcher for the Reachy robot stack (Windows).
REM Forwards any extra args to the PowerShell script, e.g.:
REM   StartReachy.bat -CameraSource http://192.168.4.159:8080/shot.jpg

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_robot.ps1" %*
if errorlevel 1 (
    echo.
    echo Startup failed. See messages above.
) else (
    echo.
    echo Reachy is running. Close this window anytime - services keep running.
)
echo.
pause
