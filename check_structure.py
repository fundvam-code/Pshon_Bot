"""Диагностика подключения к 1С.

Подключается к базе по настройкам из .env (см. .env.example) и выводит список справочников и
документов. Запускать из 32-битного окружения:
    .venv32/Scripts/python.exe check_structure.py
Если список выведен — бот сможет работать с этой базой. При ошибке показывает причину
(неверный путь, логин/пароль, нет права «Внешнее соединение», не зарегистрирован COM-коннектор).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from config import ConfigError, load_settings  # noqa: E402
from one_c import OneC  # noqa: E402


def main() -> int:
    """Подключается к 1С и печатает справочники и документы. Возвращает код завершения (0 — успех)."""
    try:
        s = load_settings()
    except ConfigError as e:
        print(f"Ошибка настройки: {e}")
        return 2

    print(f"Подключение: {s.one_c_description} (коннектор {s.one_c_progid}, пользователь «{s.one_c_user}»)")
    one_c = OneC(s.one_c_connection, s.one_c_user, s.one_c_password,
                 progid=s.one_c_progid, description=s.one_c_description)
    if not one_c.connect():
        print("Не удалось подключиться к 1С. Причина записана выше в логе; типичные причины описаны в README.")
        return 1
    print("Подключено к 1С\n")

    meta = one_c.c.Метаданные
    for title, collection in (("СПРАВОЧНИКИ", meta.Справочники), ("ДОКУМЕНТЫ", meta.Документы)):
        print("=" * 60)
        print(f"{title} В БАЗЕ 1С")
        print("=" * 60)
        for i, item in enumerate(collection, 1):
            print(f"{i}. {item.Имя} ({item.Синоним})")
        print()
    print("Проверка завершена")
    return 0


if __name__ == '__main__':
    sys.exit(main())
