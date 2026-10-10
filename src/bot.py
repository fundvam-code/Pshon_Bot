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
    CAR_GOS, CAR_VIN, CAR_YEAR, ZN_WORK_NAME, ZN_WORK_PICK, ZN_WORK_AMOUNT.
Кнопки обрабатываются в любом состоянии (on_callback → route), поэтому «Отмена» и переход в другое
меню работают всегда.

Формат callback_data у кнопок:
    m:<меню>      перейти в меню (main, refs, clients, orders), сбрасывает незавершённый сценарий
    a:<действие>  запуск сценария (find, addclient, addcar, open, edit)
    zn:<...>      выбор ЗН по номеру в списке или действие над выбранным ЗН (menu, info, works, add)
    wk:<n|new>    выбор похожей работы из списка или «добавить новую»
    cl:<n>        выбор клиента из нескольких найденных (при добавлении машины)
    pc:new|used   запчасть клиента в ЗН: новая или б/у (б/у — «запчасть б/у» в «Примечание печать»)
    pt:<n|new>    запчасть клиента: выбор найденной по артикулу запчасти или «добавить новую»
                  (порядок: новая/б/у → артикул → сверка по артикулу → наименование → количество)
    zc:<n|new>    при создании ЗН: выбор найденного клиента или «создать нового»
    zk:<n|new>    при создании ЗН: выбор машины клиента или «новая машина»

Сценарий «Создать заказ-наряд»: клиент (поиск; нет — создаётся новый) → машина (машины клиента кнопками или
новая: марка, модель, госномер, VIN, год) → итог → «Подтвердить»: в одной транзакции создаются новые клиент и
машина и сам ЗН в состоянии «В работе» → «Добавить работы?»; работы в таком ЗН получают исполнителя по
умолчанию (ONE_C_DEFAULT_EXECUTOR).
    cc:<n>        «Добавить машину» найденному клиенту (кнопка под результатом поиска)
    x:cancel / x:skip / x:ok   «Отмена» (возврат на экран из user_data['back']), «Пропустить» /
                  «Без машины», «Подтвердить» (запись в 1С)

Тип клиента выбирается кнопкой в меню «Клиенты и машины» («Клиент: физ. лицо» / «Клиент: юр. лицо»; callback
a:addclient:fl / a:addclient:ul) и в сценарии создания ЗН (zt:fl / zt:ul). Физлицо создаётся с формой
собственности «Частное лицо», юрлицо — «Юридическое лицо».

Сценарий «Добавить клиента»: ФИО (для юрлица — наименование организации) → телефон → экран с данными клиента → «Подтвердить» (клиент создаётся
в 1С) → сразу ввод машины (марка, модель, госномер, VIN, год; на первом шаге есть «Без машины») →
экран «Кому и какая машина» → «Подтвердить» (машина создаётся в 1С). До подтверждений в 1С ничего не пишется.
То же добавление машины запускается из «Добавить машину» и из результата поиска клиента.

Справочники → «Добавить з/ч» и «Добавить название работы»: название → сверка с 1С → (похожие: список и
«Добавить»/«Отмена») → запись в папку «Запчасти для разнесения» / «Работы для разнесения».

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
from config import load_settings
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
 CAR_GOS, CAR_VIN, CAR_YEAR, ZN_WORK_NAME, ZN_WORK_PICK, ZN_WORK_AMOUNT,
 CLIENT_CONFIRM, CAR_CONFIRM, CAT_NAME, CAT_CONFIRM, ZN_PART_NAME, ZN_PART_PICK, ZN_PART_QTY,
 ZN_NEW_CLIENT, ZN_NEW_PICK, ZN_NEW_NAME, ZN_NEW_PHONE, ZN_CAR_PICK, ZN_NEW_CONFIRM,
 ZN_PART_COND, ZN_PART_ARTICLE, ZN_NEW_TYPE) = range(30)

VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')
CANCEL = ("✖️ Отмена", "x:cancel")
SKIP = ("⏭ Пропустить", "x:skip")
SKIP_CAR = ("⏭ Без машины", "x:skip")
OK = ("✅ Подтвердить", "x:ok")
FLOW_KEYS = ('client_name', 'phone', 'client', 'candidates', 'brand', 'model', 'gos', 'vin', 'year',
             'after_client', 'cat', 'cat_name', 'work_id', 'work_name', 'new_work', 'new_work_candidate', 'work_options',
             'part_id', 'part_name', 'new_part', 'new_part_candidate', 'part_options',
             'part_used', 'part_article', 'part_name_typed', 'new_part_article',
             'zn_flow', 'zn_query', 'zn_new_client', 'zn_car', 'client_cars', 'new_car',
             'client_legal', 'zn_legal')


