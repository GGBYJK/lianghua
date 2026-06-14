@echo off
setlocal
cd /d "%~dp0"
title Lianghua - Start Services

echo Restarting backend and frontend...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo Startup failed. Check the error above and the log files.
) else (
    echo Backend:  http://127.0.0.1:8010
    echo Frontend: http://127.0.0.1:5173
)
echo.
pause
exit /b %EXIT_CODE%
