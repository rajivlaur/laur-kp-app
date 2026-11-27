@echo off
setlocal
if not exist "%~dp0runtime\pythonw.exe" (
  echo Please extract all files from the ZIP before opening KP Studio.
  pause
  exit /b 1
)
if /i "%~1"=="--diagnostic" (
  "%~dp0runtime\python.exe" -I -B "%~dp0app\kp_studio.py"
  pause
  exit /b
)
start "KP Studio" /D "%~dp0app" "%~dp0runtime\pythonw.exe" -I -B "%~dp0app\kp_studio.py"
