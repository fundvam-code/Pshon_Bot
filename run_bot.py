"""Точка входа: запуск Telegram-бота.

Платформа 1С и её COM-коннектор 32-битные, поэтому бот работает только в 32-битном Python.
Если скрипт запущен 64-битным интерпретатором, он сам перезапускается в окружении .venv32
(см. README, шаг 3) и завершается с кодом дочернего процесса. Запуск: python run_bot.py
"""
import os
import struct
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY32 = os.path.join(BASE_DIR, '.venv32', 'Scripts', 'python.exe')

if struct.calcsize('P') * 8 != 32:
    if not os.path.exists(PY32):
        sys.exit("Нужен 32-битный Python: создайте окружение .venv32 (см. README).")
    sys.exit(subprocess.call([PY32, os.path.abspath(__file__)] + sys.argv[1:]))

sys.path.insert(0, os.path.join(BASE_DIR, 'src'))

from bot import AutoServiceBot  # noqa: E402

if __name__ == '__main__':
    try:
        AutoServiceBot().run()
    except KeyboardInterrupt:
        print("\nБот остановлен")
