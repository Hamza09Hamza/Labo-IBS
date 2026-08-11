@echo off
setlocal
title Stop Selectra Packet Capture
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_selectra_windows.ps1" -StopOnly
endlocal
