"""Настройки бота: чтение и проверка файла .env (в корне проекта).

Все параметры, которые меняются от компьютера к компьютеру (токен, путь к базе 1С, логин и пароль,
способ подключения), задаются только в .env — в коде зашитых путей нет. Шаблон — .env.example.

Подключение к 1С бывает двух видов:
    файловая база   — задаётся ONE_C_DB_PATH (каталог, где лежит 1Cv8.1CD);
    серверная база  — задаются ONE_C_SERVER и ONE_C_BASE (имя базы в кластере); если ONE_C_SERVER
                      указан, используется этот режим.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigError(ValueError):
    """Ошибка в настройках: текст понятен пользователю и подсказывает, что исправить в .env."""


@dataclass(frozen=True)
class Settings:
    """Проверенные настройки бота.

    token: токен Telegram-бота.
    users_config: полный путь к файлу пользователей (users.json).
    one_c_connection: часть строки соединения 1С без логина и пароля, например File='C:\\base'.
    one_c_description: то же в читаемом виде (для логов и сообщений).
    one_c_user, one_c_password: пользователь 1С (с правом «Внешнее соединение») и его пароль.
    one_c_progid: имя COM-коннектора (V83.COMConnector для платформы 8.3).
    one_c_currency: наименование валюты учёта для новых записей (пусто — определить автоматически).
    one_c_default_executor: ФИО сотрудника-исполнителя по умолчанию для работ в новых заказ-нарядах.
    """
    token: str
    users_config: str
    one_c_connection: str
    one_c_description: str
    one_c_user: str
    one_c_password: str
    one_c_progid: str
    one_c_currency: str
    one_c_default_executor: str = ""


def quote_1c(value: str) -> str:
    """Оборачивает значение для строки соединения 1С в одинарные кавычки (внутренние кавычки удваиваются)."""
    return "'" + value.replace("'", "''") + "'"


def load_settings(env_path: str = "") -> Settings:
    """Читает .env и возвращает проверенные настройки.

    Параметры: env_path — путь к .env (по умолчанию .env в корне проекта).

    Обязательно: TELEGRAM_TOKEN и либо ONE_C_DB_PATH (файловая база), либо ONE_C_SERVER + ONE_C_BASE.
    Необязательно: ONE_C_USER, ONE_C_PASSWORD, ONE_C_PROGID (по умолчанию V83.COMConnector),
    ONE_C_CURRENCY, USERS_CONFIG (по умолчанию config/users.json).

    Исключение: ConfigError с описанием проблемы (нет токена, нет каталога базы, нет файла 1Cv8.1CD…).
    """
    path = env_path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        raise ConfigError(f"Не найден файл настроек {path}. Скопируйте .env.example в .env и заполните его.")
    load_dotenv(path, override=True)

    def get(name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()

    token = get("TELEGRAM_TOKEN")
    if not token:
        raise ConfigError("В .env не задан TELEGRAM_TOKEN (токен бота от @BotFather).")

    server = get("ONE_C_SERVER")
    if server:
        base = get("ONE_C_BASE")
        if not base:
            raise ConfigError("В .env задан ONE_C_SERVER, но не задан ONE_C_BASE (имя базы на сервере 1С).")
        connection = f"Srvr={quote_1c(server)};Ref={quote_1c(base)}"
        description = f"сервер {server}, база {base}"
    else:
        db_path = get("ONE_C_DB_PATH")
        if not db_path:
            raise ConfigError("В .env не задан ONE_C_DB_PATH (каталог файловой базы 1С, где лежит 1Cv8.1CD).")
        if not os.path.isdir(db_path):
            raise ConfigError(f"ONE_C_DB_PATH указывает на несуществующий каталог: {db_path}")
        if not os.path.exists(os.path.join(db_path, "1Cv8.1CD")):
            raise ConfigError(f"В каталоге {db_path} нет файла базы 1Cv8.1CD. Проверьте ONE_C_DB_PATH.")
        connection = f"File={quote_1c(db_path)}"
        description = f"файловая база {db_path}"

    users = get("USERS_CONFIG", "config/users.json")
    return Settings(
        token=token,
        users_config=users if os.path.isabs(users) else os.path.join(BASE_DIR, users),
        one_c_connection=connection,
        one_c_description=description,
        one_c_user=get("ONE_C_USER"),
        one_c_password=os.getenv("ONE_C_PASSWORD", ""),
        one_c_progid=get("ONE_C_PROGID", "V83.COMConnector"),
        one_c_currency=get("ONE_C_CURRENCY"),
        one_c_default_executor=get("ONE_C_DEFAULT_EXECUTOR"),
    )
