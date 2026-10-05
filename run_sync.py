#!/usr/bin/env python3
"""
Скрипт для синхронизации данных с 1С
"""
import sys
import os
import logging

# Добавляем src в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from dotenv import load_dotenv
from one_c import OneC
from db import BotDatabase
from sync import DataSync

# Конфигурируем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Главная функция синхронизации"""
    load_dotenv()

    logger.info("=" * 50)
    logger.info("Запуск синхронизации данных с 1С")
    logger.info("=" * 50)

    try:
        # Инициализируем компоненты
        one_c = OneC(
            os.getenv('ONE_C_DB_PATH', 'C:\\1C\\Pshon'),
            os.getenv('ONE_C_USER', ''),
            os.getenv('ONE_C_PASSWORD', '')
        )

        db = BotDatabase(os.getenv('SQLITE_DB', 'bot_data.db'))
        db.init()

        # Подключаемся к 1С
        logger.info("Подключение к 1С базе...")
        if not one_c.connect():
            logger.error("Не удалось подключиться к 1С!")
            return False

        logger.info("✅ Успешное подключение к 1С")

        # Выполняем синхронизацию
        sync = DataSync(one_c, db)
        logger.info("Начинаю полную синхронизацию...")

        if sync.sync_all():
            logger.info("✅ Синхронизация завершена успешно")
        else:
            logger.error("❌ Синхронизация завершена с ошибками")

        # Закрываем подключения
        one_c.disconnect()
        db.close()

        logger.info("=" * 50)
        return True

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
