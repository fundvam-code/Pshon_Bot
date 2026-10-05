"""Работа с базой 1С:Альфа авто через COM (запись и поиск напрямую в 1С)."""
import datetime
import logging
import os
import uuid
from typing import Dict, List, Optional

import pywintypes
import win32com.client

logger = logging.getLogger(__name__)

SEARCH_LIMIT = 10


class OneCError(Exception):
    """Ошибка работы с 1С (текст можно показывать пользователю)."""


class OneC:
    def __init__(self, db_path: str, user: str = "", password: str = ""):
        self.db_path = db_path
        self.user = user
        self.password = password
        self.c = None
        self._currency = None

    # ---------- подключение ----------

    def connect(self) -> bool:
        try:
            connector = win32com.client.Dispatch("V83.COMConnector")
            self.c = connector.Connect(
                f"File='{self.db_path}';Usr='{self.user}';Pwd='{self.password}';"
            )
            self._currency = None
            logger.info("Подключено к 1С: %s", self.db_path)
            return True
        except Exception as e:
            self.c = None
            logger.error("Ошибка подключения к 1С: %s", e)
            return False

    def _ensure(self):
        if self.c is None and not self.connect():
            raise OneCError("Нет связи с 1С. Попробуйте позже.")

    # ---------- вспомогательное ----------

    def _messages(self) -> str:
        try:
            return "\n".join(m.Текст for m in self.c.ПолучитьСообщенияПользователю(False))
        except Exception:
            return ""

    def _write(self, obj, what: str):
        try:
            obj.Записать()
        except pywintypes.com_error as e:
            detail = self._messages()
            logger.error("Не удалось записать %s: %s | %s", what, e, detail)
            raise OneCError(f"1С не приняла запись ({what}): {detail or 'ошибка записи'}")

    def _query(self, text: str, params: Optional[Dict] = None):
        q = self.c.NewObject("Запрос")
        q.Текст = text
        for k, v in (params or {}).items():
            q.УстановитьПараметр(k, v)
        return q.Выполнить().Выбрать()

    def _trim(self, catalog_name: str, text: str) -> str:
        length = getattr(self.c.Метаданные.Справочники, catalog_name).ДлинаНаименования
        return text[:length] if length else text

    def _ref_id(self, ref) -> str:
        return self.c.XMLString(ref)

    def _ref(self, catalog_name: str, ref_id: str):
        guid = self.c.NewObject("УникальныйИдентификатор", ref_id)
        return getattr(self.c.Справочники, catalog_name).ПолучитьСсылку(guid)

    def _info_kind(self, name: str):
        return getattr(self.c.Перечисления.ДополнительнаяИнформацияАвтомобилей, name)

    def _accounting_currency(self):
        if self._currency is None:
            cur = os.getenv("ONE_C_CURRENCY", "").strip()
            if cur:
                sel = self._query(
                    "ВЫБРАТЬ ПЕРВЫЕ 1 Вл.Ссылка КАК Ссылка ИЗ Справочник.Валюты КАК Вл "
                    "ГДЕ Вл.Наименование = &Н", {"Н": cur})
            else:
                sel = self._query(
                    "ВЫБРАТЬ ПЕРВЫЕ 1 М.ВалютаУчета КАК Ссылка ИЗ Справочник.Модели КАК М "
                    "ГДЕ НЕ М.ВалютаУчета = ЗНАЧЕНИЕ(Справочник.Валюты.ПустаяСсылка) "
                    "И НЕ М.ВалютаУчета.ПометкаУдаления")
            if not sel.Следующий():
                sel = self._query(
                    "ВЫБРАТЬ ПЕРВЫЕ 1 Вл.Ссылка КАК Ссылка ИЗ Справочник.Валюты КАК Вл "
                    "ГДЕ НЕ Вл.ПометкаУдаления")
                if not sel.Следующий():
                    raise OneCError("В 1С не найдена валюта учёта.")
            self._currency = sel.Ссылка
        return self._currency

    # ---------- поиск ----------

    def find_clients(self, text: str) -> List[Dict]:
        self._ensure()
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")
        pattern = f"%{escaped}%"
        try:
            sel = self._query(
                "ВЫБРАТЬ ПЕРВЫЕ " + str(SEARCH_LIMIT) + " К.Ссылка КАК Ссылка, К.Наименование КАК Имя, "
                "К.ОсновнойТелефон КАК Телефон ИЗ Справочник.Контрагенты КАК К "
                "ГДЕ НЕ К.ЭтоГруппа И НЕ К.ПометкаУдаления "
                "И (К.Наименование ПОДОБНО &Т СПЕЦСИМВОЛ \"\\\" ИЛИ К.ОсновнойТелефон ПОДОБНО &Т СПЕЦСИМВОЛ \"\\\") "
                "УПОРЯДОЧИТЬ ПО К.Наименование",
                {"Т": pattern})
            result = []
            while sel.Следующий():
                result.append({"id": self._ref_id(sel.Ссылка), "name": sel.Имя, "phone": sel.Телефон or ""})
            return result
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка поиска клиентов: %s", e)
            raise OneCError("Ошибка поиска в 1С.")

    def get_client_cars(self, client_id: str) -> List[Dict]:
        self._ensure()
        try:
            client = self._ref("Контрагенты", client_id)
            sel = self._query(
                "ВЫБРАТЬ Р.Автомобиль КАК Авто, Р.Автомобиль.Наименование КАК Название, "
                "Р.Автомобиль.VIN КАК Вин, Р.Автомобиль.ГодВыпуска КАК Выпуск "
                "ИЗ РегистрСведений.Автомобили.СрезПоследних КАК Р "
                "ГДЕ Р.ВидЗначения = ЗНАЧЕНИЕ(Перечисление.ДополнительнаяИнформацияАвтомобилей.Хозяин) "
                "И Р.Значение = &Клиент И НЕ Р.Автомобиль.ПометкаУдаления",
                {"Клиент": client})
            cars = []
            while sel.Следующий():
                year = ""
                try:
                    if sel.Выпуск.year > 1900:
                        year = str(sel.Выпуск.year)
                except Exception:
                    pass
                cars.append({"ref": sel.Авто, "name": sel.Название, "vin": sel.Вин or "", "year": year})
            for car in cars:
                car["gos_number"] = self._car_gos_number(car.pop("ref"))
            return cars
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка получения автомобилей: %s", e)
            raise OneCError("Ошибка чтения автомобилей из 1С.")

    def _car_gos_number(self, car_ref) -> str:
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 Р.Значение КАК Знач ИЗ РегистрСведений.Автомобили.СрезПоследних КАК Р "
            "ГДЕ Р.Автомобиль = &А И Р.ВидЗначения = "
            "ЗНАЧЕНИЕ(Перечисление.ДополнительнаяИнформацияАвтомобилей.ГосНомер)",
            {"А": car_ref})
        return self.c.String(sel.Знач) if sel.Следующий() and sel.Знач else ""

    def find_car_by_gos_number(self, gos_number: str) -> Optional[Dict]:
        self._ensure()
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 Р.Автомобиль КАК Авто, Р.Автомобиль.Наименование КАК Название "
            "ИЗ РегистрСведений.Автомобили.СрезПоследних КАК Р "
            "ГДЕ Р.ВидЗначения = ЗНАЧЕНИЕ(Перечисление.ДополнительнаяИнформацияАвтомобилей.ГосНомер) "
            "И Р.Значение = &Н И НЕ Р.Автомобиль.ПометкаУдаления",
            {"Н": gos_number})
        if not sel.Следующий():
            return None
        owner = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 Р.Значение КАК Знач ИЗ РегистрСведений.Автомобили.СрезПоследних КАК Р "
            "ГДЕ Р.Автомобиль = &А И Р.ВидЗначения = "
            "ЗНАЧЕНИЕ(Перечисление.ДополнительнаяИнформацияАвтомобилей.Хозяин)",
            {"А": sel.Авто})
        return {"name": sel.Название, "owner": self.c.String(owner.Знач) if owner.Следующий() and owner.Знач else ""}

    def count_open_work_orders(self) -> Dict:
        """Журнал «Заказ-наряд»: количество ЗН, не находящихся в состоянии «Закрыт»."""
        self._ensure()
        try:
            closed = self._query(
                "ВЫБРАТЬ ПЕРВЫЕ 1 С.Ссылка КАК Ссылка ИЗ Справочник.ВидыСостоянийЗаказНарядов КАК С "
                "ГДЕ С.Наименование = &Н", {"Н": "Закрыт"})
            closed_ref = closed.Ссылка if closed.Следующий() else self.c.Справочники.ВидыСостоянийЗаказНарядов.ПустаяСсылка()
            sel = self._query(
                "ВЫБРАТЬ Ж.Состояние КАК Сост, КОЛИЧЕСТВО(*) КАК Кол "
                "ИЗ ЖурналДокументов.ЗаказНаряд КАК Ж "
                "ГДЕ Ж.Ссылка ССЫЛКА Документ.ЗаказНаряд И НЕ Ж.ПометкаУдаления И Ж.Состояние <> &Закрыт "
                "СГРУППИРОВАТЬ ПО Ж.Состояние УПОРЯДОЧИТЬ ПО Ж.Состояние.Порядок",
                {"Закрыт": closed_ref})
            by_state = []
            while sel.Следующий():
                by_state.append((self.c.String(sel.Сост) or "Без состояния", int(sel.Кол)))
            rows = self._query(
                "ВЫБРАТЬ ПЕРВЫЕ 40 Д.Номер КАК Номер, Д.Заказчик.Наименование КАК Заказчик, "
                "Д.Автомобиль.Наименование КАК Авто, Д.СуммаРаботДокумента КАК Итого, "
                "Д.ВалютаДокумента.Наименование КАК Валюта, Д.Состояние.Наименование КАК Сост "
                "ИЗ Документ.ЗаказНаряд КАК Д "
                "ГДЕ НЕ Д.ПометкаУдаления И Д.Состояние <> &Закрыт УПОРЯДОЧИТЬ ПО Д.Дата УБЫВ",
                {"Закрыт": closed_ref})
            orders = []
            while rows.Следующий():
                orders.append({"number": str(rows.Номер).lstrip("0") or "0", "customer": rows.Заказчик or "—",
                               "car": rows.Авто or "—", "total": float(rows.Итого),
                               "currency": rows.Валюта or "", "state": rows.Сост or "Без состояния"})
            return {"total": sum(n for _, n in by_state), "by_state": by_state, "orders": orders}
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка подсчёта заказ-нарядов: %s", e)
            raise OneCError("Ошибка чтения журнала заказ-нарядов из 1С.")

    # ---------- заказ-наряды: просмотр ----------

    WORK_GROUP = "Работы для разнесения"

    def _order_ref(self, order_id: str):
        guid = self.c.NewObject("УникальныйИдентификатор", order_id)
        return self.c.Документы.ЗаказНаряд.ПолучитьСсылку(guid)

    def list_orders_in_progress(self) -> List[Dict]:
        self._ensure()
        try:
            sel = self._query(
                "ВЫБРАТЬ ПЕРВЫЕ 30 Д.Ссылка КАК Ссылка, Д.Номер КАК Номер, "
                "Д.Заказчик.Наименование КАК Заказчик, Д.Автомобиль.Наименование КАК Авто "
                "ИЗ Документ.ЗаказНаряд КАК Д "
                "ГДЕ НЕ Д.ПометкаУдаления И Д.Состояние.Наименование = &Состояние "
                "УПОРЯДОЧИТЬ ПО Д.Дата УБЫВ", {"Состояние": "В работе"})
            result = []
            while sel.Следующий():
                result.append({"id": self._ref_id(sel.Ссылка), "number": str(sel.Номер).lstrip("0") or "0",
                               "customer": sel.Заказчик or "—", "car": sel.Авто or "—"})
            return result
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка чтения списка ЗН: %s", e)
            raise OneCError("Ошибка чтения заказ-нарядов из 1С.")

    def get_order_works(self, order_id: str) -> Dict:
        self._ensure()
        try:
            ref = self._order_ref(order_id)
            head = self._query(
                "ВЫБРАТЬ Д.Номер КАК Номер, Д.Заказчик.Наименование КАК Заказчик, Д.Автомобиль.Наименование КАК Авто, "
                "Д.СуммаРаботДокумента КАК Итого, Д.ВалютаДокумента.Наименование КАК Валюта "
                "ИЗ Документ.ЗаказНаряд КАК Д ГДЕ Д.Ссылка = &Д", {"Д": ref})
            if not head.Следующий():
                raise OneCError("Заказ-наряд не найден.")
            rows = self._query(
                "ВЫБРАТЬ Р.Работа.Наименование КАК Работа, Р.Количество КАК Часы, Р.СуммаВсего КАК Сумма "
                "ИЗ Документ.ЗаказНаряд.Работы КАК Р ГДЕ Р.Ссылка = &Д УПОРЯДОЧИТЬ ПО Р.НомерСтроки", {"Д": ref})
            works = []
            while rows.Следующий():
                works.append({"name": rows.Работа or "—", "hours": float(rows.Часы), "sum": float(rows.Сумма)})
            return {"number": str(head.Номер).lstrip("0") or "0", "customer": head.Заказчик or "—",
                    "car": head.Авто or "—", "total": float(head.Итого), "currency": head.Валюта or "",
                    "works": works}
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка чтения работ ЗН: %s", e)
            raise OneCError("Ошибка чтения работ заказ-наряда из 1С.")

    # ---------- заказ-наряды: работы ----------

    @staticmethod
    def _like(word: str) -> str:
        for ch in ("\\", "%", "_", "["):
            word = word.replace(ch, "\\" + ch)
        return "%" + word + "%"

    def find_works(self, name: str) -> Dict:
        """Поиск в справочнике «Автоработы»: {'exact': {...}|None, 'similar': [...]}."""
        self._ensure()
        name = " ".join(name.split())
        words = [w for w in name.split() if len(w) >= 3][:3] or [name]
        try:
            def search(join: str):
                params = {f"W{i}": self._like(w) for i, w in enumerate(words)}
                conds = [f'А.Наименование ПОДОБНО &W{i} СПЕЦСИМВОЛ "\\"' for i in range(len(words))]
                sel = self._query(
                    "ВЫБРАТЬ ПЕРВЫЕ 8 А.Ссылка КАК Ссылка, А.Наименование КАК Имя ИЗ Справочник.Автоработы КАК А "
                    "ГДЕ НЕ А.ЭтоГруппа И НЕ А.ПометкаУдаления И (" + join.join(conds) + ") "
                    "УПОРЯДОЧИТЬ ПО А.Наименование", params)
                out = []
                while sel.Следующий():
                    out.append({"id": self._ref_id(sel.Ссылка), "name": sel.Имя})
                return out

            found = search(" И ") or search(" ИЛИ ")
            exact = next((w for w in found if w["name"].strip().lower() == name.lower()), None)
            similar = [w for w in found if w is not exact]
            return {"exact": exact, "similar": similar}
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка поиска работ: %s", e)
            raise OneCError("Ошибка поиска в справочнике авторабот.")

    def _work_group(self):
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 А.Ссылка КАК Ссылка ИЗ Справочник.Автоработы КАК А "
            "ГДЕ А.ЭтоГруппа И А.Наименование = &Н И НЕ А.ПометкаУдаления", {"Н": self.WORK_GROUP})
        if sel.Следующий():
            return sel.Ссылка
        group = self.c.Справочники.Автоработы.СоздатьГруппу()
        group.Наименование = self.WORK_GROUP
        self._write(group, "группа авторабот")
        logger.info("Создана группа авторабот «%s»", self.WORK_GROUP)
        return group.Ссылка

    def _work_nomenclature(self):
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 Н.Ссылка КАК Ссылка ИЗ Справочник.Номенклатура КАК Н "
            "ГДЕ Н.Наименование = &Н И НЕ Н.ЭтоГруппа И НЕ Н.ПометкаУдаления", {"Н": "Авторабота"})
        if sel.Следующий():
            return sel.Ссылка
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 А.Номенклатура КАК Ссылка ИЗ Справочник.Автоработы КАК А "
            "ГДЕ НЕ А.ЭтоГруппа И НЕ А.Номенклатура = ЗНАЧЕНИЕ(Справочник.Номенклатура.ПустаяСсылка)")
        if sel.Следующий():
            return sel.Ссылка
        raise OneCError("В 1С не найдена номенклатура «Авторабота».")

    def _create_work(self, name: str):
        work = self.c.Справочники.Автоработы.СоздатьЭлемент()
        work.Родитель = self._work_group()
        work.Наименование = self._trim("Автоработы", name)
        work.НаименованиеПолное = name
        work.Номенклатура = self._work_nomenclature()
        self._write(work, "авторабота")
        return work.Ссылка

    def _work_in_order(self, order_ref, work_ref) -> bool:
        q = self.c.NewObject("Запрос")
        q.Текст = ("ВЫБРАТЬ ПЕРВЫЕ 1 1 КАК Признак ИЗ Документ.ЗаказНаряд.Работы КАК Р "
                   "ГДЕ Р.Ссылка = &Д И Р.Работа = &Р")
        q.УстановитьПараметр("Д", order_ref)
        q.УстановитьПараметр("Р", work_ref)
        return not q.Выполнить().Пустой()

    def _default_vat(self):
        sel = self._query("ВЫБРАТЬ ПЕРВЫЕ 1 С.Ссылка КАК Ссылка ИЗ Справочник.СтавкиНДС КАК С ГДЕ С.Наименование = &Н",
                          {"Н": "Без НДС"})
        return sel.Ссылка if sel.Следующий() else self.c.Справочники.СтавкиНДС.ПустаяСсылка()

    def add_work_to_order(self, order_id: str, hours: float, work_id: str = "", new_work_name: str = "") -> Dict:
        """Добавляет строку работы в ЗН (при необходимости создаёт работу в справочнике).
        Проведённый документ перепроводится, как это делает форма."""
        self._ensure()
        if not work_id and not new_work_name:
            raise OneCError("Не указана работа.")
        try:
            self.c.НачатьТранзакцию()
            try:
                created = not work_id
                if work_id:
                    guid = self.c.NewObject("УникальныйИдентификатор", work_id)
                    work_ref = self.c.Справочники.Автоработы.ПолучитьСсылку(guid)
                else:
                    work_ref = self._create_work(new_work_name)
                norm = self._query(
                    "ВЫБРАТЬ ПЕРВЫЕ 1 Н.Ссылка КАК Ссылка, Н.Цена КАК Цена ИЗ Справочник.Нормочасы КАК Н "
                    "ГДЕ НЕ Н.ПометкаУдаления УПОРЯДОЧИТЬ ПО Н.Код")
                if not norm.Следующий():
                    raise OneCError("В 1С не найден нормочас.")
                order_ref = self._order_ref(order_id)
                if work_id and self._work_in_order(order_ref, work_ref):
                    raise OneCError("Эта работа уже есть в заказ-наряде (1С не допускает дубли строк).")
                doc = order_ref.ПолучитьОбъект()
                if self.c.String(doc.Состояние) != "В работе":
                    raise OneCError("ЗН уже не в состоянии «В работе».")
                vat = doc.Работы.Получить(0).СтавкаНДС if doc.Работы.Количество() else self._default_vat()
                price = float(norm.Цена)
                amount = round(hours * price, 2)
                row = doc.Работы.Добавить()
                row.Работа = work_ref
                row.ИдентификаторРаботы = str(uuid.uuid4())
                row.Количество = hours
                row.Нормочас = norm.Ссылка
                row.Коэффициент = 1
                row.Цена = price
                row.Сумма = amount
                row.СтавкаНДС = vat
                row.СуммаНДС = 0
                row.СуммаВсего = amount
                row.ПакетРабот = str(uuid.uuid4())
                row.НомерПакета = 1
                try:
                    if doc.Проведен:
                        doc.Записать(self.c.РежимЗаписиДокумента.Проведение)
                    else:
                        doc.Записать()
                except pywintypes.com_error as e:
                    detail = self._messages()
                    logger.error("ЗН не записан: %s | %s", e, detail)
                    raise OneCError(f"1С не приняла изменение ЗН: {detail or 'ошибка записи'}")
                self.c.ЗафиксироватьТранзакцию()
            except Exception:
                self.c.ОтменитьТранзакцию()
                raise
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка добавления работы: %s", e)
            raise OneCError("Ошибка связи с 1С при добавлении работы.")
        return {"created_work": created, "work": self.c.String(work_ref),
                "total": float(doc.СуммаРаботДокумента), "line_sum": amount}

    # ---------- запись ----------

    def add_client(self, full_name: str, phone: str = "") -> Dict:
        """Создаёт клиента (контрагент: покупатель, частное лицо). created=False, если такой уже есть."""
        self._ensure()
        full_name = " ".join(full_name.split())
        existing = self.find_clients(full_name)
        for client in existing:
            if client["name"].lower() == full_name.lower():
                return {**client, "created": False}

        parts = full_name.split(" ", 2)
        try:
            self.c.НачатьТранзакцию()
            try:
                obj = self.c.Справочники.Контрагенты.СоздатьЭлемент()
                obj.Наименование = self._trim("Контрагенты", full_name)
                obj.НаименованиеПолное = full_name
                obj.Фамилия = parts[0]
                obj.Имя = parts[1] if len(parts) > 1 else ""
                obj.Отчество = parts[2] if len(parts) > 2 else ""
                obj.ОсновнойТелефон = phone
                obj.ВидКонтрагента = self.c.Перечисления.ВидыКонтрагентов.Покупатель
                obj.ФормаСобственности = self.c.Перечисления.ФормыСобственности.ЧастноеЛицо
                obj.СогласиеНаОбработкуПерсональныхДанных = self.c.Перечисления.ВариантыОтветов.Спрашивать
                self._write(obj, "клиент")
                self.c.ЗафиксироватьТранзакцию()
            except Exception:
                self.c.ОтменитьТранзакцию()
                raise
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка добавления клиента: %s", e)
            raise OneCError("Ошибка связи с 1С при добавлении клиента.")
        logger.info("Добавлен клиент: %s", full_name)
        return {"id": self._ref_id(obj.Ссылка), "name": obj.Наименование, "phone": phone, "created": True}

    def add_car(self, client_id: str, brand: str, model: str, gos_number: str,
                vin: str = "", year: int = 0) -> Dict:
        """Создаёт автомобиль и привязывает к клиенту (хозяин + госномер в регистре)."""
        self._ensure()
        duplicate = self.find_car_by_gos_number(gos_number)
        if duplicate:
            return {"created": False, **duplicate}

        model_name = f"{brand} {model}".strip()
        try:
            client = self._ref("Контрагенты", client_id)
            self.c.НачатьТранзакцию()
            try:
                model_ref = self._find_or_create_model(model_name)
                car = self.c.Справочники.Автомобили.СоздатьЭлемент()
                car.Наименование = self._trim("Автомобили", f"{model_name} {gos_number}")
                car.НаименованиеПолное = f"{model_name} {gos_number}"
                car.Модель = model_ref
                car.VIN = vin
                if year:
                    car.ГодВыпуска = datetime.datetime(year, 7, 1, 12)
                car.ВалютаУчета = self._accounting_currency()
                car.ВключатьВПрайсЛист = self.c.Перечисления.ВидВключенияВПрайсЛист.ПоУмолчанию
                self._write(car, "автомобиль")
                self._set_info(car.Ссылка, "Хозяин", client)
                self._set_info(car.Ссылка, "ГосНомер", gos_number)
                self.c.ЗафиксироватьТранзакцию()
            except Exception:
                self.c.ОтменитьТранзакцию()
                raise
        except pywintypes.com_error as e:
            self.c = None
            logger.error("Ошибка добавления автомобиля: %s", e)
            raise OneCError("Ошибка связи с 1С при добавлении автомобиля.")
        logger.info("Добавлен автомобиль: %s %s", model_name, gos_number)
        return {"created": True, "name": car.Наименование}

    def _find_or_create_model(self, model_name: str):
        sel = self._query(
            "ВЫБРАТЬ ПЕРВЫЕ 1 М.Ссылка КАК Ссылка ИЗ Справочник.Модели КАК М "
            "ГДЕ М.Наименование = &Н И НЕ М.ПометкаУдаления", {"Н": self._trim("Модели", model_name)})
        if sel.Следующий():
            return sel.Ссылка
        obj = self.c.Справочники.Модели.СоздатьЭлемент()
        obj.Наименование = self._trim("Модели", model_name)
        obj.НаименованиеПолное = model_name
        obj.ВалютаУчета = self._accounting_currency()
        self._write(obj, "модель автомобиля")
        return obj.Ссылка

    def _set_info(self, car_ref, kind: str, value):
        rec = self.c.РегистрыСведений.Автомобили.СоздатьМенеджерЗаписи()
        rec.Период = datetime.datetime.now()
        rec.Автомобиль = car_ref
        rec.ВидЗначения = self._info_kind(kind)
        rec.Значение = value
        self._write(rec, f"данные автомобиля ({kind})")
