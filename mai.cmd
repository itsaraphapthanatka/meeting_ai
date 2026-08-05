@echo off
rem meeting_ai launcher for Windows. Keep this file ASCII-only:
rem cmd.exe reads .cmd in the OEM codepage and mangles UTF-8 bytes.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
python -m meeting_ai %*
exit /b %ERRORLEVEL%
