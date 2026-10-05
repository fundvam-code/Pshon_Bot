"""Пользователи бота и их права.

Список пользователей хранится в config/users.json (в git не попадает — личные данные;
шаблон — config/users.example.json). Формат:
    {"users": [{"telegram_id": 123, "name": "Имя", "role": "admin|master",
                "permissions": ["view_clients", "add_client", ...]}]}
Бот отвечает только пользователям из этого файла. Изменения файла применяются после перезапуска бота.
"""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AccessControl:
    """Проверка регистрации и прав пользователей по config/users.json."""

    def __init__(self, users_config_path: str):
        """Загружает пользователей из файла.

        Параметр: users_config_path — путь к users.json.
        """
        self.users_config_path = users_config_path
        self.users = {}
        self.load_users()

    def load_users(self):
        """Читает users.json в словарь {telegram_id: данные}. При ошибке чтения пишет её в лог
        (список пользователей остаётся пустым — бот никому не ответит).
        """
        try:
            with open(self.users_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Преобразуем в словарь по telegram_id для быстрого поиска
            for user in config.get('users', []):
                self.users[user['telegram_id']] = user

            logger.info(f"Загружены данные {len(self.users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка при загрузке конфигурации пользователей: {e}")

    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Возвращает данные пользователя (имя, роль, права) или None, если его нет в файле."""
        return self.users.get(telegram_id)

    def is_user_registered(self, telegram_id: int) -> bool:
        """True, если Telegram ID есть в users.json."""
        return telegram_id in self.users

    def has_permission(self, telegram_id: int, permission: str) -> bool:
        """True, если у пользователя есть указанное право (строка вроде 'add_car', см. PermissionChecker)."""
        user = self.get_user(telegram_id)
        if not user:
            return False

        permissions = user.get('permissions', [])
        return permission in permissions

    def get_user_permissions(self, telegram_id: int) -> List[str]:
        """Возвращает список прав пользователя (пустой, если пользователя нет)."""
        user = self.get_user(telegram_id)
        if not user:
            return []
        return user.get('permissions', [])

    def get_user_role(self, telegram_id: int) -> Optional[str]:
        """Возвращает роль пользователя ('admin', 'master' …) или None."""
        user = self.get_user(telegram_id)
        if not user:
            return None
        return user.get('role')

    def is_admin(self, telegram_id: int) -> bool:
        """True, если роль пользователя 'admin'."""
        return self.get_user_role(telegram_id) == 'admin'

    def is_master(self, telegram_id: int) -> bool:
        """True, если роль пользователя 'master'."""
        return self.get_user_role(telegram_id) == 'master'

    def add_user(self, telegram_id: int, name: str, role: str,
                 permissions: List[str]) -> bool:
        """Добавляет (или заменяет) пользователя и сразу сохраняет users.json.

        Параметры: telegram_id, name, role, permissions (список прав). Возвращает True/False.
        В боте пока не вызывается из меню: используется при ручном администрировании.
        """
        try:
            self.users[telegram_id] = {
                'telegram_id': telegram_id,
                'name': name,
                'role': role,
                'permissions': permissions
            }

            # Сохраняем в файл
            config = {'users': list(self.users.values())}
            with open(self.users_config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            logger.info(f"Пользователь '{name}' добавлен с ролью '{role}'")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении пользователя: {e}")
            return False

    def remove_user(self, telegram_id: int) -> bool:
        """Удаляет пользователя и сохраняет users.json. Возвращает True, если пользователь был."""
        try:
            if telegram_id in self.users:
                del self.users[telegram_id]

                # Сохраняем в файл
                config = {'users': list(self.users.values())}
                with open(self.users_config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)

                logger.info(f"Пользователь {telegram_id} удален")
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка при удалении пользователя: {e}")
            return False

    def get_all_users(self) -> List[Dict]:
        """Возвращает список всех пользователей."""
        return list(self.users.values())


class PermissionChecker:
    """Названия прав (константы) и их описания.

    Используются в боте: VIEW_CLIENTS, ADD_CLIENT, ADD_CAR, VIEW_WORK. Остальные заведены на будущее
    (редактирование/удаление, управление пользователями) и пока в меню не используются.
    """

    # Константы разрешений
    VIEW_CLIENTS = "view_clients"
    ADD_CLIENT = "add_client"
    EDIT_CLIENT = "edit_client"
    DELETE_CLIENT = "delete_client"

    VIEW_CARS = "view_cars"
    ADD_CAR = "add_car"
    EDIT_CAR = "edit_car"
    DELETE_CAR = "delete_car"

    VIEW_WORK = "view_work"
    ADD_WORK = "add_work"
    EDIT_WORK = "edit_work"
    DELETE_WORK = "delete_work"

    MANAGE_USERS = "manage_users"

    # Описания разрешений
    PERMISSION_NAMES = {
        VIEW_CLIENTS: "Просмотр клиентов",
        ADD_CLIENT: "Добавление клиентов",
        EDIT_CLIENT: "Редактирование клиентов",
        DELETE_CLIENT: "Удаление клиентов",
        VIEW_CARS: "Просмотр автомобилей",
        ADD_CAR: "Добавление автомобилей",
        EDIT_CAR: "Редактирование автомобилей",
        DELETE_CAR: "Удаление автомобилей",
        VIEW_WORK: "Просмотр работ",
        ADD_WORK: "Добавление работ",
        EDIT_WORK: "Редактирование работ",
        DELETE_WORK: "Удаление работ",
        MANAGE_USERS: "Управление пользователями"
    }
