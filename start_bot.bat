@echo off
cd /d "%~dp0"

echo === Updating from GitHub ===
git pull --ff-only
if errorlevel 1 echo [!] Update failed. Starting the current version.

if not exist ".venv32\Scripts\python.exe" (
    echo [!] .venv32 not found. Create it first, see README step 3.
    pause
    exit /b 1
)

echo === Updating dependencies ===
".venv32\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip not found in .venv32, installing it...
    ".venv32\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1
)
".venv32\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 echo [!] Dependencies were not updated. Continuing.

echo === Starting bot ===
".venv32\Scripts\python.exe" run_bot.py

echo.
echo Bot stopped.
pause
