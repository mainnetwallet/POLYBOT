@echo off
setlocal
echo ==========================================
echo POLYBOT - update
echo ==========================================
echo Stop POLYBOT first by running .\stop.bat.
echo.
where git >nul 2>&1
if %errorlevel% neq 0 (
  echo ERROR: git is not installed or not on PATH.
  pause
  exit /b 1
)
if not exist venv (
  echo Virtual environment not found. Run .\setup.bat first.
  pause
  exit /b 1
)
echo [1/4] Pulling latest commits...
git pull --ff-only
if %errorlevel% neq 0 (
  echo ERROR: git pull failed. You may have local code changes.
  echo See the Updating section in README.md.
  pause
  exit /b 1
)
echo [2/4] Updating dependencies...
call venv\Scripts\pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo ERROR: dependency installation failed
  pause
  exit /b 1
)
echo [3/4] Updating database schema...
if not exist data mkdir data
call venv\Scripts\python -c "from polybot.database import Database; Database('data/polybot.db').initialize(); print('      Database OK')"
echo [4/4] Checking for new settings...
echo Compare .env with .env.example and config\settings.yaml with
echo config\settings.yaml.example and copy any new keys.
echo.
echo Update complete. Run .\start.bat to launch POLYBOT.
pause
