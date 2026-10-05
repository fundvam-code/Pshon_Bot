"""Telegram-бот для работы с клиентами и автомобилями в 1С:Альфа авто (запись напрямую в 1С)."""
import datetime
import logging
import os
import re

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
)

from access import AccessControl, PermissionChecker as P
from one_c import OneC, OneCError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'logs', 'bot.log'), encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
works_log = logging.getLogger('works')
_wh = logging.FileHandler(os.path.join(BASE_DIR, 'logs', 'added_works.log'), encoding='utf-8')
_wh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
works_log.addHandler(_wh)

(MENU, REF_MENU, CLIENTS_MENU, ORDERS_MENU, SEARCH, CLIENT_NAME, CLIENT_PHONE,
 CAR_SEARCH, CAR_PICK, CAR_BRAND, CAR_MODEL, CAR_GOS, CAR_VIN, CAR_YEAR,
 ZN_PICK, ZN_MENU, ZN_WORK_NAME, ZN_WORK_PICK, ZN_WORK_HOURS) = range(19)

BTN_REFS = '📚 Справочники'
BTN_ORDERS = '📋 Заказ-наряды'
BTN_CLIENTS = '👥 Клиенты и машины'
BTN_OPEN_ORDERS = '📊 Сколько в работе ЗН'
BTN_BACK = '⬅️ Назад'
BTN_EDIT_ORDERS = '✏️ Редактирование ЗН'
BTN_ZN_INFO = '📄 Инфа по ЗН'
BTN_ZN_WORKS = '📃 Список работ'
BTN_ZN_ADD = '➕ Добавить работу'
BTN_ZN_LIST = '⬅️ К списку ЗН'
BTN_FIND = '🔍 Найти клиента'
BTN_ADD_CLIENT = '➕ Добавить клиента'
BTN_ADD_CAR = '➕ Добавить машину'
BTN_SKIP = 'Пропустить'
BTN_CANCEL = 'Отмена'

CANCEL_KB = ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)
SKIP_KB = ReplyKeyboardMarkup([[BTN_SKIP], [BTN_CANCEL]], resize_keyboard=True)
VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')


class AutoServiceBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_TOKEN')
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN не найден в .env файле")
        self.access = AccessControl(os.path.join(BASE_DIR, os.getenv('USERS_CONFIG', 'config/users.json')))
        self.one_c = OneC(
            os.getenv('ONE_C_DB_PATH', r'C:\1C\Pshon'),
            os.getenv('ONE_C_USER', ''),
            os.getenv('ONE_C_PASSWORD', ''),
        )

    # ---------- клавиатуры и общие проверки ----------

    def can_use_refs(self, uid: int) -> bool:
        return any(self.access.has_permission(uid, p) for p in (P.VIEW_CLIENTS, P.ADD_CLIENT, P.ADD_CAR))

    def keyboard(self, uid: int, menu: int) -> ReplyKeyboardMarkup:
        has = self.access.has_permission
        if menu == REF_MENU:
            rows = [[BTN_CLIENTS], [BTN_BACK]]
        elif menu == CLIENTS_MENU:
            rows = []
            if has(uid, P.VIEW_CLIENTS):
                rows.append([BTN_FIND])
            if has(uid, P.ADD_CLIENT):
                rows.append([BTN_ADD_CLIENT])
            if has(uid, P.ADD_CAR):
                rows.append([BTN_ADD_CAR])
            rows.append([BTN_BACK])
        elif menu == ORDERS_MENU:
            rows = [[BTN_OPEN_ORDERS], [BTN_EDIT_ORDERS], [BTN_BACK]]
        else:
            rows = []
            if self.can_use_refs(uid):
                rows.append([BTN_REFS])
            if has(uid, P.VIEW_WORK):
                rows.append([BTN_ORDERS])
        return ReplyKeyboardMarkup(rows or [['/myid']], resize_keyboard=True)

    async def to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, menu: int = MENU):
        context.user_data.clear()
        await update.message.reply_text(text, reply_markup=self.keyboard(update.effective_user.id, menu))
        return menu

    # ---------- команды ----------

    async def myid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        await update.message.reply_text(
            f"Ваш Telegram ID: <code>{u.id}</code>\nДобавьте его в config/users.json.",
            parse_mode='HTML',
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if not self.access.is_user_registered(u.id):
            await update.message.reply_text(
                f"Здравствуйте, {u.first_name}! Вы не зарегистрированы.\n"
                f"Ваш Telegram ID: <code>{u.id}</code>. Передайте его администратору.",
                parse_mode='HTML',
            )
            return ConversationHandler.END
        name = self.access.get_user(u.id).get('name', u.first_name)
        logger.info("Старт: %s (%s)", name, u.id)
        return await self.to_menu(update, context, f"Здравствуйте, {name}! Выберите действие:")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self.to_menu(update, context, "Отменено.")

    # ---------- меню ----------

    async def _guard(self, update: Update) -> bool:
        if self.access.is_user_registered(update.effective_user.id):
            return True
        await update.message.reply_text("Вы не зарегистрированы. Команда /myid покажет ваш ID.")
        return False

    async def handle_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid, text = update.effective_user.id, update.message.text
        if not await self._guard(update):
            return ConversationHandler.END
        if text == BTN_REFS and self.can_use_refs(uid):
            return await self.to_menu(update, context, "Справочники:", REF_MENU)
        if text == BTN_ORDERS and self.access.has_permission(uid, P.VIEW_WORK):
            return await self.to_menu(update, context, "Заказ-наряды:", ORDERS_MENU)
        return await self.to_menu(update, context, "Выберите действие из меню.")

    async def handle_ref_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return ConversationHandler.END
        text = update.message.text
        if text == BTN_CLIENTS and self.can_use_refs(update.effective_user.id):
            return await self.to_menu(update, context, "Клиенты и машины:", CLIENTS_MENU)
        if text == BTN_BACK:
            return await self.to_menu(update, context, "Главное меню:")
        return await self.to_menu(update, context, "Справочники:", REF_MENU)

    async def handle_clients_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid, text = update.effective_user.id, update.message.text
        if not await self._guard(update):
            return ConversationHandler.END
        has = self.access.has_permission
        if text == BTN_BACK:
            return await self.to_menu(update, context, "Справочники:", REF_MENU)
        if text == BTN_FIND and has(uid, P.VIEW_CLIENTS):
            await update.message.reply_text("Введите ФИО или телефон клиента:", reply_markup=CANCEL_KB)
            return SEARCH
        if text == BTN_ADD_CLIENT and has(uid, P.ADD_CLIENT):
            await update.message.reply_text("Введите ФИО клиента (Фамилия Имя Отчество):", reply_markup=CANCEL_KB)
            return CLIENT_NAME
        if text == BTN_ADD_CAR and has(uid, P.ADD_CAR):
            await update.message.reply_text("Введите ФИО или телефон владельца машины:", reply_markup=CANCEL_KB)
            return CAR_SEARCH
        return await self.to_menu(update, context, "Клиенты и машины:", CLIENTS_MENU)

    async def handle_orders_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return ConversationHandler.END
        text = update.message.text
        if text == BTN_BACK:
            return await self.to_menu(update, context, "Главное меню:")
        if text == BTN_OPEN_ORDERS and self.access.has_permission(update.effective_user.id, P.VIEW_WORK):
            try:
                res = self.one_c.count_open_work_orders()
            except OneCError as e:
                return await self.to_menu(update, context, f"❌ {e}", ORDERS_MENU)
            lines = [f"📋 В работе (не закрыто) заказ-нарядов: {res['total']}"]
            for o in res['orders']:
                lines.append(f"\n№{o['number']} · {o['customer']}\n   🚗 {o['car']}\n"
                             f"   💰 {self.num(o['total'])} {o['currency']}".rstrip() + f" · {o['state']}")
            if res['total'] > len(res['orders']):
                lines.append(f"\n…показаны последние {len(res['orders'])} из {res['total']}")
            text = "\n".join(lines)
            chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]
            for part in chunks[:-1]:
                await update.message.reply_text(part)
            return await self.to_menu(update, context, chunks[-1], ORDERS_MENU)
        if text == BTN_EDIT_ORDERS:
            return await self.show_orders_list(update, context)
        return await self.to_menu(update, context, "Заказ-наряды:", ORDERS_MENU)

    # ---------- редактирование ЗН ----------

    @staticmethod
    def num(value: float) -> str:
        return f"{value:,.2f}".replace(",", " ").rstrip("0").rstrip(".")

    def zn_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [[BTN_ZN_INFO, BTN_ZN_WORKS], [BTN_ZN_ADD], [BTN_ZN_LIST]], resize_keyboard=True)

    async def show_orders_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            orders = self.one_c.list_orders_in_progress()
        except OneCError as e:
            return await self.to_menu(update, context, f"❌ {e}", ORDERS_MENU)
        if not orders:
            return await self.to_menu(update, context, "ЗН в состоянии «В работе» нет.", ORDERS_MENU)
        labels = {}
        for o in orders:
            labels[f"№{o['number']} · {o['customer']} — {o['car']}"[:60]] = o
        context.user_data.clear()
        context.user_data['orders'] = labels
        rows = [[label] for label in labels] + [[BTN_BACK]]
        await update.message.reply_text("Выберите заказ-наряд (В работе):",
                                        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
        return ZN_PICK

    async def handle_zn_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        if text == BTN_BACK:
            return await self.to_menu(update, context, "Заказ-наряды:", ORDERS_MENU)
        order = context.user_data.get('orders', {}).get(text)
        if not order:
            await update.message.reply_text("Выберите ЗН кнопкой из списка.")
            return ZN_PICK
        context.user_data['order'] = order
        await update.message.reply_text(
            f"ЗН №{order['number']} · {order['customer']} — {order['car']}", reply_markup=self.zn_keyboard())
        return ZN_MENU

    async def handle_zn_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        order = context.user_data.get('order')
        if text == BTN_ZN_LIST or not order:
            return await self.show_orders_list(update, context)
        if text in (BTN_ZN_INFO, BTN_ZN_WORKS):
            try:
                info = self.one_c.get_order_works(order['id'])
            except OneCError as e:
                await update.message.reply_text(f"❌ {e}", reply_markup=self.zn_keyboard())
                return ZN_MENU
            head = f"ЗН №{info['number']} · {info['customer']} — {info['car']}"
            if not info['works']:
                body = "Работ нет."
            elif text == BTN_ZN_WORKS:
                body = "\n".join(f"{i}. {w['name']}" for i, w in enumerate(info['works'], 1))
            else:
                body = "\n".join(f"{i}. {w['name']} — {self.num(w['hours'])} н/ч"
                                 for i, w in enumerate(info['works'], 1))
                body += f"\n\nОбщая стоимость работ: {self.num(info['total'])} {info['currency']}".rstrip()
            await update.message.reply_text(f"{head}\n\n{body}", reply_markup=self.zn_keyboard())
            return ZN_MENU
        if text == BTN_ZN_ADD:
            await update.message.reply_text("Введите название работы:", reply_markup=CANCEL_KB)
            return ZN_WORK_NAME
        await update.message.reply_text("Выберите действие кнопкой.", reply_markup=self.zn_keyboard())
        return ZN_MENU

    async def zn_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for key in ('work_id', 'work_name', 'new_work', 'work_options'):
            context.user_data.pop(key, None)
        await update.message.reply_text("Отменено.", reply_markup=self.zn_keyboard())
        return ZN_MENU

    async def ask_hours(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        await update.message.reply_text(text + "\nСколько нормочасов? (например, 1.5)", reply_markup=CANCEL_KB)
        return ZN_WORK_HOURS

    async def handle_zn_work_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = " ".join(update.message.text.split())[:100]
        if len(name) < 2:
            await update.message.reply_text("Введите название работы (не короче 2 символов):")
            return ZN_WORK_NAME
        try:
            found = self.one_c.find_works(name)
        except OneCError as e:
            await update.message.reply_text(f"❌ {e}", reply_markup=self.zn_keyboard())
            return ZN_MENU
        d = context.user_data
        if found['exact']:
            d['work_id'], d['work_name'] = found['exact']['id'], found['exact']['name']
            return await self.ask_hours(update, context, f"Работа найдена в справочнике: «{d['work_name']}».")
        if not found['similar']:
            d['new_work'] = name
            return await self.ask_hours(
                update, context, f"Работы «{name}» нет в справочнике, будет добавлена в «Работы для разнесения».")
        options = {w['name'][:60]: w for w in found['similar']}
        new_label = f"➕ Добавить новую: {name}"[:60]
        d['work_options'] = options
        d['new_work_label'] = new_label
        d['new_work_candidate'] = name
        rows = [[label] for label in options] + [[new_label], [BTN_CANCEL]]
        await update.message.reply_text("Точной работы нет, но есть похожие. Выберите или добавьте новую:",
                                        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
        return ZN_WORK_PICK

    async def handle_zn_work_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        d = context.user_data
        if text == d.get('new_work_label'):
            d['new_work'] = d['new_work_candidate']
            return await self.ask_hours(
                update, context, f"Работа «{d['new_work']}» будет добавлена в «Работы для разнесения».")
        work = d.get('work_options', {}).get(text)
        if not work:
            await update.message.reply_text("Выберите вариант кнопкой.")
            return ZN_WORK_PICK
        d['work_id'], d['work_name'] = work['id'], work['name']
        return await self.ask_hours(update, context, f"Выбрана работа «{work['name']}».")

    async def handle_zn_work_hours(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            hours = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            hours = 0
        if not 0 < hours <= 999:
            await update.message.reply_text("Введите число нормочасов больше 0, например 1.5:")
            return ZN_WORK_HOURS
        d = context.user_data
        order = d['order']
        user = update.effective_user
        try:
            res = self.one_c.add_work_to_order(
                order['id'], hours, work_id=d.get('work_id', ''), new_work_name=d.get('new_work', ''))
        except OneCError as e:
            for key in ('work_id', 'work_name', 'new_work', 'work_options'):
                d.pop(key, None)
            await update.message.reply_text(f"❌ {e}", reply_markup=self.zn_keyboard())
            return ZN_MENU
        if res['created_work']:
            works_log.info("Пользователь %s (%s) добавил работу «%s» в справочник (группа «%s»), ЗН №%s",
                           self.access.get_user(user.id).get('name', ''), user.id, res['work'],
                           OneC.WORK_GROUP, order['number'])
        logger.info("%s добавил в ЗН №%s работу «%s» %s н/ч", user.id, order['number'], res['work'], hours)
        for key in ('work_id', 'work_name', 'new_work', 'work_options'):
            d.pop(key, None)
        added = " (новая работа добавлена в справочник)" if res['created_work'] else ""
        await update.message.reply_text(
            f"✅ В ЗН №{order['number']} добавлено: {res['work']} — {self.num(hours)} н/ч, "
            f"{self.num(res['line_sum'])}{added}\nОбщая стоимость работ: {self.num(res['total'])}",
            reply_markup=self.zn_keyboard())
        return ZN_MENU

    # ---------- поиск клиента ----------

    @staticmethod
    def format_client(client, cars) -> str:
        lines = [f"👤 {client['name']}"]
        if client['phone']:
            lines.append(f"   ☎️ {client['phone']}")
        for car in cars:
            extra = ", ".join(x for x in (f"VIN {car['vin']}" if car['vin'] else "",
                                          f"{car['year']} г." if car['year'] else "") if x)
            lines.append(f"   🚗 {car['name']}" + (f" ({extra})" if extra else ""))
        if not cars:
            lines.append("   Автомобилей нет")
        return "\n".join(lines)

    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
            if not clients:
                return await self.to_menu(update, context, f"Клиент «{query}» не найден.", CLIENTS_MENU)
            blocks = [self.format_client(c, self.one_c.get_client_cars(c['id'])) for c in clients]
        except OneCError as e:
            return await self.to_menu(update, context, f"❌ {e}", CLIENTS_MENU)
        return await self.to_menu(update, context, "\n\n".join(blocks), CLIENTS_MENU)

    # ---------- добавление клиента ----------

    async def handle_client_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = " ".join(update.message.text.split())
        if len(name) < 2 or len(name) > 100:
            await update.message.reply_text("Введите ФИО (от 2 до 100 символов):")
            return CLIENT_NAME
        context.user_data['client_name'] = name
        await update.message.reply_text("Телефон клиента:", reply_markup=SKIP_KB)
        return CLIENT_PHONE

    async def handle_client_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        phone = '' if update.message.text == BTN_SKIP else update.message.text.strip()[:50]
        name = context.user_data['client_name']
        try:
            res = self.one_c.add_client(name, phone)
        except OneCError as e:
            return await self.to_menu(update, context, f"❌ {e}", CLIENTS_MENU)
        if not res['created']:
            return await self.to_menu(update, context, f"Клиент «{res['name']}» уже есть в 1С.", CLIENTS_MENU)
        logger.info("%s добавил клиента %s", update.effective_user.id, res['name'])
        return await self.to_menu(update, context, f"✅ Клиент «{res['name']}» добавлен в 1С.", CLIENTS_MENU)

    # ---------- добавление машины ----------

    async def handle_car_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
        except OneCError as e:
            return await self.to_menu(update, context, f"❌ {e}", CLIENTS_MENU)
        if not clients:
            await update.message.reply_text(f"Клиент «{query}» не найден. Сначала добавьте клиента или повторите поиск:")
            return CAR_SEARCH
        if len(clients) == 1:
            return await self.choose_client(update, context, clients[0])
        context.user_data['candidates'] = clients
        listing = "\n".join(f"{i}. {c['name']} {c['phone']}".rstrip() for i, c in enumerate(clients, 1))
        await update.message.reply_text(f"Найдено несколько клиентов, введите номер:\n{listing}")
        return CAR_PICK

    async def handle_car_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        candidates = context.user_data.get('candidates', [])
        text = update.message.text.strip()
        if not text.isdigit() or not 1 <= int(text) <= len(candidates):
            await update.message.reply_text(f"Введите номер от 1 до {len(candidates)}:")
            return CAR_PICK
        return await self.choose_client(update, context, candidates[int(text) - 1])

    async def choose_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE, client):
        context.user_data['client'] = client
        await update.message.reply_text(
            f"Клиент: {client['name']}\nВведите марку автомобиля (например, Toyota):", reply_markup=CANCEL_KB)
        return CAR_BRAND

    async def handle_car_brand(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['brand'] = update.message.text.strip()[:40]
        await update.message.reply_text("Модель (например, Camry):")
        return CAR_MODEL

    async def handle_car_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['model'] = update.message.text.strip()[:40]
        await update.message.reply_text("Госномер (например, А123БВ77):")
        return CAR_GOS

    async def handle_car_gos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        gos = re.sub(r'\s+', '', update.message.text).upper()
        if len(gos) < 4 or len(gos) > 12:
            await update.message.reply_text("Госномер выглядит неверно, введите ещё раз:")
            return CAR_GOS
        context.user_data['gos'] = gos
        await update.message.reply_text("VIN (17 символов):", reply_markup=SKIP_KB)
        return CAR_VIN

    async def handle_car_vin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip().upper()
        if text == BTN_SKIP.upper():
            text = ''
        elif not VIN_RE.match(text):
            await update.message.reply_text("VIN — 17 символов, латиница и цифры (без I, O, Q). Повторите или пропустите:")
            return CAR_VIN
        context.user_data['vin'] = text
        await update.message.reply_text("Год выпуска (например, 2020):", reply_markup=SKIP_KB)
        return CAR_YEAR

    async def handle_car_year(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        year = 0
        if text != BTN_SKIP:
            max_year = datetime.date.today().year + 1
            if not text.isdigit() or not 1950 <= int(text) <= max_year:
                await update.message.reply_text(f"Введите год от 1950 до {max_year} или пропустите:")
                return CAR_YEAR
            year = int(text)
        d = context.user_data
        try:
            res = self.one_c.add_car(d['client']['id'], d['brand'], d['model'], d['gos'], d['vin'], year)
        except OneCError as e:
            return await self.to_menu(update, context, f"❌ {e}", CLIENTS_MENU)
        if not res['created']:
            owner = f", владелец: {res['owner']}" if res.get('owner') else ""
            return await self.to_menu(update, context, f"Машина с номером {d['gos']} уже есть в 1С ({res['name']}{owner}).", CLIENTS_MENU)
        logger.info("%s добавил авто %s клиенту %s", update.effective_user.id, d['gos'], d['client']['name'])
        return await self.to_menu(update, context, f"✅ Машина «{res['name']}» добавлена клиенту {d['client']['name']}.", CLIENTS_MENU)

    # ---------- запуск ----------

    async def on_error(self, update, context: ContextTypes.DEFAULT_TYPE):
        logger.error("Ошибка: %s", context.error, exc_info=context.error)

    def run(self):
        if not self.one_c.connect():
            logger.warning("1С недоступна при старте, подключение будет повторяться при запросах")

        text = filters.TEXT & ~filters.COMMAND
        common = [CommandHandler('cancel', self.cancel),
                  MessageHandler(filters.Regex(f'^{BTN_CANCEL}$'), self.cancel)]

        def step(handler):
            return common + [MessageHandler(text, handler)]

        zn_common = [CommandHandler('cancel', self.zn_cancel),
                     MessageHandler(filters.Regex(f'^{BTN_CANCEL}$'), self.zn_cancel)]

        def zn_step(handler):
            return zn_common + [MessageHandler(text, handler)]

        conv = ConversationHandler(
            entry_points=[CommandHandler('start', self.start), MessageHandler(text, self.handle_menu)],
            states={
                MENU: [MessageHandler(text, self.handle_menu)],
                REF_MENU: [MessageHandler(text, self.handle_ref_menu)],
                CLIENTS_MENU: [MessageHandler(text, self.handle_clients_menu)],
                ORDERS_MENU: [MessageHandler(text, self.handle_orders_menu)],
                SEARCH: step(self.handle_search),
                CLIENT_NAME: step(self.handle_client_name),
                CLIENT_PHONE: step(self.handle_client_phone),
                CAR_SEARCH: step(self.handle_car_search),
                CAR_PICK: step(self.handle_car_pick),
                CAR_BRAND: step(self.handle_car_brand),
                CAR_MODEL: step(self.handle_car_model),
                CAR_GOS: step(self.handle_car_gos),
                CAR_VIN: step(self.handle_car_vin),
                CAR_YEAR: step(self.handle_car_year),
                ZN_PICK: [MessageHandler(text, self.handle_zn_pick)],
                ZN_MENU: [MessageHandler(text, self.handle_zn_menu)],
                ZN_WORK_NAME: zn_step(self.handle_zn_work_name),
                ZN_WORK_PICK: zn_step(self.handle_zn_work_pick),
                ZN_WORK_HOURS: zn_step(self.handle_zn_work_hours),
            },
            fallbacks=[CommandHandler('start', self.start), CommandHandler('cancel', self.cancel)],
        )

        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler('myid', self.myid))
        app.add_handler(conv)
        app.add_error_handler(self.on_error)
        logger.info("Бот запущен")
        app.run_polling()


if __name__ == '__main__':
    AutoServiceBot().run()
