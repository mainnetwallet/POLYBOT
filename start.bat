@echo off
setlocal
echo ==========================================
echo POLYBOT - starting
echo ==========================================
if not exist venv (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
echo.
echo Mode: PAPER by default (LIVE locked until explicitly activated)
echo Dashboard: http://127.0.0.1:8000   (bound to 127.0.0.1 only)
echo Logs: logs/  |  Data: data/
echo.
echo Press Ctrl+C to stop.
echo ==========================================
echo.
python -m polybot
