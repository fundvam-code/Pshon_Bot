@echo off
cd /d "%~dp0"

echo === Checking that the bot is not running ===
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*run_bot.py*' }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo [!] The bot is already running, so it cannot be updated.
    echo     Close it first ^(close its console window or end the python process in Task Manager^),
    echo     then run this file again.
    echo.
    pause
    exit /b 1
)

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
