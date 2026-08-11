@echo off
setlocal
title Selectra Full Packet Capture
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_selectra_windows.ps1"
if errorlevel 1 (
  echo.
  echo The capture did not complete. Read the error above.
  pause
)
endlocal
