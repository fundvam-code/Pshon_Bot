"""Telegram-бот для 1С:Альфа авто: клиенты, машины и заказ-наряды.

Интерфейс — inline-кнопки прямо под сообщениями. Навигация по меню правит одно и то же
сообщение; данные (ФИО, телефон, марка, госномер, VIN, год, название работы, нормочасы)
вводятся обычным текстом. Все данные читаются и пишутся напрямую в 1С (см. one_c.py).

Меню:
    Главное → Справочники → Клиенты и машины → (Найти / Добавить клиента / Добавить машину)
            → Заказ-наряды → (Сколько в работе ЗН / Редактирование ЗН → ЗН → Инфа / Список работ /
              Добавить работу)

Состояния диалога (ConversationHandler) — это только шаги, где бот ждёт ТЕКСТ или выбор из списка:
    MENU (навигация), SEARCH, CLIENT_NAME, CLIENT_PHONE, CAR_SEARCH, CAR_PICK, CAR_BRAND, CAR_MODEL,
    CAR_GOS, CAR_VIN, CAR_YEAR, ZN_WORK_NAME, ZN_WORK_PICK, ZN_WORK_HOURS.
Кнопки обрабатываются в любом состоянии (on_callback → route), поэтому «Отмена» и переход в другое
меню работают всегда.

Формат callback_data у кнопок:
    m:<меню>      перейти в меню (main, refs, clients, orders), сбрасывает незавершённый сценарий
    a:<действие>  запуск сценария (find, addclient, addcar, open, edit)
    zn:<...>      выбор ЗН по номеру в списке или действие над выбранным ЗН (menu, info, works, add)
    wk:<n|new>    выбор похожей работы из списка или «добавить новую»
    cl:<n>        выбор клиента из нескольких найденных (при добавлении машины)
    x:cancel / x:skip   «Отмена» (возврат на экран из user_data['back']) и «Пропустить»

Данные сценария лежат в context.user_data (client_name, client, brand, model, gos, vin, order, …);
по завершении сценария они очищаются, выбранный ЗН ('order') сохраняется внутри раздела ЗН.

Права: пункты меню «Справочники/клиенты/машины» зависят от прав в config/users.json,
раздел «Заказ-наряды» — от права view_work. Незарегистрированным пользователям бот отвечает
их Telegram ID (для передачи администратору).
"""
import datetime
import logging
import os
import re

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
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

(MENU, SEARCH, CLIENT_NAME, CLIENT_PHONE, CAR_SEARCH, CAR_PICK, CAR_BRAND, CAR_MODEL,
 CAR_GOS, CAR_VIN, CAR_YEAR, ZN_WORK_NAME, ZN_WORK_PICK, ZN_WORK_HOURS) = range(14)

VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')
CANCEL = ("✖️ Отмена", "x:cancel")
SKIP = ("⏭ Пропустить", "x:skip")
FLOW_KEYS = ('client_name', 'client', 'candidates', 'brand', 'model', 'gos', 'vin',
             'work_id', 'work_name', 'new_work', 'new_work_candidate', 'work_options')


