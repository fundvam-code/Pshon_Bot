"""
Система управления доступом и правами пользователей
"""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AccessControl:
    """Система управления правами доступа"""

    def __init__(self, users_config_path: str):
        """
        Инициализация системы доступа

        Args:
            users_config_path: Путь к файлу конфигурации пользователей
        """
        self.users_config_path = users_config_path
        self.users = {}
        self.load_users()

    def load_users(self):
        """Загрузить конфигурацию пользователей из файла"""
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
        """Получить данные пользователя"""
        return self.users.get(telegram_id)

    def is_user_registered(self, telegram_id: int) -> bool:
        """Проверить, зарегистрирован ли пользователь"""
        return telegram_id in self.users

    def has_permission(self, telegram_id: int, permission: str) -> bool:
        """
        Проверить, есть ли у пользователя разрешение

        Args:
            telegram_id: ID пользователя Telegram
            permission: Название разрешения

        Returns:
            True если есть разрешение
        """
        user = self.get_user(telegram_id)
        if not user:
            return False

        permissions = user.get('permissions', [])
        return permission in permissions

    def get_user_permissions(self, telegram_id: int) -> List[str]:
        """Получить список разрешений пользователя"""
        user = self.get_user(telegram_id)
        if not user:
            return []
        return user.get('permissions', [])

    def get_user_role(self, telegram_id: int) -> Optional[str]:
        """Получить роль пользователя"""
        user = self.get_user(telegram_id)
        if not user:
            return None
        return user.get('role')

    def is_admin(self, telegram_id: int) -> bool:
        """Проверить, является ли пользователь администратором"""
        return self.get_user_role(telegram_id) == 'admin'

    def is_master(self, telegram_id: int) -> bool:
        """Проверить, является ли пользователь мастером"""
        return self.get_user_role(telegram_id) == 'master'

    def add_user(self, telegram_id: int, name: str, role: str,
                 permissions: List[str]) -> bool:
        """
        Добавить нового пользователя

        Args:
            telegram_id: ID в Telegram
            name: ФИО пользователя
            role: Роль (admin, master, etc)
            permissions: Список разрешений

        Returns:
            True если успешно
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
        """Удалить пользователя"""
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
        """Получить всех пользователей"""
        return list(self.users.values())


class PermissionChecker:
    """Вспомогательный класс для проверки разрешений"""

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
