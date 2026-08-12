@echo off
setlocal
title Full Network Packet Capture (unfiltered)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_selectra_windows.ps1" -CaptureAll
if errorlevel 1 (
  echo.
  echo The capture did not complete. Read the error above.
  pause
)
endlocal
