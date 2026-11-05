@echo off
setlocal
echo ==========================================
echo POLYBOT - stopping
echo ==========================================
echo Requesting shutdown of POLYBOT processes...
taskkill /F /FI "IMAGENAME eq python.exe" /FI "COMMANDLINE eq *polybot*" >nul 2>&1
if %errorlevel% equ 0 (
  echo POLYBOT process terminated.
) else (
  echo No running POLYBOT process found (or already stopped).
)
echo.
echo To also cancel all open orders, use the dashboard Emergency Stop before
echo shutting down, or restart and run recovery.
pause