def kb(*rows) -> InlineKeyboardMarkup:
    """Собирает inline-клавиатуру из рядов пар (текст кнопки, callback_data)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


class AutoServiceBot:
    """Бот: связывает Telegram (python-telegram-bot), права пользователей и базу 1С.

    Создаёт OneC (подключение к 1С) и AccessControl (пользователи из config/users.json).
    Всё поведение описано через методы-обработчики; запуск — метод run.
    """
    def __init__(self):
        """Читает и проверяет настройки из .env (config.load_settings), создаёт клиентов 1С и прав доступа.

        Подключение к 1С выполняется позже, в run().
        Исключение: ConfigError, если в .env чего-то не хватает или путь к базе неверный.
        """
        settings = load_settings()
        self.token = settings.token
        self.access = AccessControl(settings.users_config)
        self.one_c = OneC(
            settings.one_c_connection, settings.one_c_user, settings.one_c_password,
            progid=settings.one_c_progid, description=settings.one_c_description,
            currency=settings.one_c_currency, default_executor=settings.one_c_default_executor,
        )

    # ---------- вывод ----------

    @staticmethod
    def num(value: float, digits: int = 2) -> str:
        """Форматирует число для вывода: разделитель тысяч — пробел, без лишних нулей (1250.5 → «1 250.5», 110.0 → «110»).

        digits — максимум знаков после запятой (для нормочасов 1С хранит 3)."""
        return f"{value:,.{digits}f}".replace(",", " ").rstrip("0").rstrip(".")

    async def show(self, update: Update, text: str, markup=None, new: bool = False):
        """Показывает экран: правит текущее сообщение при нажатии кнопки, иначе отправляет новое.

        Параметры: update; text — текст; markup — inline-клавиатура или None;
        new — всегда отправлять новое сообщение (чтобы не затирать, например, результат поиска).
        Возвращает объект сообщения (чтобы позже убрать у него кнопки, см. drop_prompt).
        Если Telegram не даёт отредактировать сообщение (например, оно устарело), отправляет новое.
        """
        q = update.callback_query
        if q and not new:
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

    async def ask(self, update, context, text, markup, state, back, new: bool = False):
        """Задаёт вопрос и переводит диалог в состояние ожидания ответа.

        Параметры:
            text, markup: вопрос и кнопки (обычно «Отмена», иногда «Пропустить» или «Подтвердить»).
            state: состояние ConversationHandler, которое нужно вернуть.
            back: callback_data экрана, куда вернёт кнопка «Отмена» (например, "m:clients" или "zn:menu").
            new: отправить вопрос новым сообщением, а не править текущее.
        Запоминает сообщение-вопрос и состояние в user_data, возвращает state.
        """
        context.user_data['prompt'] = await self.show(update, text, markup, new=new)
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
            return "📚 Справочники", kb(
                [("👥 Клиенты и машины", "m:clients")],
                [("🔩 Добавить з/ч", "a:addpart")],
                [("🛠 Добавить название работы", "a:addwork")],
                [("⬅️ Назад", "m:main")])
        if name == 'clients':
            rows = []
            if has(uid, P.VIEW_CLIENTS):
                rows.append([("🔍 Найти клиента", "a:find")])
            if has(uid, P.ADD_CLIENT):
                rows.append([("➕ Клиент: физ. лицо", "a:addclient:fl"), ("➕ Клиент: юр. лицо", "a:addclient:ul")])
            if has(uid, P.ADD_CAR):
                rows.append([("➕ Добавить машину", "a:addcar")])
            rows.append([("⬅️ Назад", "m:refs")])
            return "👥 Клиенты и машины", kb(*rows)
        if name == 'orders':
            return "📋 Заказ-наряды", kb(
                [("🆕 Создать заказ-наряд", "a:newzn")],
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
        «Инфа по ЗН», «Список работ», «Добавить работу», «Добавить з/ч клиента», «К списку ЗН».
        """
        text = f"ЗН №{order['number']} · {order['customer']} — {order['car']}"
        return text, kb(
            [("📄 Инфа по ЗН", "zn:info"), ("📃 Список работ", "zn:works")],
            [("➕ Добавить работу", "zn:add")],
            [("🧰 Добавить з/ч клиента", "zn:addpart")],
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
            step = {CLIENT_PHONE: self._client_phone, CAR_VIN: self._car_vin, CAR_YEAR: self._car_year,
                    CAR_BRAND: self._skip_car, ZN_NEW_PHONE: self._zn_new_phone,
                    ZN_PART_ARTICLE: self._zn_part_article}.get(d.get('state'))
            if step:
                return await step(update, context, "")
            return await self.route(update, context, "m:main")

        if data == "x:ok":
            step = {CLIENT_CONFIRM: self._client_commit, CAR_CONFIRM: self._car_commit,
                    CAT_CONFIRM: self._cat_commit, ZN_NEW_CONFIRM: self._zn_create_commit}.get(d.get('state'))
            if step:
                return await step(update, context)
            return await self.route(update, context, "m:main")

        if data.startswith("cc:") and has(uid, P.ADD_CAR):
            found = d.get('found') or []
            idx = data[3:]
            if idx.isdigit() and int(idx) < len(found):
                return await self.choose_client(update, context, found[int(idx)], new=True)
            return await self.route(update, context, "m:clients")

        if data == "a:find" and has(uid, P.VIEW_CLIENTS):
            return await self.ask(update, context, "Введите ФИО или телефон клиента:", kb([CANCEL]), SEARCH, "m:clients")
        if data in ("a:addclient", "a:addclient:fl", "a:addclient:ul") and has(uid, P.ADD_CLIENT):
            d['client_legal'] = data.endswith(":ul")
            prompt = ("Введите наименование организации (например, ООО «Ромашка»):" if d['client_legal']
                      else "Введите ФИО клиента (Фамилия Имя Отчество):")
            return await self.ask(update, context, prompt, kb([CANCEL]), CLIENT_NAME, "m:clients")
        if data in ("zt:fl", "zt:ul") and d.get('state') == ZN_NEW_TYPE:
            return await self.zn_client_type(update, context, data == "zt:ul")
        if data == "a:addcar" and has(uid, P.ADD_CAR):
            return await self.ask(update, context, "Введите ФИО или телефон владельца машины:", kb([CANCEL]),
                                  CAR_SEARCH, "m:clients")

        if data in ("a:addpart", "a:addwork") and self.can_use_refs(uid):
            kind = "part" if data == "a:addpart" else "work"
            d['cat'] = kind
            what = "запчасти" if kind == "part" else "работы"
            return await self.ask(update, context, f"Введите название {what}:", kb([CANCEL]), CAT_NAME, "m:refs")

        if data == "a:newzn" and has(uid, P.VIEW_WORK):
            d['zn_flow'] = True
            return await self.ask(update, context, "Введите ФИО или телефон клиента для нового заказ-наряда:",
                                  kb([CANCEL]), ZN_NEW_CLIENT, "m:orders")

        if data.startswith("zc:") and d.get('zn_flow'):
            return await self.zn_client_button(update, context, data[3:])
        if data.startswith("zk:") and d.get('zn_flow'):
            return await self.zn_car_button(update, context, data[3:])

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
            return await self.ask_amount(update, context,
                                        f"Работа «{d['new_work']}» будет добавлена в «Работы для разнесения».")
        if data.startswith("wk:"):
            options = d.get('work_options') or []
            idx = data[3:]
            if idx.isdigit() and int(idx) < len(options):
                d['work_id'], d['work_name'] = options[int(idx)]['id'], options[int(idx)]['name']
                return await self.ask_amount(update, context, f"Выбрана работа «{d['work_name']}».")
            return await self.route(update, context, "m:main")

        if data in ("pc:new", "pc:used") and d.get('state') == ZN_PART_COND:
            d['part_used'] = data == "pc:used"
            return await self.ask_part_article(update, context)
        if data == "pt:new":
            return await self.ask_part_article(
                update, context, "Артикул должен быть уникальным: 1С не допускает два одинаковых артикула. ")
        if data.startswith("pt:"):
            options = d.get('part_options') or []
            idx = data[3:]
            if idx.isdigit() and int(idx) < len(options):
                d['part_id'], d['part_name'] = options[int(idx)]['id'], options[int(idx)]['name']
                return await self.ask_qty(update, context, f"Выбрана запчасть «{d['part_name']}».")
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
                body = "\n".join(f"{i}. {w['name']} — {self.num(w['hours'], 3)} н/ч"
                                 for i, w in enumerate(info['works'], 1))
                body += f"\n\nОбщая стоимость работ: {self.num(info['total'])} {info['currency']}".rstrip()
            if action == 'info':
                if info['materials']:
                    body += "\n\n🧰 Запчасти клиента (материалы заказчика):\n" + "\n".join(
                        (f"{i}. {m['name']} — {self.num(m['qty'])} {m['unit']}".rstrip()
                         + (f" ({m['note']})" if m.get('note') else ""))
                        for i, m in enumerate(info['materials'], 1))
                else:
                    body += "\n\n🧰 Запчастей клиента нет."
            await self.show(update, f"{head}\n\n{body}", markup)
            return MENU
        if action == 'add':
            return await self.ask(update, context, "Введите название работы:", kb([CANCEL]), ZN_WORK_NAME, "zn:menu")
        if action == 'addpart':
            return await self.ask(update, context, "Какая запчасть клиента?",
                                  kb([("🆕 Новая", "pc:new"), ("♻️ Б/у", "pc:used")], [CANCEL]),
                                  ZN_PART_COND, "zn:menu")
        return MENU

    async def ask_amount(self, update, context, text: str):
        """Спрашивает сумму за добавляемую работу (шаг после выбора/создания работы).

        В вопросе показывается действующий нормочас (цена из справочника «Нормочасы»): по нему бот переведёт
        сумму в часы."""
        try:
            norm = self.one_c.get_norm_hour()
            hint = f"\nДействующий нормочас: {self.num(norm['price'])} {norm['currency']}".rstrip()
        except OneCError:
            hint = ""
        return await self.ask(update, context, f"{text}\nВведите сумму за работу (например, 165):{hint}",
                              kb([CANCEL]), ZN_WORK_AMOUNT, "zn:menu")

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
            return await self.ask_amount(update, context, f"Работа найдена в справочнике: «{d['work_name']}».")
        if not found['similar']:
            d['new_work'] = name
            return await self.ask_amount(
                update, context, f"Работы «{name}» нет в справочнике, будет добавлена в «Работы для разнесения».")
        d['work_options'] = found['similar']
        d['new_work_candidate'] = name
        rows = [[(w['name'], f"wk:{i}")] for i, w in enumerate(found['similar'])]
        rows.append([(f"➕ Добавить новую: {name}", "wk:new")])
        rows.append([CANCEL])
        return await self.ask(update, context, "Точной работы нет, но есть похожие. Выберите или добавьте новую:",
                              kb(*rows), ZN_WORK_PICK, "zn:menu")

    async def handle_zn_work_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «сумма за работу»: проверяет число (больше 0; допускается запятая) и добавляет работу в ЗН
        (OneC.add_work_to_order): нормочасы вычисляются как сумма / цена действующего нормочаса, в таблицу
        работ ЗН записываются эти часы и введённая сумма.

        При создании новой работы пишет запись в logs/added_works.log (кто, какая работа, в какой ЗН).
        Показывает результат и общую стоимость работ; ошибки 1С (дубль работы, ЗН уже не «В работе») — текстом.
        """
        await self.drop_prompt(context)
        try:
            amount = round(float(update.message.text.strip().replace(" ", "").replace(",", ".")), 2)
        except ValueError:
            amount = 0
        if not 0 < amount <= 100000000:
            return await self.ask(update, context, "Введите сумму больше 0, например 165:",
                                  kb([CANCEL]), ZN_WORK_AMOUNT, "zn:menu")
        d = context.user_data
        order, user = d['order'], update.effective_user
        try:
            res = self.one_c.add_work_to_order(
                order['id'], 0, work_id=d.get('work_id', ''), new_work_name=d.get('new_work', ''),
                assign_executor=order.get('default_executor', False), amount=amount)
        except OneCError as e:
            return await self.done_zn(update, context, f"❌ {e}")
        if res['created_work']:
            works_log.info("Пользователь %s (%s) добавил работу «%s» в справочник (группа «%s»), ЗН №%s",
                           self.access.get_user(user.id).get('name', ''), user.id, res['work'],
                           OneC.WORK_GROUP, order['number'])
        logger.info("%s добавил в ЗН №%s работу «%s»: %s н/ч (сумма %s)", user.id, order['number'], res['work'],
                    res['hours'], amount)
        added = " (новая работа добавлена в справочник)" if res['created_work'] else ""
        executor = f"\nИсполнитель: {res['executor']}" if res.get('executor') else ""
        return await self.done_zn(
            update, context,
            f"✅ В ЗН №{order['number']} добавлено: {res['work']} — {self.num(res['line_sum'])}, "
            f"это {self.num(res['hours'], 3)} н/ч (нормочас {self.num(res['norm_price'])}){added}{executor}\n"
            f"Общая стоимость работ: {self.num(res['total'])}")

    async def ask_qty(self, update, context, text: str):
        """Спрашивает количество запчастей клиента (шаг после выбора/создания запчасти)."""
        return await self.ask(update, context, text + "\nСколько штук? (например, 2)", kb([CANCEL]),
                              ZN_PART_QTY, "zn:menu")

    async def ask_part_article(self, update, context, head: str = ""):
        """Спрашивает артикул запчасти клиента (шаг после выбора «новая / б/у»).

        Кнопка «Пропустить»: артикул будет создан автоматически из названия. Вызывается и повторно, когда
        пользователь нажал «Добавить новую» при уже существующем артикуле: нужен другой, уникальный артикул.
        """
        context.user_data.pop('part_article', None)
        text = (f"{head}Введите артикул запчасти.\n"
                f"Если артикула нет, нажмите «Пропустить»: он будет создан автоматически из названия.")
        return await self.ask(update, context, text, kb([SKIP, CANCEL]), ZN_PART_ARTICLE, "zn:menu")

    async def handle_zn_part_article(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «артикул» (текстом): передаёт значение в _zn_part_article."""
        return await self._zn_part_article(update, context, " ".join(update.message.text.split())[:50])

    async def _zn_part_article(self, update, context, article: str):
        """Принимает артикул (пустой — кнопка «Пропустить») и сверяет запчасть по артикулу, а не по названию.

        Артикул уже есть в справочнике — показывает найденные запчасти (с названием) кнопками и «Добавить новую».
        Артикула нет — спрашивает наименование (если оно уже известно, сразу переходит к количеству).
        """
        await self.drop_prompt(context)
        d = context.user_data
        d['part_article'] = article
        if article:
            try:
                found = self.one_c.find_parts_by_article(article)
            except OneCError as e:
                return await self.done_zn(update, context, f"❌ {e}")
            if found:
                return await self.zn_part_options(update, context, found, f"Артикул «{article}» уже есть в справочнике.")
            if d.get('part_name_typed'):
                d['new_part'], d['new_part_article'] = d['part_name_typed'], article
                return await self.ask_qty(
                    update, context,
                    f"Запчасть «{d['new_part']}» (артикул {article}) будет добавлена в «Запчасти для разнесения».")
        elif d.get('part_name_typed'):
            return await self._zn_part_name(update, context, d['part_name_typed'])
        return await self.ask(update, context, "Введите наименование запчасти:", kb([CANCEL]), ZN_PART_NAME, "zn:menu")

    async def zn_part_options(self, update, context, found, head: str):
        """Показывает запчасти, найденные по артикулу: в кнопках название и артикул, плюс «➕ Добавить новую»."""
        context.user_data['part_options'] = found
        listing = "\n".join(f"• {p['name']} (артикул {p['article']})" for p in found)
        rows = [[(f"{p['name']} · {p['article']}"[:60], f"pt:{i}")] for i, p in enumerate(found)]
        rows.append([("➕ Добавить новую", "pt:new")])
        rows.append([CANCEL])
        return await self.ask(update, context, f"{head}\n{listing}\n\nВыберите запчасть или добавьте новую:",
                              kb(*rows), ZN_PART_PICK, "zn:menu")

    async def handle_zn_part_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «наименование запчасти» (текстом): передаёт значение в _zn_part_name."""
        await self.drop_prompt(context)
        return await self._zn_part_name(update, context, " ".join(update.message.text.split())[:100])

    async def _zn_part_name(self, update, context, name: str):
        """Принимает наименование новой запчасти.

        Артикул был введён (и проверен как уникальный) — запчасть будет создана с ним. Артикула нет — он
        формируется из названия (без пробелов, до 25 символов) и тоже сверяется со справочником: если такой
        артикул уже есть, показываются найденные запчасти. Дальше — количество.
        """
        d = context.user_data
        if len(name) < 2:
            return await self.ask(update, context, "Введите наименование запчасти (не короче 2 символов):",
                                  kb([CANCEL]), ZN_PART_NAME, "zn:menu")
        d['part_name_typed'] = name
        article = d.get('part_article', '')
        if article:
            d['new_part'], d['new_part_article'] = name, article
            return await self.ask_qty(
                update, context, f"Запчасть «{name}» (артикул {article}) будет добавлена в «Запчасти для разнесения».")
        generated = "".join(name.split())[:25]
        try:
            found = self.one_c.find_parts_by_article(generated)
        except OneCError as e:
            return await self.done_zn(update, context, f"❌ {e}")
        if found:
            return await self.zn_part_options(
                update, context, found, f"Артикул создаётся из названия («{generated}»), такой уже есть в справочнике.")
        d['new_part'], d['new_part_article'] = name, ""
        return await self.ask_qty(
            update, context,
            f"Запчасть «{name}» (артикул будет создан автоматически: {generated}) "
            f"будет добавлена в «Запчасти для разнесения».")

    async def handle_zn_part_qty(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «количество»: проверяет число (больше 0, не более 9999; допускается запятая) и добавляет
        запчасть клиента в ЗН (OneC.add_customer_part_to_order).

        Если такая запчасть уже есть в материалах заказчика ЗН, количество суммируется. При создании новой
        запчасти пишет запись в logs/added_works.log. Ошибки 1С выводятся текстом.
        """
        await self.drop_prompt(context)
        try:
            qty = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            qty = 0
        if not 0 < qty <= 9999:
            return await self.ask(update, context, "Введите количество больше 0, например 2:",
                                  kb([CANCEL]), ZN_PART_QTY, "zn:menu")
        d = context.user_data
        order, user = d['order'], update.effective_user
        try:
            res = self.one_c.add_customer_part_to_order(
                order['id'], qty, part_id=d.get('part_id', ''), new_part_name=d.get('new_part', ''),
                new_part_article=d.get('new_part_article', ''), used=d.get('part_used', False))
        except OneCError as e:
            return await self.done_zn(update, context, f"❌ {e}")
        if res['created_part']:
            works_log.info("Пользователь %s (%s) добавил запчасть «%s» в папку «%s» (из ЗН №%s)",
                           self.access.get_user(user.id).get('name', ''), user.id, res['part'],
                           OneC.PARTS_GROUP, order['number'])
        logger.info("%s добавил в ЗН №%s запчасть клиента «%s» %s", user.id, order['number'], res['part'], qty)
        unit = f" {res['unit']}" if res['unit'] else ""
        added = " (новая запчасть добавлена в справочник)" if res['created_part'] else ""
        total = (f"\nВсего этой запчасти в ЗН: {self.num(res['total_qty'])}{unit}"
                 if res['total_qty'] != qty else "")
        kind = "б/у" if res['used'] else "новая"
        article = f", артикул {res['article']}" if res['article'] else ""
        return await self.done_zn(
            update, context,
            f"✅ В ЗН №{order['number']} добавлена запчасть клиента ({kind}): {res['part']}{article} — "
            f"{self.num(qty)}{unit}{added}{total}")

    # ---------- создание заказ-наряда ----------

    @staticmethod
    def _back(context) -> str:
        """Куда ведёт «Отмена» на шагах ввода машины: в сценарии создания ЗН — в меню заказ-нарядов,
        иначе — в меню «Клиенты и машины»."""
        return "m:orders" if context.user_data.get('zn_flow') else "m:clients"

    async def handle_zn_new_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «клиент» при создании ЗН: ищет клиента по ФИО или телефону.

        Один найден — берёт его; несколько — предлагает выбрать кнопкой; не найден — предлагает создать нового.
        """
        await self.drop_prompt(context)
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}", 'orders')
        d = context.user_data
        d['zn_query'] = query
        if not clients:
            return await self.ask(update, context, f"Клиент «{query}» не найден. Создать нового?",
                                  kb([("➕ Создать клиента", "zc:new"), CANCEL]), ZN_NEW_PICK, "m:orders")
        if len(clients) == 1:
            return await self.zn_pick_client(update, context, clients[0])
        d['candidates'] = clients
        rows = [[(f"{c['name']} {c['phone']}".strip(), f"zc:{i}")] for i, c in enumerate(clients)]
        rows.append([("➕ Создать нового клиента", "zc:new")])
        rows.append([CANCEL])
        return await self.ask(update, context, "Найдено несколько клиентов, выберите:", kb(*rows),
                              ZN_NEW_PICK, "m:orders")

    async def zn_client_button(self, update, context, value: str):
        """Кнопки выбора клиента при создании ЗН: zc:<n> — выбрать найденного, zc:new — создать нового
        (если введённый текст похож на ФИО, берётся он, иначе бот спросит ФИО)."""
        d = context.user_data
        if value == "new":
            return await self.ask(update, context, "Кого создать?",
                                  kb([("👤 Физ. лицо", "zt:fl"), ("🏢 Юр. лицо", "zt:ul")], [CANCEL]),
                                  ZN_NEW_TYPE, "m:orders")
        candidates = d.get('candidates') or []
        if value.isdigit() and int(value) < len(candidates):
            return await self.zn_pick_client(update, context, candidates[int(value)])
        return await self.route(update, context, "m:orders")

    async def zn_client_type(self, update, context, legal: bool):
        """Тип нового клиента при создании ЗН (кнопки «Физ. лицо» / «Юр. лицо»).

        Если введённый для поиска текст похож на ФИО или название (есть буквы), он берётся как имя клиента и
        бот сразу спрашивает телефон; иначе (например, искали по номеру телефона) спрашивает ФИО/наименование.
        """
        d = context.user_data
        d['zn_legal'] = legal
        query = d.get('zn_query', '')
        if re.search(r'[A-Za-zА-Яа-яЁё]', query):
            d['zn_new_client'] = {"name": " ".join(query.split())[:100], "phone": "", "legal": legal}
            return await self.ask(update, context, f"Новый клиент: {d['zn_new_client']['name']}\nТелефон клиента:",
                                  kb([SKIP, CANCEL]), ZN_NEW_PHONE, "m:orders")
        prompt = ("Введите наименование организации (например, ООО «Ромашка»):" if legal
                  else "Введите ФИО нового клиента (Фамилия Имя Отчество):")
        return await self.ask(update, context, prompt, kb([CANCEL]), ZN_NEW_NAME, "m:orders")

    async def handle_zn_new_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «ФИО нового клиента» (для юрлица — наименование) при создании ЗН: проверяет длину и
        спрашивает телефон."""
        await self.drop_prompt(context)
        name = " ".join(update.message.text.split())
        if len(name) < 2 or len(name) > 100:
            what = "наименование организации" if context.user_data.get('zn_legal') else "ФИО"
            return await self.ask(update, context, f"Введите {what} (от 2 до 100 символов):",
                                  kb([CANCEL]), ZN_NEW_NAME, "m:orders")
        context.user_data['zn_new_client'] = {"name": name, "phone": "", "legal": context.user_data.get('zn_legal', False)}
        return await self.ask(update, context, f"Новый клиент: {name}\nТелефон клиента:",
                              kb([SKIP, CANCEL]), ZN_NEW_PHONE, "m:orders")

    async def handle_zn_new_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «телефон нового клиента» (текстом): передаёт значение в _zn_new_phone."""
        return await self._zn_new_phone(update, context, update.message.text.strip())

    async def _zn_new_phone(self, update, context, phone: str):
        """Запоминает телефон нового клиента (клиент пока не создаётся) и переходит к вводу его машины."""
        await self.drop_prompt(context)
        context.user_data['zn_new_client']['phone'] = phone[:50]
        return await self.zn_new_car(update, context, f"Новый клиент: {context.user_data['zn_new_client']['name']}\n")

    async def zn_pick_client(self, update, context, client):
        """Клиент выбран из найденных: показывает его машины кнопками и «Новая машина»; если машин нет —
        сразу запрашивает данные новой."""
        d = context.user_data
        d['client'] = client
        try:
            cars = self.one_c.get_client_cars(client['id'])
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}", 'orders')
        if not cars:
            return await self.zn_new_car(update, context, f"Клиент: {client['name']}\nУ клиента нет машин.\n")
        d['client_cars'] = cars
        rows = [[(f"🚗 {c['name']}"[:60], f"zk:{i}")] for i, c in enumerate(cars)]
        rows.append([("➕ Новая машина", "zk:new")])
        rows.append([CANCEL])
        return await self.ask(update, context, f"Клиент: {client['name']}\nВыберите машину:", kb(*rows),
                              ZN_CAR_PICK, "m:orders")

    async def zn_car_button(self, update, context, value: str):
        """Кнопки выбора машины при создании ЗН: zk:<n> — машина клиента, zk:new — ввести новую."""
        d = context.user_data
        if value == "new":
            client = d.get('client') or d.get('zn_new_client') or {}
            return await self.zn_new_car(update, context, f"Клиент: {client.get('name', '')}\n")
        cars = d.get('client_cars') or []
        if value.isdigit() and int(value) < len(cars):
            d['zn_car'] = cars[int(value)]
            return await self.zn_confirm(update, context)
        return await self.route(update, context, "m:orders")

    async def zn_new_car(self, update, context, head: str = ""):
        """Запрашивает данные новой машины (марка → модель → госномер → VIN → год): те же шаги, что в разделе
        «Добавить машину», но в конце вместо записи машины показывается итог создания ЗН."""
        return await self.ask(update, context, f"{head}Введите марку автомобиля (например, Toyota):",
                              kb([CANCEL]), CAR_BRAND, "m:orders")

    async def zn_confirm(self, update, context):
        """Итог перед созданием ЗН: клиент (новый или существующий), автомобиль (новый или существующий),
        состояние «В работе». В 1С ничего не пишется до кнопки «Подтвердить» (_zn_create_commit)."""
        d = context.user_data
        new_client, client = d.get('zn_new_client'), d.get('client')
        if new_client:
            kind = "юр. лицо" if new_client.get('legal') else "физ. лицо"
            who = (f"{new_client['name']} (новый клиент, {kind}"
                   + (f", {new_client['phone']}" if new_client['phone'] else "") + ")")
        else:
            who = client['name']
        new_car = d.get('new_car')
        if new_car:
            extra = ", ".join(x for x in (f"VIN {new_car['vin']}" if new_car['vin'] else "",
                                          f"{new_car['year']} г." if new_car['year'] else "") if x)
            car = f"{new_car['brand']} {new_car['model']}, {new_car['gos']}" + (f", {extra}" if extra else "") + " (новая машина)"
        else:
            car = d['zn_car']['name']
        text = (f"Проверьте данные нового заказ-наряда:\n\n👤 Клиент: {who}\n🚗 Автомобиль: {car}\n"
                f"📌 Состояние: В работе\n\nСоздать заказ-наряд в 1С?")
        return await self.ask(update, context, text, kb([OK, CANCEL]), ZN_NEW_CONFIRM, "m:orders")

    async def _zn_create_commit(self, update, context):
        """Подтверждение: создаёт в 1С (в одной транзакции) нового клиента и машину, если они новые, и сам
        заказ-наряд в состоянии «В работе» (OneC.create_work_order). Затем предлагает добавить работы:
        к работам, добавленным в этот ЗН через бота, ставится исполнитель по умолчанию."""
        await self.drop_prompt(context)
        d = context.user_data
        client, car = d.get('client'), d.get('zn_car')
        try:
            res = self.one_c.create_work_order(
                client_id=client['id'] if client and not d.get('zn_new_client') else "",
                new_client=d.get('zn_new_client'),
                car_id=car['id'] if car else "",
                new_car=d.get('new_car'))
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}", 'orders')
        logger.info("%s создал заказ-наряд №%s (%s, %s)", update.effective_user.id, res['number'],
                    res['customer'], res['car'])
        self.clear_flow(context)
        d['order'] = {"id": res['id'], "number": res['number'], "customer": res['customer'],
                      "car": res['car'], "default_executor": True}
        await update.effective_message.reply_text(
            f"✅ Заказ-наряд №{res['number']} создан в 1С (состояние «В работе»).\n"
            f"👤 {res['customer']}\n🚗 {res['car']}\n\nДобавить работы? (исполнитель — по умолчанию)",
            reply_markup=kb([("➕ Добавить работы", "zn:add")], [("Нет, готово", "zn:menu")]))
        return MENU

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
        """Шаг «найти клиента»: ищет по ФИО или телефону и выводит клиентов вместе с их машинами.

        Под результатом, если у пользователя есть право добавлять машины, для каждого найденного клиента
        есть кнопка «Добавить машину: ФИО» (callback cc:<n>) — она сразу запускает ввод машины для него.
        Найденные клиенты запоминаются в user_data['found'].
        """
        await self.drop_prompt(context)
        query = update.message.text.strip()
        try:
            clients = self.one_c.find_clients(query)
            if not clients:
                return await self.done(update, context, f"Клиент «{query}» не найден.")
            blocks = [self.format_client(c, self.one_c.get_client_cars(c['id'])) for c in clients]
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        self.clear_flow(context)
        context.user_data['found'] = clients
        title, menu = self.menu_view(update.effective_user.id, 'clients')
        rows = []
        if self.access.has_permission(update.effective_user.id, P.ADD_CAR):
            rows = kb(*[[(f"🚗 Добавить машину: {c['name']}"[:60], f"cc:{i}")] for i, c in enumerate(clients)]).inline_keyboard
        markup = InlineKeyboardMarkup(list(rows) + list(menu.inline_keyboard))
        await update.effective_message.reply_text("\n\n".join(blocks) + f"\n\n{title}", reply_markup=markup)
        return MENU

    # ---------- добавление клиента ----------

    async def handle_client_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «ФИО клиента» (для юрлица — наименование организации): проверяет длину (2–100 символов)
        и спрашивает телефон. Тип клиента (физ. / юр. лицо) выбран кнопкой в меню (user_data['client_legal'])."""
        await self.drop_prompt(context)
        name = " ".join(update.message.text.split())
        if len(name) < 2 or len(name) > 100:
            what = "наименование организации" if context.user_data.get('client_legal') else "ФИО"
            return await self.ask(update, context, f"Введите {what} (от 2 до 100 символов):",
                                  kb([CANCEL]), CLIENT_NAME, "m:clients")
        context.user_data['client_name'] = name
        return await self.ask(update, context, "Телефон клиента:", kb([SKIP, CANCEL]), CLIENT_PHONE, "m:clients")

    async def handle_client_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «телефон клиента» (текстом): передаёт значение в _client_phone."""
        return await self._client_phone(update, context, update.message.text.strip())

    async def _client_phone(self, update, context, phone: str):
        """Принимает телефон и показывает данные клиента на подтверждение (в 1С пока ничего не пишется).

        Параметры: phone — телефон или пустая строка (кнопка «Пропустить»).
        Следующий шаг — кнопка «Подтвердить» (_client_commit) или «Отмена».
        """
        await self.drop_prompt(context)
        d = context.user_data
        d['phone'] = phone[:50]
        kind = "юридическое лицо" if d.get('client_legal') else "физическое лицо"
        text = (f"Проверьте данные нового клиента:\n\n{'🏢' if d.get('client_legal') else '👤'} {d['client_name']} ({kind})\n"
                f"☎️ {d['phone'] or 'телефон не указан'}\n\nСоздать клиента в 1С?")
        return await self.ask(update, context, text, kb([OK, CANCEL]), CLIENT_CONFIRM, "m:clients")

    async def _client_commit(self, update, context):
        """Подтверждение клиента: создаёт его в 1С (OneC.add_client) и сразу переходит к вводу машины.

        Если клиент с таким ФИО уже есть, ничего не создаётся: пользователю сообщается об этом и машина
        добавляется существующему клиенту. На первом вопросе о машине есть кнопка «Без машины».
        """
        d = context.user_data
        try:
            res = self.one_c.add_client(d['client_name'], d['phone'], legal=d.get('client_legal', False))
        except OneCError as e:
            await self.drop_prompt(context)
            return await self.done(update, context, f"❌ {e}")
        if res['created']:
            logger.info("%s добавил клиента %s", update.effective_user.id, res['name'])
            head = f"✅ Клиент «{res['name']}» создан в 1С."
        else:
            head = f"Клиент «{res['name']}» уже есть в 1С."
        client = {"id": res["id"], "name": res["name"], "phone": res["phone"]}
        for key in ('client_name', 'phone', 'client_legal'):
            d.pop(key, None)
        return await self.choose_client(update, context, client, head=head, optional=True)

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

    async def choose_client(self, update, context, client, head: str = "", optional: bool = False,
                            new: bool = False):
        """Запоминает владельца машины и спрашивает марку автомобиля.

        Параметры: client — {"id", "name", "phone"}; head — строка сверху (например, «Клиент создан»);
        optional — машина необязательна (сразу после создания клиента): добавляется кнопка «Без машины»;
        new — задать вопрос новым сообщением (из результата поиска, чтобы не затирать его).
        """
        d = context.user_data
        d['client'] = client
        d['after_client'] = optional
        text = (f"{head}\n\n" if head else "") + f"Клиент: {client['name']}\nВведите марку автомобиля (например, Toyota):"
        markup = kb([SKIP_CAR, CANCEL]) if optional else kb([CANCEL])
        return await self.ask(update, context, text, markup, CAR_BRAND, "m:clients", new=new)

    async def _skip_car(self, update, context, _value=""):
        """Кнопка «Без машины» после создания клиента: завершает сценарий, клиент остаётся без машины."""
        await self.drop_prompt(context)
        d = context.user_data
        if not d.get('after_client') or not d.get('client'):
            return await self.route(update, context, "m:clients")
        return await self.done(update, context, f"Клиент «{d['client']['name']}» оставлен без машины.")

    async def handle_car_brand(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «марка автомобиля» (например, Toyota)."""
        await self.drop_prompt(context)
        context.user_data['brand'] = update.message.text.strip()[:40]
        return await self.ask(update, context, "Модель (например, Camry):", kb([CANCEL]), CAR_MODEL, self._back(context))

    async def handle_car_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «модель автомобиля» (например, Camry)."""
        await self.drop_prompt(context)
        context.user_data['model'] = update.message.text.strip()[:40]
        return await self.ask(update, context, "Госномер (например, А123БВ77):", kb([CANCEL]), CAR_GOS, self._back(context))

    async def handle_car_gos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «госномер»: убирает пробелы, приводит к верхнему регистру, проверяет длину (4–12 символов)."""
        await self.drop_prompt(context)
        gos = re.sub(r'\s+', '', update.message.text).upper()
        if len(gos) < 4 or len(gos) > 12:
            return await self.ask(update, context, "Госномер выглядит неверно, введите ещё раз:",
                                  kb([CANCEL]), CAR_GOS, self._back(context))
        if context.user_data.get('zn_flow'):
            try:
                duplicate = self.one_c.find_car_by_gos_number(gos)
            except OneCError as e:
                return await self.done(update, context, f"❌ {e}", 'orders')
            if duplicate:
                owner = f", владелец: {duplicate['owner']}" if duplicate.get('owner') else ""
                return await self.ask(
                    update, context,
                    f"Машина с номером {gos} уже есть в 1С ({duplicate['name']}{owner}). Введите другой госномер:",
                    kb([CANCEL]), CAR_GOS, self._back(context))
        context.user_data['gos'] = gos
        return await self.ask(update, context, "VIN (17 символов):", kb([SKIP, CANCEL]), CAR_VIN, self._back(context))

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
                                  kb([SKIP, CANCEL]), CAR_VIN, self._back(context))
        context.user_data['vin'] = vin
        return await self.ask(update, context, "Год выпуска (например, 2020):", kb([SKIP, CANCEL]), CAR_YEAR, self._back(context))

    async def handle_car_year(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «год выпуска» (текстом): передаёт значение в _car_year."""
        return await self._car_year(update, context, update.message.text.strip())

    async def _car_year(self, update, context, text: str):
        """Проверяет год (1950 … следующий год) или принимает пустой и показывает итог на подтверждение.

        В итоге перечислено всё: кому (клиент и телефон) и какая машина (марка, модель, госномер, VIN, год).
        В 1С ничего не пишется до нажатия «Подтвердить» (_car_commit).
        """
        await self.drop_prompt(context)
        year = 0
        if text:
            max_year = datetime.date.today().year + 1
            if not text.isdigit() or not 1950 <= int(text) <= max_year:
                return await self.ask(update, context, f"Введите год от 1950 до {max_year} или пропустите:",
                                      kb([SKIP, CANCEL]), CAR_YEAR, self._back(context))
            year = int(text)
        d = context.user_data
        d['year'] = year
        if d.get('zn_flow'):
            d['new_car'] = {"brand": d['brand'], "model": d['model'], "gos": d['gos'],
                            "vin": d.get('vin', ''), "year": year}
            return await self.zn_confirm(update, context)
        client = d['client']
        owner = client['name'] + (f" ({client['phone']})" if client.get('phone') else "")
        text = "\n".join([
            "Проверьте данные перед отправкой в 1С:", "",
            f"👤 Кому: {owner}",
            f"🚗 Машина: {d['brand']} {d['model']}",
            f"🔢 Госномер: {d['gos']}",
            f"VIN: {d['vin'] or 'не указан'}",
            f"Год выпуска: {year or 'не указан'}", "",
            "Добавить машину этому клиенту в 1С?"])
        return await self.ask(update, context, text, kb([OK, CANCEL]), CAR_CONFIRM, self._back(context))

    async def _car_commit(self, update, context):
        """Подтверждение машины: создаёт её в 1С (OneC.add_car) и привязывает к клиенту.

        Если машина с таким госномером уже есть, сообщает название и владельца и ничего не создаёт.
        """
        await self.drop_prompt(context)
        d = context.user_data
        year = d.get('year', 0)
        try:
            res = self.one_c.add_car(d['client']['id'], d['brand'], d['model'], d['gos'], d['vin'], year)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}")
        if not res['created']:
            owner = f", владелец: {res['owner']}" if res.get('owner') else ""
            return await self.done(update, context, f"Машина с номером {d['gos']} уже есть в 1С ({res['name']}{owner}).")
        logger.info("%s добавил авто %s клиенту %s", update.effective_user.id, d['gos'], d['client']['name'])
        return await self.done(update, context, f"✅ Машина «{res['name']}» добавлена клиенту {d['client']['name']}.")

    # ---------- справочники: запчасти и названия работ ----------

    async def handle_cat_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Шаг «название» при добавлении запчасти или названия работы (user_data['cat'] = 'part' | 'work').

        Сверяет с 1С: точное совпадение (без учёта регистра) — сообщает, что запись существует;
        есть похожие — показывает их списком и кнопки «Добавить» / «Отмена»; ничего похожего нет —
        добавляет запись сразу. Запчасти попадают в папку «Запчасти для разнесения»,
        работы — в «Работы для разнесения».
        """
        await self.drop_prompt(context)
        d = context.user_data
        kind = d.get('cat', 'part')
        what = "запчасти" if kind == "part" else "работы"
        name = " ".join(update.message.text.split())
        if len(name) < 2 or len(name) > 100:
            return await self.ask(update, context, f"Введите название {what} (от 2 до 100 символов):",
                                  kb([CANCEL]), CAT_NAME, "m:refs")
        try:
            found = self.one_c.find_parts(name) if kind == "part" else self.one_c.find_works(name)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}", 'refs')
        if found['exact']:
            return await self.done(update, context, f"Такая запись уже существует: «{found['exact']['name']}».", 'refs')
        if not found['similar']:
            return await self._cat_add(update, context, name)
        d['cat_name'] = name
        folder = OneC.PARTS_GROUP if kind == "part" else OneC.WORK_GROUP
        listing = "\n".join(f"• {w['name']}" for w in found['similar'])
        text = (f"Точной записи нет, но есть похожие:\n{listing}\n\n"
                f"Добавить «{name}» как новую в папку «{folder}»?")
        return await self.ask(update, context, text, kb([("➕ Добавить", "x:ok"), CANCEL]), CAT_CONFIRM, "m:refs")

    async def _cat_commit(self, update, context):
        """Кнопка «Добавить» под списком похожих записей: добавляет введённое название."""
        await self.drop_prompt(context)
        return await self._cat_add(update, context, context.user_data.get('cat_name', ''))

    async def _cat_add(self, update, context, name: str):
        """Создаёт запчасть (OneC.add_part) или работу (OneC.add_work) и сообщает результат.

        Создание пишется в logs/added_works.log (кто, что и в какую папку добавил). Если запись за это
        время появилась в 1С, сообщает, что такая запись существует.
        """
        kind = context.user_data.get('cat', 'part')
        try:
            res = self.one_c.add_part(name) if kind == "part" else self.one_c.add_work(name)
        except OneCError as e:
            return await self.done(update, context, f"❌ {e}", 'refs')
        if not res['created']:
            return await self.done(update, context, f"Такая запись уже существует: «{res['name']}».", 'refs')
        what = "запчасть" if kind == "part" else "работу"
        user = update.effective_user
        works_log.info("Пользователь %s (%s) добавил %s «%s» в папку «%s»",
                       self.access.get_user(user.id).get('name', ''), user.id, what, res['name'], res['folder'])
        label = "Запчасть" if kind == "part" else "Работа"
        return await self.done(update, context,
                               f"✅ {label} «{res['name']}» добавлена в папку «{res['folder']}».", 'refs')

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
                ZN_WORK_AMOUNT: st(self.handle_zn_work_amount),
                CLIENT_CONFIRM: st(self.pick_by_button),
                CAR_CONFIRM: st(self.pick_by_button),
                ZN_NEW_CLIENT: st(self.handle_zn_new_client),
                ZN_NEW_PICK: st(self.pick_by_button),
                ZN_NEW_TYPE: st(self.pick_by_button),
                ZN_NEW_NAME: st(self.handle_zn_new_name),
                ZN_NEW_PHONE: st(self.handle_zn_new_phone),
                ZN_CAR_PICK: st(self.pick_by_button),
                ZN_NEW_CONFIRM: st(self.pick_by_button),
                ZN_PART_COND: st(self.pick_by_button),
                ZN_PART_ARTICLE: st(self.handle_zn_part_article),
                ZN_PART_NAME: st(self.handle_zn_part_name),
                ZN_PART_PICK: st(self.pick_by_button),
                ZN_PART_QTY: st(self.handle_zn_part_qty),
                CAT_NAME: st(self.handle_cat_name),
                CAT_CONFIRM: st(self.pick_by_button),
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
