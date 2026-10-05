"""
Модуль для подключения и работы с 1С:Альфа авто через COM объект
"""
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime

try:
    import win32com.client
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False
    logging.warning("pywin32 не установлен, COM подключение недоступно")

logger = logging.getLogger(__name__)


class OneC:
    """Класс для работы с 1С базой через COM"""

    def __init__(self, db_path: str, user: str = "", password: str = ""):
        """
        Инициализация подключения к 1С

        Args:
            db_path: Путь к базе 1С (например, C:\\1C\\Pshon)
            user: Пользователь 1С
            password: Пароль 1С
        """
        self.db_path = db_path
        self.user = user
        self.password = password
        self.connection = None
        self.is_connected = False

    def connect(self) -> bool:
        """Подключение к 1С базе"""
        if not HAS_WIN32COM:
            logger.error("pywin32 не установлен")
            return False

        try:
            # Создаем COM объект для подключения
            connector = win32com.client.Dispatch("V83.COMConnector")

            # Формируем строку подключения
            if self.user and self.password:
                connection_string = f"File='{self.db_path}';User='{self.user}';Password='{self.password}';"
            else:
                connection_string = f"File='{self.db_path}';"

            # Подключаемся
            self.connection = connector.Connect(connection_string)
            self.is_connected = True
            logger.info(f"Успешное подключение к 1С: {self.db_path}")
            return True

        except Exception as e:
            logger.error(f"Ошибка подключения к 1С: {e}")
            self.is_connected = False
            return False

    def disconnect(self):
        """Отключение от 1С"""
        if self.connection:
            try:
                self.connection.Close()
                self.is_connected = False
                logger.info("Отключение от 1С")
            except Exception as e:
                logger.error(f"Ошибка при отключении: {e}")

    def get_clients(self, search_query: str = "") -> List[Dict]:
        """
        Получить список клиентов из справочника

        Args:
            search_query: Строка для поиска по ФИО или контакту

        Returns:
            Список клиентов
        """
        if not self.is_connected:
            logger.error("Нет подключения к 1С")
            return []

        try:
            # Запрос клиентов из справочника
            query = """
            ВЫБРАТЬ
                Контрагенты.Ссылка КАК ID,
                Контрагенты.Описание КАК ФИО,
                Контрагенты.Контактная_информация КАК Контакты,
                Контрагенты.Адрес КАК Адрес
            ИЗ
                Справочник.Контрагенты КАК Контрагенты
            ГДЕ
                Контрагенты.ПометкаУдаления = ЛОЖЬ
            """

            if search_query:
                query += f" И Контрагенты.Описание ПОДОБНО '{search_query}%'"

            result = self.connection.NewObject("Запрос")
            result.Text = query
            table = result.Выполнить().Выгрузить()

            clients = []
            for row in table:
                clients.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "contact": row[2] if row[2] else "",
                    "address": row[3] if row[3] else ""
                })

            return clients

        except Exception as e:
            logger.error(f"Ошибка при получении клиентов: {e}")
            return []

    def get_client_cars(self, client_id: str) -> List[Dict]:
        """
        Получить автомобили клиента

        Args:
            client_id: ID клиента

        Returns:
            Список автомобилей
        """
        if not self.is_connected:
            logger.error("Нет подключения к 1С")
            return []

        try:
            query = """
            ВЫБРАТЬ
                Автомобили.Ссылка КАК ID,
                Автомобили.Описание КАК Название,
                Автомобили.Марка КАК Марка,
                Автомобили.Модель КАК Модель,
                Автомобили.ГосударственныйНомер КАК ГосНомер,
                Автомобили.VIN КАК VIN,
                Автомобили.ГодВыпуска КАК Год
            ИЗ
                Справочник.Автомобили КАК Автомобили
            ГДЕ
                Автомобили.Владелец = &ВладелецСсылка
                И Автомобили.ПометкаУдаления = ЛОЖЬ
            """

            result = self.connection.NewObject("Запрос")
            result.Text = query
            result.УстановитьПараметр("ВладелецСсылка", client_id)

            table = result.Выполнить().Выгрузить()

            cars = []
            for row in table:
                cars.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "brand": row[2] if row[2] else "",
                    "model": row[3] if row[3] else "",
                    "gos_number": row[4] if row[4] else "",
                    "vin": row[5] if row[5] else "",
                    "year": row[6] if row[6] else ""
                })

            return cars

        except Exception as e:
            logger.error(f"Ошибка при получении автомобилей: {e}")
            return []

    def add_client(self, name: str, contact: str = "", address: str = "") -> bool:
        """
        Добавить нового клиента

        Args:
            name: ФИО клиента
            contact: Контактная информация
            address: Адрес

        Returns:
            True если успешно
        """
        if not self.is_connected:
            logger.error("Нет подключения к 1С")
            return False

        try:
            # Создаем новый элемент справочника
            new_client = self.connection.Справочники.Контрагенты.СоздатьЭлемент()
            new_client.Описание = name
            new_client.Контактная_информация = contact
            new_client.Адрес = address
            new_client.Записать()

            logger.info(f"Клиент '{name}' добавлен")
            return True

        except Exception as e:
            logger.error(f"Ошибка при добавлении клиента: {e}")
            return False

    def add_car(self, client_id: str, brand: str, model: str,
                gos_number: str, vin: str, year: int) -> bool:
        """
        Добавить автомобиль для клиента

        Args:
            client_id: ID клиента-владельца
            brand: Марка
            model: Модель
            gos_number: Государственный номер
            vin: VIN номер
            year: Год выпуска

        Returns:
            True если успешно
        """
        if not self.is_connected:
            logger.error("Нет подключения к 1С")
            return False

        try:
            new_car = self.connection.Справочники.Автомобили.СоздатьЭлемент()
            new_car.Описание = f"{brand} {model} ({gos_number})"
            new_car.Владелец = client_id
            new_car.Марка = brand
            new_car.Модель = model
            new_car.ГосударственныйНомер = gos_number
            new_car.VIN = vin
            new_car.ГодВыпуска = year
            new_car.Записать()

            logger.info(f"Автомобиль '{brand} {model}' добавлен для клиента")
            return True

        except Exception as e:
            logger.error(f"Ошибка при добавлении автомобиля: {e}")
            return False
