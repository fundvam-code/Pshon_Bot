"""
Модуль для работы с SQLite базой данных бота
"""
import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class BotDatabase:
    """Класс для работы с SQLite базой"""

    def __init__(self, db_path: str = "bot_data.db"):
        """
        Инициализация базы данных

        Args:
            db_path: Путь к файлу SQLite базы
        """
        self.db_path = db_path
        self.connection = None
        self.cursor = None

    def init(self):
        """Инициализация и создание таблиц"""
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.cursor = self.connection.cursor()

            # Таблица клиентов
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                one_c_id TEXT UNIQUE,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # Таблица автомобилей
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id TEXT PRIMARY KEY,
                one_c_id TEXT UNIQUE,
                client_id TEXT NOT NULL,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                gos_number TEXT UNIQUE,
                vin TEXT,
                year INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id)
            )
            """)

            # Таблица логов синхронизации
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT,
                status TEXT,
                message TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # Таблица для истории изменений
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                action TEXT,
                table_name TEXT,
                record_id TEXT,
                changes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            self.connection.commit()
            logger.info(f"База данных инициализирована: {self.db_path}")

        except Exception as e:
            logger.error(f"Ошибка при инициализации БД: {e}")

    def close(self):
        """Закрытие подключения"""
        if self.connection:
            self.connection.close()

    # === КЛИЕНТЫ ===

    def add_client(self, name: str, phone: str = "", email: str = "",
                   address: str = "", one_c_id: str = "") -> bool:
        """Добавить клиента"""
        try:
            self.cursor.execute("""
            INSERT INTO clients (id, one_c_id, name, phone, email, address)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (f"cli_{datetime.now().timestamp()}", one_c_id, name, phone, email, address))

            self.connection.commit()
            logger.info(f"Клиент '{name}' добавлен")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении клиента: {e}")
            return False

    def get_client(self, client_id: str) -> Optional[Dict]:
        """Получить клиента по ID"""
        try:
            self.cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
            row = self.cursor.fetchone()

            if row:
                return {
                    "id": row[0],
                    "one_c_id": row[1],
                    "name": row[2],
                    "phone": row[3],
                    "email": row[4],
                    "address": row[5]
                }
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении клиента: {e}")
            return None

    def search_clients(self, query: str) -> List[Dict]:
        """Поиск клиентов по названию или номеру"""
        try:
            self.cursor.execute("""
            SELECT * FROM clients
            WHERE name LIKE ? OR phone LIKE ?
            ORDER BY name
            """, (f"%{query}%", f"%{query}%"))

            rows = self.cursor.fetchall()
            clients = []
            for row in rows:
                clients.append({
                    "id": row[0],
                    "one_c_id": row[1],
                    "name": row[2],
                    "phone": row[3],
                    "email": row[4],
                    "address": row[5]
                })
            return clients
        except Exception as e:
            logger.error(f"Ошибка при поиске клиентов: {e}")
            return []

    def get_all_clients(self) -> List[Dict]:
        """Получить всех клиентов"""
        try:
            self.cursor.execute("SELECT * FROM clients ORDER BY name")
            rows = self.cursor.fetchall()
            clients = []
            for row in rows:
                clients.append({
                    "id": row[0],
                    "one_c_id": row[1],
                    "name": row[2],
                    "phone": row[3],
                    "email": row[4],
                    "address": row[5]
                })
            return clients
        except Exception as e:
            logger.error(f"Ошибка при получении всех клиентов: {e}")
            return []

    # === АВТОМОБИЛИ ===

    def add_car(self, client_id: str, brand: str, model: str,
                gos_number: str, vin: str = "", year: int = 0,
                one_c_id: str = "") -> bool:
        """Добавить автомобиль"""
        try:
            self.cursor.execute("""
            INSERT INTO cars (id, one_c_id, client_id, brand, model,
                            gos_number, vin, year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"car_{datetime.now().timestamp()}", one_c_id, client_id,
                  brand, model, gos_number, vin, year))

            self.connection.commit()
            logger.info(f"Автомобиль '{brand} {model}' добавлен")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении автомобиля: {e}")
            return False

    def get_client_cars(self, client_id: str) -> List[Dict]:
        """Получить автомобили клиента"""
        try:
            self.cursor.execute("""
            SELECT * FROM cars WHERE client_id = ? ORDER BY created_at DESC
            """, (client_id,))

            rows = self.cursor.fetchall()
            cars = []
            for row in rows:
                cars.append({
                    "id": row[0],
                    "one_c_id": row[1],
                    "client_id": row[2],
                    "brand": row[3],
                    "model": row[4],
                    "gos_number": row[5],
                    "vin": row[6],
                    "year": row[7]
                })
            return cars
        except Exception as e:
            logger.error(f"Ошибка при получении автомобилей: {e}")
            return []

    def get_car(self, car_id: str) -> Optional[Dict]:
        """Получить автомобиль по ID"""
        try:
            self.cursor.execute("SELECT * FROM cars WHERE id = ?", (car_id,))
            row = self.cursor.fetchone()

            if row:
                return {
                    "id": row[0],
                    "one_c_id": row[1],
                    "client_id": row[2],
                    "brand": row[3],
                    "model": row[4],
                    "gos_number": row[5],
                    "vin": row[6],
                    "year": row[7]
                }
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении автомобиля: {e}")
            return None

    # === ЛОГИРОВАНИЕ ===

    def log_action(self, user_id: str, action: str, table_name: str,
                   record_id: str, changes: Dict = None):
        """Логировать действие пользователя"""
        try:
            changes_json = json.dumps(changes) if changes else ""
            self.cursor.execute("""
            INSERT INTO audit_log (user_id, action, table_name, record_id, changes)
            VALUES (?, ?, ?, ?, ?)
            """, (user_id, action, table_name, record_id, changes_json))

            self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка при логировании действия: {e}")

    def log_sync(self, sync_type: str, status: str, message: str = ""):
        """Логировать синхронизацию"""
        try:
            self.cursor.execute("""
            INSERT INTO sync_log (sync_type, status, message)
            VALUES (?, ?, ?)
            """, (sync_type, status, message))

            self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка при логировании синхронизации: {e}")
