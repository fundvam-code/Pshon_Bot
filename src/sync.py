"""
Модуль синхронизации данных из 1С в SQLite
"""
import logging
from datetime import datetime
from one_c import OneC
from db import BotDatabase

logger = logging.getLogger(__name__)


class DataSync:
    """Класс для синхронизации данных"""

    def __init__(self, one_c: OneC, db: BotDatabase):
        """
        Инициализация синхронизации

        Args:
            one_c: Объект подключения к 1С
            db: Объект базы данных SQLite
        """
        self.one_c = one_c
        self.db = db

    def sync_clients(self) -> bool:
        """Синхронизировать клиентов из 1С"""
        if not self.one_c.is_connected:
            logger.warning("1С не подключена, синхронизация невозможна")
            return False

        try:
            logger.info("Начало синхронизации клиентов...")
            clients = self.one_c.get_clients()

            synced_count = 0
            for client in clients:
                # Добавляем в локальную БД
                success = self.db.add_client(
                    name=client.get('name', 'Unknown'),
                    phone=client.get('contact', ''),
                    address=client.get('address', ''),
                    one_c_id=client.get('id', '')
                )
                if success:
                    synced_count += 1

            self.db.log_sync('clients', 'success', f'Синхронизировано {synced_count} клиентов')
            logger.info(f"Синхронизировано {synced_count} клиентов")
            return True

        except Exception as e:
            logger.error(f"Ошибка при синхронизации клиентов: {e}")
            self.db.log_sync('clients', 'error', str(e))
            return False

    def sync_cars(self, client_id: str) -> bool:
        """
        Синхронизировать автомобили клиента

        Args:
            client_id: ID клиента

        Returns:
            True если успешно
        """
        if not self.one_c.is_connected:
            logger.warning("1С не подключена, синхронизация невозможна")
            return False

        try:
            logger.info(f"Синхронизация автомобилей для клиента {client_id}...")

            # Получаем информацию о клиенте из локальной БД
            client = self.db.get_client(client_id)
            if not client:
                logger.error(f"Клиент {client_id} не найден")
                return False

            # Если у клиента есть ID в 1С, получаем его машины
            if client.get('one_c_id'):
                cars = self.one_c.get_client_cars(client['one_c_id'])

                synced_count = 0
                for car in cars:
                    success = self.db.add_car(
                        client_id=client_id,
                        brand=car.get('brand', ''),
                        model=car.get('model', ''),
                        gos_number=car.get('gos_number', ''),
                        vin=car.get('vin', ''),
                        year=car.get('year', 0),
                        one_c_id=car.get('id', '')
                    )
                    if success:
                        synced_count += 1

                self.db.log_sync('cars', 'success',
                               f'Синхронизировано {synced_count} машин для клиента')
                logger.info(f"Синхронизировано {synced_count} машин")
                return True
            else:
                logger.warning(f"У клиента {client_id} нет ID в 1С")
                return False

        except Exception as e:
            logger.error(f"Ошибка при синхронизации машин: {e}")
            self.db.log_sync('cars', 'error', str(e))
            return False

    def sync_all(self) -> bool:
        """Полная синхронизация всех данных"""
        logger.info("Начало полной синхронизации...")

        # Синхронизируем клиентов
        clients_success = self.sync_clients()

        if clients_success:
            # Получаем всех клиентов и синхронизируем их машины
            all_clients = self.db.get_all_clients()
            for client in all_clients:
                self.sync_cars(client['id'])

        logger.info("Полная синхронизация завершена")
        return clients_success


def main():
    """Тестирование синхронизации"""
    import os
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

    # Конфигурируем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Инициализируем компоненты
    one_c = OneC(
        os.getenv('ONE_C_DB_PATH', 'C:\\1C\\Pshon'),
        os.getenv('ONE_C_USER', ''),
        os.getenv('ONE_C_PASSWORD', '')
    )

    db = BotDatabase(os.getenv('SQLITE_DB', 'bot_data.db'))
    db.init()

    # Подключаемся к 1С и синхронизируем
    if one_c.connect():
        sync = DataSync(one_c, db)
        sync.sync_all()
        one_c.disconnect()
    else:
        logger.error("Не удалось подключиться к 1С")

    db.close()


if __name__ == '__main__':
    main()