def kb(*rows) -> InlineKeyboardMarkup:
    """Собирает inline-клавиатуру из рядов пар (текст кнопки, callback_data)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


class AutoServiceBot:
    """Бот: связывает Telegram (python-telegram-bot), права пользователей и базу 1С.

    Создаёт OneC (подключение к 1С) и AccessControl (пользователи из config/users.json).
    Всё поведение описано через методы-обработчики; запуск — метод run.
    """
    def __init__(self):
        """Читает настройки из .env и создаёт клиентов 1С и прав доступа.

        Переменные окружения: TELEGRAM_TOKEN (обязательно), ONE_C_DB_PATH, ONE_C_USER, ONE_C_PASSWORD,
        USERS_CONFIG (по умолчанию config/users.json). Подключение к 1С выполняется позже, в run().
        Исключение: ValueError, если не задан TELEGRAM_TOKEN.
        """
        self.token = os.getenv('TELEGRAM_TOKEN')
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN не найден в .env файле")
        self.access = AccessControl(os.path.join(BASE_DIR, os.getenv('USERS_CONFIG', 'config/users.json')))
        self.one_c = OneC(
            os.getenv('ONE_C_DB_PATH', r'C:\1C\Pshon'),
            os.getenv('ONE_C_USER', ''),
            os.getenv('ONE_C_PASSWORD', ''),
        )

    # ---------- вывод ----------

    @staticmethod
    def num(value: float) -> str:
        """Форматирует число для вывода: разделитель тысяч — пробел, без лишних нулей (1250.5 → «1 250.5», 110.0 → «110»)."""
        return f"{value:,.2f}".replace(",", " ").rstrip("0").rstrip(".")

    async def show(self, update: Update, text: str, markup=None):
        """Показывает экран: правит текущее сообщение при нажатии кнопки, иначе отправляет новое.

        Параметры: update; text — текст; markup — inline-клавиатура или None.
        Возвращает объект сообщения (чтобы позже убрать у него кнопки, см. drop_prompt).
        Если Telegram не даёт отредактировать сообщение (например, оно устарело), отправляет новое.
        """
        q = update.callback_query
        if q:
            try:
                res = await q.edit_message_text(text, reply_markup=markup)
                return res if not isinstance(res, bool) else q.message
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return q.message
                logger.warning("Не удалось отредактировать сообщение: %s", e)
        return await update.effective_message.reply_text(text, reply_markup=markup)

    async def show_long(self, update: Update, text: str, markup=None):
        """То же, что show, но делит текст длиннее ~3800 символов на несколько сообщений
        (лимит Telegram — 4096); кнопки прикрепляются к последнему.
        """
        chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]
        for part in chunks[:-1]:
            await update.effective_message.reply_text(part)
        await self.show(update, chunks[-1], markup)

    async def ask(self, update, context, text, markup, state, back):
        """Задаёт вопрос и переводит диалог в состояние ожидания ответа.

        Параметры:
            text, markup: вопрос и кнопки (обычно «Отмена», иногда «Пропустить»).
            state: состояние ConversationHandler, которое нужно вернуть.
            back: callback_data экрана, куда вернёт кнопка «Отмена» (например, "m:clients" или "zn:menu").
        Запоминает сообщение-вопрос и состояние в user_data, возвращает state.
        """
        context.user_data['prompt'] = await self.show(update, text, markup)
        context.user_data['state'] = state
        context.user_data['back'] = back
        return state

    @staticmethod
    async def drop_prompt(context):
        """Убирает кнопки у последнего вопроса бота, чтобы после ответа в чате не оставались «мёртвые» кнопки."""
        msg = context.user_data.pop('prompt', None)
        if msg:
            try:
                await msg.edit_reply_markup(None)
            except Exception:
                pass

    def clear_flow(self, context):
        """Забывает данные незавершённого сценария (поля клиента, машины, работы, вопрос и состояние),
        но сохраняет выбранный ЗН и список ЗН.
        """
        for key in FLOW_KEYS + ('prompt', 'state', 'back'):
            context.user_data.pop(key, None)

    # ---------- меню ----------

    def can_use_refs(self, uid: int) -> bool:
        """True, если у пользователя есть хотя бы одно право на раздел «Справочники»
        (просмотр клиентов, добавление клиента или машины).
        """
        return any(self.access.has_permission(uid, p) for p in (P.VIEW_CLIENTS, P.ADD_CLIENT, P.ADD_CAR))

    def menu_view(self, uid: int, name: str):
        """Возвращает (заголовок, клавиатура) нужного меню с учётом прав пользователя.

        Параметры: uid — Telegram ID; name — 'main', 'refs', 'clients' или 'orders'.
        Кнопки, на которые у пользователя нет прав, не показываются.
        """
        has = self.access.has_permission
        if name == 'refs':
            return "📚 Справочники", kb([("👥 Клиенты и машины", "m:clients")], [("⬅️ Назад", "m:main")])
        if name == 'clients':
            rows = []
            if has(uid, P.VIEW_CLIENTS):
                rows.append([("🔍 Найти клиента", "a:find")])
            if has(uid, P.ADD_CLIENT):
                rows.append([("➕ Добавить клиента", "a:addclient")])
            if has(uid, P.ADD_CAR):
                rows.append([("➕ Добавить машину", "a:addcar")])
            rows.append([("⬅️ Назад", "m:refs")])
            return "👥 Клиенты и машины", kb(*rows)
        if name == 'orders':
            return "📋 Заказ-наряды", kb(
                [("📊 Сколько в работе ЗН", "a:open")], [("✏️ Редактирование ЗН", "a:edit")],
                [("⬅️ Назад", "m:main")])
        rows = []
        if self.can_use_refs(uid):
            rows.append([("📚 Справочники", "m:refs")])
        if has(uid, P.VIEW_WORK):
            rows.append([("📋 Заказ-наряды", "m:orders")])
        return "Главное меню. Выберите действие:", kb(*rows) if rows else None

    async def go_menu(self, update, context, name: str, text: str = ""):
        """Показывает меню по имени (правкой текущего сообщения), при необходимости с пояснением сверху
        (например, текстом ошибки). Возвращает состояние MENU.
        """
        title, markup = self.menu_view(update.effective_user.id, name)
        await self.show(update, f"{text}\n\n{title}".strip() if text else title, markup)
        return MENU

    async def done(self, update, context, text: str, name: str = 'clients'):
        """Завершает сценарий: очищает данные, отправляет НОВЫМ сообщением результат и под ним снова меню
        раздела (по умолчанию «Клиенты и машины»). Возвращает MENU.
        """
        self.clear_flow(context)
        title, markup = self.menu_view(update.effective_user.id, name)
        await update.effective_message.reply_text(f"{text}\n\n{title}", reply_markup=markup)
        return MENU

    def zn_view(self, order):
        """Возвращает (заголовок, клавиатура) экрана выбранного заказ-наряда:
        «Инфа по ЗН», «Список работ», «Добавить работу», «К списку ЗН».
        """
        text = f"ЗН №{order['number']} · {order['customer']} — {order['car']}"
        return text, kb(
            [("📄 Инфа по ЗН", "zn:info"), ("📃 Список работ", "zn:works")],
            [("➕ Добавить работу", "zn:add")],
            [("⬅️ К списку ЗН", "a:edit")])

    async def done_zn(self, update, context, text: str):
        """Как done, но возвращает пользователя на экран выбранного ЗН (используется в сценарии добавления работы)."""
        self.clear_flow(context)
        head, markup = self.zn_view(context.user_data['order'])
        await update.effective_message.reply_text(f"{text}\n\n{head}", reply_markup=markup)
        return MENU

    # ---------- команды ----------

    async def myid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /myid: показывает Telegram ID пользователя (его нужно вписать в config/users.json).
        Доступна и незарегистрированным пользователям.
        """
        await update.message.reply_text(
            f"Ваш Telegram ID: <code>{update.effective_user.id}</code>\nДобавьте его в config/users.json.",
            parse_mode='HTML')

    async def _registered(self, update: Update) -> bool:
        """Проверяет, есть ли пользователь в config/users.json.

        Если нет — сообщает ему Telegram ID (для передачи администратору) и возвращает False.
        Для кнопок ответ показывается всплывающим окном, для текста — сообщением.
        """
        if self.access.is_user_registered(update.effective_user.id):
            return True
        text = (f"Вы не зарегистрированы. Ваш Telegram ID: {update.effective_user.id}. "
                f"Передайте его администратору.")
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        else:
            await update.effective_message.reply_text(text)
        return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start: приветствие и главное меню.

        Сбрасывает незавершённые сценарии и убирает у пользователя старую «нижнюю» клавиатуру
        (от прежней версии бота) — для этого отправляет и сразу удаляет служебное сообщение.
        """
        if not await self._registered(update):
            return ConversationHandler.END
        u = update.effective_user
        context.user_data.clear()
        old = await update.message.reply_text("⌨️", reply_markup=ReplyKeyboardRemove())
        try:
            await old.delete()
        except Exception:
            pass
        name = self.access.get_user(u.id).get('name', u.first_name)
        logger.info("Старт: %s (%s)", name, u.id)
        title, markup = self.menu_view(u.id, 'main')
        await update.message.reply_text(f"Здравствуйте, {name}!\n{title}", reply_markup=markup)
        return MENU

    async def idle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Любой текст вне сценария (и команда /cancel): показывает главное меню."""
        if not await self._registered(update):
            return ConversationHandler.END
        context.user_data.clear()
        title, markup = self.menu_view(update.effective_user.id, 'main')
        await update.message.reply_text(title, reply_markup=markup)
        return MENU

    async def pick_by_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ответ на текст в шагах, где нужно нажать кнопку (выбор клиента или похожей работы):
        напоминает выбрать кнопкой или нажать «Отмена»; состояние не меняется.
        """
        await update.message.reply_text("Выберите вариант кнопкой выше или нажмите «Отмена».")
        return context.user_data.get('state', MENU)

    # ---------- кнопки ----------

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Единая точка входа для всех нажатий inline-кнопок: подтверждает нажатие,
        проверяет регистрацию пользователя и передаёт callback_data в route.
        """
        q = update.callback_query
        await q.answer()
        if not await self._registered(update):
            return ConversationHandler.END
        return await self.route(update, context, q.data)

    async def route(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
        """Маршрутизатор кнопок: по callback_data выполняет переход или запускает сценарий.

        Параметры: update; context; data — строка callback_data (формат см. в описании модуля).
        Права проверяются здесь же (нельзя открыть раздел или сценарий без права, даже со старой кнопки).
        Неизвестные или устаревшие данные (например, после перезапуска бота) безопасно ведут в главное меню.
        Возвращает следующее состояние диалога.
        """
        uid = update.effective_user.id
        has = self.access.has_permission
        d = context.user_data

        if data.startswith("m:"):
            name = data[2:]
            if name in ('refs', 'clients') and not self.can_use_refs(uid):
                name = 'main'
            if name == 'orders' and not has(uid, P.VIEW_WORK):
                name = 'main'
            d.clear()
            return await self.go_menu(update, context, name if name in ('refs', 'clients', 'orders') else 'main')

        if data == "x:cancel":
            back = d.get('back', 'm:main')
            self.clear_flow(context)
            return await self.route(update, context, back)

        if data == "x:skip":
            step = {CLIENT_PHONE: self._client_phone, CAR_VIN: self._car_vin, CAR_YEAR: self._car_year}.get(d.get('state'))
            if step:
                return await step(update, context, "")
            return await self.route(update, context, "m:main")

        if data == "a:find" and has(uid, P.VIEW_CLIENTS):
            return await self.ask(update, context, "Введите ФИО или телефон клиента:", kb([CANCEL]), SEARCH, "m:clients")
        if data == "a:addclient" and has(uid, P.ADD_CLIENT):
            return await self.ask(update, context, "Введите ФИО клиента (Фамилия Имя Отчество):", kb([CANCEL]),
                                  CLIENT_NAME, "m:clients")
        if data == "a:addcar" and has(uid, P.ADD_CAR):
            return await self.ask(update, context, "Введите ФИО или телефон владельца машины:", kb([CANCEL]),
                                  CAR_SEARCH, "m:clients")

        if data == "a:open":
            return await self.open_orders(update, context)
        if data == "a:edit":
            return await self.orders_list(update, context)

        if data.startswith("cl:"):
            candidates = d.get('candidates') or []
            idx = data[3:]
            if idx.isdigit() and int(idx) < len(candidates):
                return await self.choose_client(update, context, candidates[int(idx)])
            return await self.route(update, context, "m:main")

        if data.startswith("zn:"):
            return await self.zn_action(update, context, data[3:])

        if data == "wk:new":
            d['new_work'] = d.pop('new_work_candidate', '')
            return await self.ask_hours(update, context,
                                        f"Работа «{d['new_work']}» будет добавлена в «Работы для разнесения».")
        if data.startswith("wk:"):
            options = d.get('work_options') or []
            idx = data[3:]
            if idx.isdigit() and int(idx) < len(options):
                d['work_id'], d['work_name'] = options[int(idx)]['id'], options[int(idx)]['name']
                return await self.ask_hours(update, context, f"Выбрана работа «{d['work_name']}».")
            return await self.route(update, context, "m:main")

        return await self.route(update, context, "m:main")

    # ---------- заказ-наряды ----------

    async def open_orders(self, update, context):
        """Кнопка «Сколько в работе ЗН»: общее число незакрытых ЗН и список (№, заказчик, авто,
        стоимость работ, состояние) — до 40 последних. Данные: OneC.count_open_work_orders.
        """
        try:
            res = self.one_c.count_open_work_orders()
        except OneCError as e:
            return await self.go_menu(update, context, 'orders', f"❌ {e}")
        lines = [f"📋 В работе (не закрыто) заказ-нарядов: {res['total']}"]
        for o in res['orders']:
            lines.append(f"\n№{o['number']} · {o['customer']}\n   🚗 {o['car']}\n"
                         f"   💰 {self.num(o['total'])} {o['currency']}".rstrip() + f" · {o['state']}")
        if res['total'] > len(res['orders']):
            lines.append(f"\n…показаны последние {len(res['orders'])} из {res['total']}")
        await self.show_long(update, "\n".join(lines), kb([("⬅️ Назад", "m:orders")]))
        return MENU

    async def orders_list(self, update, context):
        """Кнопка «Редактирование ЗН»: список ЗН в состоянии «В работе» кнопками
        («№5 · Заказчик — Авто»). Список запоминается в user_data['orders'].
        """
        try:
            orders = self.one_c.list_orders_in_progress()
        except OneCError as e:
            return await self.go_menu(update, context, 'orders', f"❌ {e}")
        if not orders:
            return await self.go_menu(update, context, 'orders', "ЗН в состоянии «В работе» нет.")
        context.user_data.clear()
        context.user_data['orders'] = orders
        rows = [[(f"№{o['number']} · {o['customer']} — {o['car']}", f"zn:{i}")] for i, o in enumerate(orders)]
        rows.append([("⬅️ Назад", "m:orders")])
        await self.show(update, "Выберите заказ-наряд (В работе):", kb(*rows))
        return MENU

    async def zn_action(self, update, context, action: str):
        """Действия над ЗН.

        Параметры: action — число (выбор ЗН из списка) или одно из: menu (экран ЗН), info (работы с нормочасами
        и общей стоимостью), works (только названия работ), add (запрос названия новой работы).
        Если выбранный ЗН потерян (перезапуск бота), возвращает в меню заказ-нарядов.
        """
        d = context.user_data
        if action.isdigit():
            orders = d.get('orders') or []
            if int(action) >= len(orders):
                return await self.route(update, context, "m:orders")
            d['order'] = orders[int(action)]
            action = 'menu'
        order = d.get('order')
        if not order:
            return await self.route(update, context, "m:orders")
        head, markup = self.zn_view(order)
        if action == 'menu':
            self.clear_flow(context)
            await self.show(update, head, markup)
            return MENU
        if action in ('info', 'works'):
            try:
                info = self.one_c.get_order_works(order['id'])
            except OneCError as e:
                await self.show(update, f"❌ {e}\n\n{head}", markup)
                return MENU
            if not info['works']:
                body = "Работ нет."
            elif action == 'works':
                body = "\n".join(f"{i}. {w['name']}" for i, w in enumerate(info['works'], 1))
            else:
                body = "\n".join(f"{i}. {w['name']} — {self.num(w['hours'])} н/ч"
                                 for i, w in enumerate(info['works'], 1))
                body += f"\n\nОбщая стоимость работ: {self.num(info['total'])} {info['currency']}".rstrip()
            await self.show(update, f"{head}\n\n{body}", markup)
            return MENU
        if action == 'add':
            return await self.ask(update, context, "Введите название работы:", kb([CANCEL]), ZN_WORK_NAME, "zn:menu")
        return MENU

    async def ask_hours(self, update, context, text: str):
        """Спрашивает количество нормочасов для добавляемой работы (шаг после выбора/создания работы)."""
        return await self.ask(update, context, text + "\nСколько нормочасов? (например, 1.5)", kb([CANCEL]),
                              ZN_WORK_HOURS, "zn:menu")

    async def handle_zn_work_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «название работы»: ищет работу в справочнике.

        Точное совпадение — берёт её; нет точного, но есть похожие — показывает их кнопками плюс
        «➕ Добавить новую»; совсем ничего нет — будет создана новая в группе «Работы для разнесения».
        Дальше спрашивает нормочасы.
        """
        await self.drop_prompt(context)
        name = " ".join(update.message.text.split())[:100]
        if len(name) < 2:
            return await self.ask(update, context, "Введите название работы (не короче 2 символов):",
                                  kb([CANCEL]), ZN_WORK_NAME, "zn:menu")
        try:
            found = self.one_c.find_works(name)
        except OneCError as e:
            return await self.done_zn(update, context, f"❌ {e}")
        d = context.user_data
        if found['exact']:
            d['work_id'], d['work_name'] = found['exact']['id'], found['exact']['name']
            return await self.ask_hours(update, context, f"Работа найдена в справочнике: «{d['work_name']}».")
        if not found['similar']:
            d['new_work'] = name
            return await self.ask_hours(
                update, context, f"Работы «{name}» нет в справочнике, будет добавлена в «Работы для разнесения».")
        d['work_options'] = found['similar']
        d['new_work_candidate'] = name
        rows = [[(w['name'], f"wk:{i}")] for i, w in enumerate(found['similar'])]
        rows.append([(f"➕ Добавить новую: {name}", "wk:new")])
        rows.append([CANCEL])
        return await self.ask(update, context, "Точной работы нет, но есть похожие. Выберите или добавьте новую:",
                              kb(*rows), ZN_WORK_PICK, "zn:menu")

    async def handle_zn_work_hours(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «нормочасы»: проверяет число (больше 0, не более 999; допускается запятая)
        и добавляет работу в ЗН (OneC.add_work_to_order).

        При создании новой работы пишет запись в logs/added_works.log (кто, какая работа, в какой ЗН).
        Показывает результат и общую стоимость работ; ошибки 1С (дубль работы, ЗН уже не «В работе») — текстом.
        """
        await self.drop_prompt(context)
        try:
            hours = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            hours = 0
        if not 0 < hours <= 999:
            return await self.ask(update, context, "Введите число нормочасов больше 0, например 1.5:",
                                  kb([CANCEL]), ZN_WORK_HOURS, "zn:menu")
        d = context.user_data
        order, user = d['order'], update.effective_user
        try:
            res = self.one_c.add_work_to_order(
                order['id'], hours, work_id=d.get('work_id', ''), new_work_name=d.get('new_work', ''))
        except OneCError as e:
            return await self.done_zn(update, context, f"❌ {e}")
        if res['created_work']:
            works_log.info("Пользователь %s (%s) добавил работу «%s» в справочник (группа «%s»), ЗН №%s",
                           self.access.get_user(user.id).get('name', ''), user.id, res['work'],
                           OneC.WORK_GROUP, order['number'])
        logger.info("%s добавил в ЗН №%s работу «%s» %s н/ч", user.id, order['number'], res['work'], hours)
        added = " (новая работа добавлена в справочник)" if res['created_work'] else ""
        return await self.done_zn(
            update, context,
            f"✅ В ЗН №{order['number']} добавлено: {res['work']} — {self.num(hours)} н/ч, "
            f"{self.num(res['line_sum'])}{added}\nОбщая стоимость работ: {self.num(res['total'])}")

    # ---------- поиск клиента ----------

    @staticmethod
    def format_client(client, cars) -> str:
        """Форматирует карточку клиента с его автомобилями (ФИО, телефон, машины с VIN и годом)
        для ответа на поиск.
        """
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
        """Шаг «найти клиента»: ищет по ФИО или телефону и выводит клиентов вместе с их машинами."""
        await self.drop_prompt(context)
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
            if not clients:
                return await self.done(update, context, f"Клиент «{query}» не найден.")
            blocks = [self.format_client(c, self.one_c.get_client_cars(c['id'])) for c in clients]
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        return await self.done(update, context, "\n\n".join(blocks))

    # ---------- добавление клиента ----------

    async def handle_client_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «ФИО клиента»: проверяет длину (2–100 символов) и спрашивает телефон."""
        await self.drop_prompt(context)
        name = " ".join(update.message.text.split())
        if len(name) < 2 or len(name) > 100:
            return await self.ask(update, context, "Введите ФИО (от 2 до 100 символов):",
                                  kb([CANCEL]), CLIENT_NAME, "m:clients")
        context.user_data['client_name'] = name
        return await self.ask(update, context, "Телефон клиента:", kb([SKIP, CANCEL]), CLIENT_PHONE, "m:clients")

    async def handle_client_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «телефон клиента» (текстом): передаёт значение в _client_phone."""
        return await self._client_phone(update, context, update.message.text.strip())

    async def _client_phone(self, update, context, phone: str):
        """Завершает добавление клиента: создаёт его в 1С (OneC.add_client).

        Параметры: phone — телефон или пустая строка (кнопка «Пропустить»).
        Если клиент с таким ФИО уже есть, сообщает об этом и ничего не создаёт.
        """
        await self.drop_prompt(context)
        try:
            res = self.one_c.add_client(context.user_data['client_name'], phone[:50])
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        if not res['created']:
            return await self.done(update, context, f"Клиент «{res['name']}» уже есть в 1С.")
        logger.info("%s добавил клиента %s", update.effective_user.id, res['name'])
        return await self.done(update, context, f"✅ Клиент «{res['name']}» добавлен в 1С.")

    # ---------- добавление машины ----------

    async def handle_car_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «владелец машины»: ищет клиента. Один найден — сразу идёт дальше; несколько — предлагает
        выбрать кнопкой; не найден — просит повторить или сначала добавить клиента.
        """
        await self.drop_prompt(context)
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        if not clients:
            return await self.ask(update, context,
                                  f"Клиент «{query}» не найден. Сначала добавьте клиента или повторите поиск:",
                                  kb([CANCEL]), CAR_SEARCH, "m:clients")
        if len(clients) == 1:
            return await self.choose_client(update, context, clients[0])
        context.user_data['candidates'] = clients
        rows = [[(f"{c['name']} {c['phone']}".strip(), f"cl:{i}")] for i, c in enumerate(clients)]
        rows.append([CANCEL])
        return await self.ask(update, context, "Найдено несколько клиентов, выберите:", kb(*rows), CAR_PICK, "m:clients")

    async def choose_client(self, update, context, client):
        """Запоминает выбранного владельца и спрашивает марку автомобиля."""
        context.user_data['client'] = client
        return await self.ask(update, context,
                              f"Клиент: {client['name']}\nВведите марку автомобиля (например, Toyota):",
                              kb([CANCEL]), CAR_BRAND, "m:clients")

    async def handle_car_brand(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «марка автомобиля» (например, Toyota)."""
        await self.drop_prompt(context)
        context.user_data['brand'] = update.message.text.strip()[:40]
        return await self.ask(update, context, "Модель (например, Camry):", kb([CANCEL]), CAR_MODEL, "m:clients")

    async def handle_car_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «модель автомобиля» (например, Camry)."""
        await self.drop_prompt(context)
        context.user_data['model'] = update.message.text.strip()[:40]
        return await self.ask(update, context, "Госномер (например, А123БВ77):", kb([CANCEL]), CAR_GOS, "m:clients")

    async def handle_car_gos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «госномер»: убирает пробелы, приводит к верхнему регистру, проверяет длину (4–12 символов)."""
        await self.drop_prompt(context)
        gos = re.sub(r'\s+', '', update.message.text).upper()
        if len(gos) < 4 or len(gos) > 12:
            return await self.ask(update, context, "Госномер выглядит неверно, введите ещё раз:",
                                  kb([CANCEL]), CAR_GOS, "m:clients")
        context.user_data['gos'] = gos
        return await self.ask(update, context, "VIN (17 символов):", kb([SKIP, CANCEL]), CAR_VIN, "m:clients")

    async def handle_car_vin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «VIN» (текстом): передаёт значение в _car_vin."""
        return await self._car_vin(update, context, update.message.text.strip().upper())

    async def _car_vin(self, update, context, vin: str):
        """Проверяет VIN (17 символов, латиница и цифры без I, O, Q) или принимает пустой (кнопка «Пропустить»)
        и спрашивает год выпуска.
        """
        await self.drop_prompt(context)
        if vin and not VIN_RE.match(vin):
            return await self.ask(update, context,
                                  "VIN — 17 символов, латиница и цифры (без I, O, Q). Повторите или пропустите:",
                                  kb([SKIP, CANCEL]), CAR_VIN, "m:clients")
        context.user_data['vin'] = vin
        return await self.ask(update, context, "Год выпуска (например, 2020):", kb([SKIP, CANCEL]), CAR_YEAR, "m:clients")

    async def handle_car_year(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «год выпуска» (текстом): передаёт значение в _car_year."""
        return await self._car_year(update, context, update.message.text.strip())

    async def _car_year(self, update, context, text: str):
        """Проверяет год (1950 … следующий год) или принимает пустой и создаёт машину в 1С (OneC.add_car).

        Если машина с таким госномером уже есть, сообщает название и владельца и ничего не создаёт.
        """
        await self.drop_prompt(context)
        year = 0
        if text:
            max_year = datetime.date.today().year + 1
            if not text.isdigit() or not 1950 <= int(text) <= max_year:
                return await self.ask(update, context, f"Введите год от 1950 до {max_year} или пропустите:",
                                      kb([SKIP, CANCEL]), CAR_YEAR, "m:clients")
            year = int(text)
        d = context.user_data
        try:
            res = self.one_c.add_car(d['client']['id'], d['brand'], d['model'], d['gos'], d['vin'], year)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        if not res['created']:
            owner = f", владелец: {res['owner']}" if res.get('owner') else ""
            return await self.done(update, context, f"Машина с номером {d['gos']} уже есть в 1С ({res['name']}{owner}).")
        logger.info("%s добавил авто %s клиенту %s", update.effective_user.id, d['gos'], d['client']['name'])
        return await self.done(update, context, f"✅ Машина «{res['name']}» добавлена клиенту {d['client']['name']}.")

    # ---------- запуск ----------

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /cancel: прерывает любой сценарий и показывает главное меню."""
        return await self.idle_text(update, context)

    async def on_error(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Записывает необработанные исключения обработчиков в лог (logs/bot.log)."""
        logger.error("Ошибка: %s", context.error, exc_info=context.error)

    def run(self):
        """Запускает бота.

        Подключается к 1С (около 30 секунд при первом входе; если не удалось — бот всё равно стартует и
        будет переподключаться при запросах), собирает ConversationHandler с состояниями диалога,
        регистрирует /myid и обработчик ошибок и запускает long polling Telegram (блокирующий вызов).
        Одновременно с одним токеном может работать только один экземпляр бота.
        """
        if not self.one_c.connect():
            logger.warning("1С недоступна при старте, подключение будет повторяться при запросах")

        text = filters.TEXT & ~filters.COMMAND
        cb = CallbackQueryHandler(self.on_callback)

        def st(handler):
            return [cb, MessageHandler(text, handler)]

        conv = ConversationHandler(
            entry_points=[CommandHandler('start', self.start), cb, MessageHandler(text, self.idle_text)],
            states={
                MENU: st(self.idle_text),
                SEARCH: st(self.handle_search),
                CLIENT_NAME: st(self.handle_client_name),
                CLIENT_PHONE: st(self.handle_client_phone),
                CAR_SEARCH: st(self.handle_car_search),
                CAR_PICK: st(self.pick_by_button),
                CAR_BRAND: st(self.handle_car_brand),
                CAR_MODEL: st(self.handle_car_model),
                CAR_GOS: st(self.handle_car_gos),
                CAR_VIN: st(self.handle_car_vin),
                CAR_YEAR: st(self.handle_car_year),
                ZN_WORK_NAME: st(self.handle_zn_work_name),
                ZN_WORK_PICK: st(self.pick_by_button),
                ZN_WORK_HOURS: st(self.handle_zn_work_hours),
            },
            fallbacks=[CommandHandler('start', self.start), CommandHandler('cancel', self.cmd_cancel)],
        )

        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler('myid', self.myid))
        app.add_handler(conv)
        app.add_error_handler(self.on_error)
        logger.info("Бот запущен")
        app.run_polling()


if __name__ == '__main__':
    AutoServiceBot().run()
