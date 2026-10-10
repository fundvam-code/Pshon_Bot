@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Проверка: бот не запущен? ===
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*run_bot.py*' }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo [!] Бот уже запущен, поэтому обновить его сейчас нельзя.
    echo     Сначала закройте его ^(закройте окно с ботом или завершите процесс python в Диспетчере задач^),
    echo     затем запустите этот файл снова.
    echo.
    pause
    exit /b 1
)

echo === Обновление с GitHub ===
git pull --ff-only
if errorlevel 1 echo [!] Не удалось обновить проект. Запускаю текущую версию.

if not exist ".venv32\Scripts\python.exe" (
    echo [!] Не найдено окружение .venv32. Сначала создайте его, см. README, шаг 3.
    pause
    exit /b 1
)

echo === Обновление зависимостей ===
".venv32\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo В .venv32 нет pip, устанавливаю...
    ".venv32\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1
)
".venv32\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 echo [!] Зависимости не обновлены. Продолжаю запуск.

echo === Запуск бота ===
".venv32\Scripts\python.exe" run_bot.py

echo.
echo Бот остановлен.
pause
