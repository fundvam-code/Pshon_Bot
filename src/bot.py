"""
Telegram бот для управления справочниками Альфа авто
"""
import os
import logging
from typing import Optional
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

from db import BotDatabase
from one_c import OneC
from access import AccessControl, PermissionChecker

# Загрузим переменные окружения
load_dotenv()

# Конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
MENU_STATE = 0
ADD_CLIENT_NAME = 1
ADD_CLIENT_PHONE = 2
ADD_CLIENT_EMAIL = 3
ADD_CLIENT_ADDRESS = 4
ADD_CAR_BRAND = 5
ADD_CAR_MODEL = 6
ADD_CAR_GOS_NUMBER = 7
ADD_CAR_VIN = 8
ADD_CAR_YEAR = 9
SEARCH_CLIENT = 10


class AutoServiceBot:
    """Главный класс бота"""

    def __init__(self):
        """Инициализация бота"""
        self.token = os.getenv('TELEGRAM_TOKEN')
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN не найден в .env файле")

        # Инициализируем компоненты
        self.db = BotDatabase(os.getenv('SQLITE_DB', 'bot_data.db'))
        self.db.init()

        self.access = AccessControl(os.getenv('USERS_CONFIG', 'config/users.json'))

        self.one_c_db_path = os.getenv('ONE_C_DB_PATH', 'C:\\1C\\Pshon')
        self.one_c_user = os.getenv('ONE_C_USER', '')
        self.one_c_password = os.getenv('ONE_C_PASSWORD', '')

        self.one_c = OneC(self.one_c_db_path, self.one_c_user, self.one_c_password)

        logger.info("Компоненты бота инициализированы")

    def get_main_keyboard(self, user_id: int):
        """Получить главное меню в зависимости от прав"""
        keyboard = []

        if self.access.has_permission(user_id, PermissionChecker.VIEW_CLIENTS):
            keyboard.append(['🔍 Найти клиента'])

        if self.access.has_permission(user_id, PermissionChecker.ADD_CLIENT):
            keyboard.append(['➕ Добавить клиента'])

        if self.access.has_permission(user_id, PermissionChecker.VIEW_CARS):
            keyboard.append(['🚗 Мои машины'])

        if self.access.has_permission(user_id, PermissionChecker.ADD_CAR):
            keyboard.append(['➕ Добавить машину'])

        if self.access.is_admin(user_id):
            keyboard.append(['👥 Управление пользователями'])

        keyboard.append(['ℹ️ О боте'])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Проверяем, зарегистрирован ли пользователь
        if not self.access.is_user_registered(user_id):
            await update.message.reply_text(
                f"Привет, {user_name}! 👋\n\n"
                f"К сожалению, вы не зарегистрированы в системе.\n"
                f"Обратитесь к администратору для добавления."
            )
            return

        user = self.access.get_user(user_id)
        role_name = user.get('name', 'Пользователь')

        await update.message.reply_text(
            f"Добро пожаловать, {role_name}! 🚗\n\n"
            f"Выберите действие:",
            reply_markup=self.get_main_keyboard(user_id)
        )

        logger.info(f"Пользователь {role_name} ({user_id}) начал работу")
        return MENU_STATE

    async def handle_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик выбора пункта меню"""
        user_id = update.effective_user.id
        text = update.message.text

        # Проверяем разрешение
        if not self.access.is_user_registered(user_id):
            await update.message.reply_text("❌ Вы не авторизованы")
            return MENU_STATE

        if text == '🔍 Найти клиента':
            if not self.access.has_permission(user_id, PermissionChecker.VIEW_CLIENTS):
                await update.message.reply_text("❌ У вас нет прав на просмотр клиентов")
                return MENU_STATE

            await update.message.reply_text(
                "Введите ФИО или телефон клиента:"
            )
            return SEARCH_CLIENT

        elif text == '➕ Добавить клиента':
            if not self.access.has_permission(user_id, PermissionChecker.ADD_CLIENT):
                await update.message.reply_text("❌ У вас нет прав на добавление клиентов")
                return MENU_STATE

            await update.message.reply_text(
                "Введите ФИО клиента:",
                reply_markup=ReplyKeyboardRemove()
            )
            return ADD_CLIENT_NAME

        elif text == '➕ Добавить машину':
            if not self.access.has_permission(user_id, PermissionChecker.ADD_CAR):
                await update.message.reply_text("❌ У вас нет прав на добавление машин")
                return MENU_STATE

            await update.message.reply_text(
                "Сначала выберите клиента. Введите его ФИО:"
            )
            return SEARCH_CLIENT

        elif text == 'ℹ️ О боте':
            await update.message.reply_text(
                "🚗 Бот управления справочниками Альфа авто\n\n"
                "Функции:\n"
                "• Просмотр и поиск клиентов\n"
                "• Просмотр автомобилей клиентов\n"
                "• Добавление новых клиентов и машин\n\n"
                "Версия: 1.0\n"
                "Разработано для автосервиса"
            )
            return MENU_STATE

        else:
            await update.message.reply_text(
                "Не понимаю. Выберите действие из меню:"
            )
            return MENU_STATE

    async def handle_search_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик поиска клиента"""
        user_id = update.effective_user.id
        query = update.message.text.strip()

        # Ищем в локальной базе
        clients = self.db.search_clients(query)

        if not clients:
            await update.message.reply_text(
                f"❌ Клиентов не найдено по запросу: {query}\n\n"
                "Попробуйте еще раз:",
                reply_markup=self.get_main_keyboard(user_id)
            )
            return MENU_STATE

        # Выводим результаты
        response = "Найденные клиенты:\n\n"
        for i, client in enumerate(clients, 1):
            response += f"{i}. {client['name']}\n"
            if client['phone']:
                response += f"   ☎️ {client['phone']}\n"
            if client['email']:
                response += f"   📧 {client['email']}\n"
            if client['address']:
                response += f"   📍 {client['address']}\n"

            # Получаем машины клиента
            cars = self.db.get_client_cars(client['id'])
            if cars:
                response += "   🚗 Автомобили:\n"
                for car in cars:
                    response += (
                        f"      • {car['brand']} {car['model']} "
                        f"({car['gos_number']})"
                    )
                    if car['vin']:
                        response += f" VIN: {car['vin']}"
                    if car['year']:
                        response += f" {car['year']}г."
                    response += "\n"
            response += "\n"

        await update.message.reply_text(
            response,
            reply_markup=self.get_main_keyboard(user_id)
        )
        return MENU_STATE

    async def handle_add_client_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления клиента - ФИО"""
        context.user_data['client_name'] = update.message.text
        await update.message.reply_text("Введите телефон (или пропустите):")
        return ADD_CLIENT_PHONE

    async def handle_add_client_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления клиента - телефон"""
        if update.message.text.lower() != 'пропустить':
            context.user_data['client_phone'] = update.message.text
        else:
            context.user_data['client_phone'] = ""

        await update.message.reply_text("Введите email (или пропустите):")
        return ADD_CLIENT_EMAIL

    async def handle_add_client_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления клиента - email"""
        if update.message.text.lower() != 'пропустить':
            context.user_data['client_email'] = update.message.text
        else:
            context.user_data['client_email'] = ""

        await update.message.reply_text("Введите адрес (или пропустите):")
        return ADD_CLIENT_ADDRESS

    async def handle_add_client_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления клиента - адрес"""
        user_id = update.effective_user.id

        if update.message.text.lower() != 'пропустить':
            context.user_data['client_address'] = update.message.text
        else:
            context.user_data['client_address'] = ""

        # Добавляем клиента в БД
        success = self.db.add_client(
            context.user_data['client_name'],
            context.user_data['client_phone'],
            context.user_data['client_email'],
            context.user_data['client_address']
        )

        if success:
            # Логируем действие
            self.db.log_action(
                user_id, "add", "clients",
                context.user_data['client_name'],
                {"name": context.user_data['client_name']}
            )

            await update.message.reply_text(
                f"✅ Клиент '{context.user_data['client_name']}' успешно добавлен!",
                reply_markup=self.get_main_keyboard(user_id)
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при добавлении клиента",
                reply_markup=self.get_main_keyboard(user_id)
            )

        return MENU_STATE

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена операции"""
        user_id = update.effective_user.id
        await update.message.reply_text(
            "Операция отменена",
            reply_markup=self.get_main_keyboard(user_id)
        )
        return MENU_STATE

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Update {update} caused error {context.error}")

    def run(self):
        """Запуск бота"""
        logger.info("Запуск бота...")

        # Подключаемся к 1С
        if not self.one_c.connect():
            logger.warning("Не удалось подключиться к 1С, бот будет работать только с локальной БД")

        # Создаем приложение
        app = Application.builder().token(self.token).build()

        # Добавляем обработчики
        app.add_handler(CommandHandler("start", self.start))

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", self.start),
                MessageHandler(filters.TEXT, self.handle_menu)
            ],
            states={
                MENU_STATE: [MessageHandler(filters.TEXT, self.handle_menu)],
                SEARCH_CLIENT: [MessageHandler(filters.TEXT, self.handle_search_client)],
                ADD_CLIENT_NAME: [MessageHandler(filters.TEXT, self.handle_add_client_name)],
                ADD_CLIENT_PHONE: [MessageHandler(filters.TEXT, self.handle_add_client_phone)],
                ADD_CLIENT_EMAIL: [MessageHandler(filters.TEXT, self.handle_add_client_email)],
                ADD_CLIENT_ADDRESS: [MessageHandler(filters.TEXT, self.handle_add_client_address)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )

        app.add_handler(conv_handler)
        app.add_error_handler(self.error_handler)

        # Запускаем бота
        logger.info("Бот запущен и ожидает сообщений...")
        app.run_polling()


if __name__ == '__main__':
    bot = AutoServiceBot()
    bot.run()
