#!/usr/bin/env python3
"""
Главный скрипт для запуска бота
"""
import sys
import os

# Добавляем src в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from bot import AutoServiceBot

if __name__ == '__main__':
    try:
        bot = AutoServiceBot()
        bot.run()
    except KeyboardInterrupt:
        print("\n\nБот остановлен пользователем")
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)
