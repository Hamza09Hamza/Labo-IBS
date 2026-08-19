@echo off
setlocal
title Analyzer TCP and UDP Packet Capture
echo.
echo Analyzer TCP and UDP discovery capture
echo.
set /p TARGET_IP=Analyzer IPv4 address (leave blank to capture ALL TCP and UDP):
echo.
if "%TARGET_IP%"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_analyzer_windows.ps1" -AllTcpUdp
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_analyzer_windows.ps1" -TargetIP "%TARGET_IP%"
)
if errorlevel 1 (
  echo.
  echo The capture did not complete. Read the error above.
  pause
)
endlocal
