@echo off
echo Starting LaboBridge service...
C:\nssm\win64\nssm.exe start LaboBridge
echo.
echo Current status:
C:\nssm\win64\nssm.exe status LaboBridge
echo.
pause
