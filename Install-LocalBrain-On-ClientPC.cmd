@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-LocalBrain-On-ClientPC.ps1"
if errorlevel 1 (
  echo.
  echo LocalBrain setup failed. Keep this window open and report the error shown above.
  pause
  exit /b 1
)
echo.
echo LocalBrain setup and local service verification completed successfully.
pause
