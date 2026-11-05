@echo off
setlocal enabledelayedexpansion
echo ==========================================
echo POLYBOT setup (Windows)
echo ==========================================
echo.

rem --- Python check ---
python --version >nul 2>&1
if %errorlevel% neq 0 (
  echo ERROR: Python is not installed or not on PATH.
  echo Install Python 3.11+ from https://python.org and re-run setup.bat
  pause
  exit /b 1
)

rem --- Virtual environment ---
if not exist venv (
  echo [1/5] Creating virtual environment...
  python -m venv venv
  if %errorlevel% neq 0 (
    echo ERROR: failed to create venv
    pause
    exit /b 1
  )
) else (
  echo [1/5] Virtual environment already exists.
)

rem --- Dependencies ---
echo [2/5] Installing dependencies (requirements.txt)...
call venv\Scripts\pip install --upgrade pip >nul 2>&1
call venv\Scripts\pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo ERROR: dependency installation failed
  pause
  exit /b 1
)

rem --- Configuration files ---
if not exist .env (
  echo [3/5] Creating .env from .env.example ...
  copy .env.example .env >nul
  echo     Please edit .env with your wallet and capital settings.
) else (
  echo [3/5] .env already exists (left unchanged).
)

if not exist config\settings.yaml (
  echo [4/5] Creating config/settings.yaml from example ...
  copy config\settings.yaml.example config\settings.yaml >nul
) else (
  echo [4/5] config/settings.yaml already exists (left unchanged).
)

rem --- Directories ---
if not exist data mkdir data
if not exist logs mkdir logs

rem --- Database ---
echo [5/5] Initializing database...
call venv\Scripts\python -c "from polybot.database import Database; Database('data/polybot.db').initialize(); print('      SQLite WAL database initialized')"
if %errorlevel% neq 0 (
  echo WARNING: database initialization had issues (will retry at startup)
)

echo.
echo ==========================================
echo Setup complete.
echo ==========================================
echo 1. Edit .env with your wallet address and (optionally) private key.
echo 2. Set POLYBOT_CAPITAL_USD in .env before trading.
echo 3. Review config/settings.yaml.
echo 4. Run start.bat to launch POLYBOT (PAPER mode by default).
echo 5. Open http://127.0.0.1:8000 for the dashboard.
echo.
echo WARNING: LIVE trading is locked until you explicitly activate it in the
echo dashboard and the strict LIVE gate passes. No real funds are used in PAPER.
echo.
pause
