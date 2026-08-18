@echo off
setlocal
title Stop Analyzer Packet Capture
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_analyzer_windows.ps1" -StopOnly
if errorlevel 1 pause
endlocal
