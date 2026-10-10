"""Точка входа: запуск Telegram-бота.

Платформа 1С и её COM-коннектор 32-битные, поэтому бот работает только в 32-битном Python.
Если рядом с проектом есть окружение .venv32 (см. README, шаг 3), скрипт всегда перезапускает себя в нём,
каким бы Python его ни запустили (miniconda, системный, двойным щелчком). Без .venv32 бот запустится только
в 32-битном Python с установленными зависимостями. Запуск: python run_bot.py
"""
import os
import struct
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY32 = os.path.join(BASE_DIR, '.venv32', 'Scripts', 'python.exe')
REEXEC_FLAG = 'ALFABOT_IN_VENV'


def _same_path(a: str, b: str) -> bool:
    """True, если два пути указывают на один и тот же файл (без учёта регистра и слэшей)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


if os.path.exists(PY32) and not os.environ.get(REEXEC_FLAG) and not _same_path(sys.executable, PY32):
    sys.exit(subprocess.call([PY32, os.path.abspath(__file__)] + sys.argv[1:],
                             env={**os.environ, REEXEC_FLAG: '1'}))

if struct.calcsize('P') * 8 != 32:
    sys.exit("Нужен 32-битный Python: создайте окружение .venv32 (см. README, шаг 3).")

sys.path.insert(0, os.path.join(BASE_DIR, 'src'))

try:
    from bot import AutoServiceBot  # noqa: E402
    from config import ConfigError  # noqa: E402
except ModuleNotFoundError as e:
    sys.exit(f"Не установлена зависимость «{e.name}». Установите пакеты в 32-битное окружение:\n"
             f"  {PY32} -m pip install -r requirements.txt\n"
             f"(если окружения .venv32 ещё нет, создайте его по README, шаг 3).")

if __name__ == '__main__':
    try:
        AutoServiceBot().run()
    except ConfigError as e:
        sys.exit(f"Ошибка настройки: {e}")
    except KeyboardInterrupt:
        print("\nБот остановлен")
