"""Запуск бота. COM-коннектор 1С 32-битный, поэтому бот работает только в 32-битном Python (.venv32)."""
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
