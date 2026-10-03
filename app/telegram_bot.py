from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import os
import re
import threading
import time
from typing import Callable

import pandas as pd

from db import (
    database_enabled,
    get_db_connection,
    get_service_state,
    log_audit_event,
    set_service_state,
)
from salon_data_store import load_archive_data, load_salons
from sku_catalog_store import (
    apply_sku_aliases,
    apply_sku_attribute_overrides,
    load_sku_aliases,
    load_sku_attribute_overrides,
)
from supplier_rules_store import load_supplier_keyword_rules
from telegram_reports import (
    TARGETED_TELEGRAM_REPORT_LABELS,
    answer_telegram_callback,
    configure_telegram_commands,
    get_telegram_updates,
    send_targeted_telegram_report,
    send_telegram_message,
)
from telegram_settings_store import load_telegram_settings


UPDATE_STATE_PREFIX = "telegram-bot-update-offset"
ADVISORY_LOCK_NAME = "artdb-telegram-menu-bot"
_START_LOCK = threading.Lock()
_BOT_THREAD: threading.Thread | None = None
_DATE_PATTERN = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")

MAIN_MENU_MARKUP: dict[str, object] = {
    "keyboard": [
        [{"text": "Сводка"}, {"text": "Категории"}],
        [{"text": "Портфель SKU"}, {"text": "Карточка SKU"}],
        [{"text": "Меню"}, {"text": "Помощь"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

@dataclass
class ChatState:
    report_kind: str = ""
    category: str | None = None
    product_key: str | None = None
    awaiting: str = ""
    sku_options: list[tuple[str, str]] = field(default_factory=list)
    category_options: list[str] = field(default_factory=list)
    requested_period: tuple[date, date] | None = None


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name, "").strip().casefold()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _parse_date(value: str) -> date:
    normalized = value.strip()
    date_format = "%d.%m.%Y" if "." in normalized else "%Y-%m-%d"
    return pd.to_datetime(normalized, format=date_format, errors="raise").date()


def parse_date_range(text: str) -> tuple[date, date] | None:
    matches = _DATE_PATTERN.findall(str(text or ""))
    if not matches:
        return None
    if len(matches) != 2:
        raise ValueError("Укажите две даты: начало и конец периода.")
    start_date, end_date = (_parse_date(value) for value in matches)
    if start_date > end_date:
        raise ValueError("Дата начала не может быть позже даты окончания.")
    return start_date, end_date


def resolve_report_period(data: pd.DataFrame, period_key: str) -> tuple[date, date]:
    if data.empty or "date" not in data.columns:
        raise ValueError("В архиве пока нет данных о продажах.")
    dates = pd.to_datetime(data["date"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("В архиве нет корректных дат продаж.")

    min_date = dates.min().date()
    max_date = dates.max().date()
    if period_key == "7":
        return max(min_date, max_date - timedelta(days=6)), max_date
    if period_key == "30":
        return max(min_date, max_date - timedelta(days=29)), max_date
    if period_key == "month":
        return max(min_date, max_date.replace(day=1)), max_date
    if period_key == "previous":
        current_month_start = max_date.replace(day=1)
        previous_end = current_month_start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        return previous_start, previous_end
    if period_key == "all":
        return min_date, max_date
    raise ValueError("Неизвестный период отчёта.")


def _supplier_rules() -> tuple[tuple[str, str], ...]:
    rules = load_supplier_keyword_rules()
    if rules.empty or not {"supplier", "keyword"}.issubset(rules.columns):
        return ()
    if "is_active" in rules.columns:
        rules = rules[rules["is_active"].fillna(True).astype(bool)]
    return tuple(
        (str(row["supplier"]).strip(), str(row["keyword"]).strip())
        for _, row in rules.iterrows()
        if str(row["supplier"]).strip() and str(row["keyword"]).strip()
    )


def load_bot_sales_data() -> pd.DataFrame:
    salons = load_salons()
    archive_result = load_archive_data(
        salons=salons if salons else None,
        supplier_rules=_supplier_rules(),
    )
    data = archive_result.data.copy()
    if data.empty:
        return data
    data = apply_sku_attribute_overrides(data, load_sku_attribute_overrides())
    return apply_sku_aliases(data, load_sku_aliases())


def _short_text(value: object, limit: int = 54) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip() or "Без названия"
    return text if len(text) <= limit else f"{text[:limit - 1].rstrip()}…"


def _clean_catalog_value(value: object) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


class TelegramReportMenu:
    def __init__(self, data_loader: Callable[[], pd.DataFrame] = load_bot_sales_data) -> None:
        self._data_loader = data_loader
        self._states: dict[str, ChatState] = {}
        self._cached_data = pd.DataFrame()
        self._cache_loaded_at = 0.0

    def _state(self, chat_id: str) -> ChatState:
        return self._states.setdefault(chat_id, ChatState())

    def _load_data(self) -> pd.DataFrame:
        cache_seconds = _env_int("TELEGRAM_BOT_CACHE_SECONDS", 180, 0, 1800)
        now = time.monotonic()
        if self._cached_data.empty or now - self._cache_loaded_at >= cache_seconds:
            self._cached_data = self._data_loader()
            self._cache_loaded_at = now
        return self._cached_data

    def handle_update(self, update: dict[str, object], *, allowed_chat_id: str) -> None:
        chat_id, sender_id = self._extract_identity(update)
        if not chat_id:
            return
        if chat_id != str(allowed_chat_id).strip():
            callback = update.get("callback_query")
            if isinstance(callback, dict) and callback.get("id"):
                answer_telegram_callback(
                    str(callback["id"]),
                    text="Доступ к отчётам запрещён.",
                    show_alert=True,
                )
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback(chat_id, sender_id, callback)
            return

        message = update.get("message")
        if isinstance(message, dict):
            self._handle_message(chat_id, sender_id, str(message.get("text") or "").strip())

    @staticmethod
    def _extract_identity(update: dict[str, object]) -> tuple[str, str]:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            message = callback.get("message")
            chat = message.get("chat") if isinstance(message, dict) else None
            sender = callback.get("from")
        else:
            message = update.get("message")
            chat = message.get("chat") if isinstance(message, dict) else None
            sender = message.get("from") if isinstance(message, dict) else None
        chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
        sender_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
        return chat_id, sender_id

    def _send_main_menu(self, chat_id: str) -> None:
        self._states[chat_id] = ChatState()
        send_telegram_message(
            "<b>ArtDB: отчёты</b>\nВыберите нужный отчёт кнопкой ниже.",
            chat_id=chat_id,
            reply_markup=MAIN_MENU_MARKUP,
        )

    def _send_help(self, chat_id: str) -> None:
        send_telegram_message(
            "\n".join(
                [
                    "<b>Как запросить отчёт</b>",
                    "Используйте кнопки меню или команды:",
                    "• /summary - управленческая сводка",
                    "• /categories - категории",
                    "• /portfolio - портфель SKU",
                    "• /sku АРТИКУЛ - карточка товара",
                    "",
                    "Для точного периода добавьте две даты:",
                    "/summary 01.09.2026 30.09.2026",
                    "/sku A-123 01.09.2026 30.09.2026",
                ]
            ),
            chat_id=chat_id,
            reply_markup=MAIN_MENU_MARKUP,
        )

    def _handle_message(self, chat_id: str, sender_id: str, text: str) -> None:
        if not text:
            return
        state = self._state(chat_id)
        command_text, _, arguments = text.partition(" ")
        command = command_text.split("@", 1)[0].casefold()

        if command in {"/start", "/menu"} or text == "Меню":
            self._send_main_menu(chat_id)
            return
        if command == "/help" or text == "Помощь":
            self._send_help(chat_id)
            return

        button_kinds = {
            "Сводка": "summary",
            "Категории": "categories",
            "Портфель SKU": "portfolio",
            "Карточка SKU": "sku",
        }
        if text in button_kinds:
            self._choose_report_kind(chat_id, button_kinds[text])
            return

        command_kinds = {
            "/summary": "summary",
            "/categories": "categories",
            "/portfolio": "portfolio",
        }
        if command in command_kinds:
            report_kind = command_kinds[command]
            explicit_period = parse_date_range(arguments)
            state.report_kind = report_kind
            state.category = None
            state.product_key = None
            if explicit_period:
                self._run_report(chat_id, sender_id, *explicit_period)
            else:
                self._show_period_menu(chat_id)
            return

        if command == "/sku":
            self._handle_sku_command(chat_id, sender_id, arguments)
            return

        if state.awaiting == "sku_query":
            requested_period = state.requested_period
            selected = self._find_sku(
                chat_id,
                text,
                show_period=requested_period is None,
            )
            if selected and requested_period:
                state.requested_period = None
                self._run_report(chat_id, sender_id, *requested_period)
            return
        if state.awaiting == "custom_dates":
            try:
                explicit_period = parse_date_range(text)
                if explicit_period is None:
                    raise ValueError("Укажите период двумя датами.")
                state.awaiting = ""
                self._run_report(chat_id, sender_id, *explicit_period)
            except ValueError as error:
                send_telegram_message(
                    f"{error}\nПример: 01.09.2026 30.09.2026",
                    chat_id=chat_id,
                )
            return

        send_telegram_message(
            "Не удалось распознать запрос. Нажмите кнопку меню или используйте /help.",
            chat_id=chat_id,
            reply_markup=MAIN_MENU_MARKUP,
        )

    def _choose_report_kind(self, chat_id: str, report_kind: str) -> None:
        state = self._state(chat_id)
        state.report_kind = report_kind
        state.category = None
        state.product_key = None
        state.awaiting = ""
        state.sku_options = []
        state.category_options = []
        state.requested_period = None
        if report_kind == "sku":
            state.awaiting = "sku_query"
            send_telegram_message(
                "<b>Карточка SKU</b>\nОтправьте артикул или часть названия товара одним сообщением.",
                chat_id=chat_id,
            )
            return
        self._show_period_menu(chat_id)

    def _show_period_menu(self, chat_id: str) -> None:
        state = self._state(chat_id)
        report_label = TARGETED_TELEGRAM_REPORT_LABELS.get(state.report_kind, "Отчёт")
        keyboard: list[list[dict[str, str]]] = [
            [
                {"text": "7 дней", "callback_data": "period:7"},
                {"text": "30 дней", "callback_data": "period:30"},
            ],
            [
                {"text": "Последний месяц", "callback_data": "period:month"},
                {"text": "Предыдущий месяц", "callback_data": "period:previous"},
            ],
            [
                {"text": "Весь период", "callback_data": "period:all"},
                {"text": "Свои даты", "callback_data": "period:custom"},
            ],
        ]
        if state.report_kind == "portfolio":
            keyboard.append([{"text": "Выбрать категорию", "callback_data": "scope:category"}])
        scope_text = f"\nКатегория: {state.category}" if state.category else ""
        if state.product_key:
            scope_text += f"\nSKU: {state.product_key}"
        send_telegram_message(
            f"<b>{report_label}</b>{scope_text}\nВыберите период:",
            chat_id=chat_id,
            reply_markup={"inline_keyboard": keyboard},
        )

    def _handle_callback(self, chat_id: str, sender_id: str, callback: dict[str, object]) -> None:
        callback_id = str(callback.get("id") or "")
        callback_data = str(callback.get("data") or "")
        if callback_id:
            answer_telegram_callback(callback_id, text="Принято")
        state = self._state(chat_id)

        if callback_data.startswith("period:"):
            period_key = callback_data.partition(":")[2]
            if not state.report_kind:
                self._send_main_menu(chat_id)
                return
            if state.report_kind == "sku" and not state.product_key:
                state.awaiting = "sku_query"
                send_telegram_message("Сначала отправьте артикул или название SKU.", chat_id=chat_id)
                return
            if period_key == "custom":
                state.awaiting = "custom_dates"
                send_telegram_message(
                    "Отправьте начало и конец периода одним сообщением.\nПример: 01.09.2026 30.09.2026",
                    chat_id=chat_id,
                )
                return
            data = self._load_data()
            start_date, end_date = resolve_report_period(data, period_key)
            self._run_report(chat_id, sender_id, start_date, end_date, data=data)
            return

        if callback_data == "scope:category":
            self._show_category_options(chat_id)
            return

        if callback_data.startswith("category:"):
            option_text = callback_data.partition(":")[2]
            if option_text == "all":
                state.category = None
            else:
                option_index = int(option_text)
                if option_index >= len(state.category_options):
                    raise ValueError("Список категорий устарел. Откройте меню ещё раз.")
                state.category = state.category_options[option_index]
            self._show_period_menu(chat_id)
            return

        if callback_data.startswith("sku:"):
            option_index = int(callback_data.partition(":")[2])
            if option_index >= len(state.sku_options):
                raise ValueError("Список SKU устарел. Выполните поиск ещё раз.")
            state.product_key = state.sku_options[option_index][0]
            state.awaiting = ""
            if state.requested_period:
                requested_period = state.requested_period
                state.requested_period = None
                self._run_report(chat_id, sender_id, *requested_period)
            else:
                self._show_period_menu(chat_id)
            return

        self._send_main_menu(chat_id)

    def _show_category_options(self, chat_id: str) -> None:
        data = self._load_data()
        if "category" not in data.columns:
            raise ValueError("В данных нет колонки с категориями.")
        categories = sorted(
            {
                str(value).strip()
                for value in data["category"].dropna().astype(str)
                if str(value).strip()
            },
            key=str.casefold,
        )
        if not categories:
            raise ValueError("В архиве не найдены категории.")
        state = self._state(chat_id)
        state.category_options = categories[:40]
        keyboard = [[{"text": "Все категории", "callback_data": "category:all"}]]
        keyboard.extend(
            [{"text": _short_text(category), "callback_data": f"category:{index}"}]
            for index, category in enumerate(state.category_options)
        )
        send_telegram_message(
            "<b>Фильтр портфеля</b>\nВыберите категорию:",
            chat_id=chat_id,
            reply_markup={"inline_keyboard": keyboard},
        )

    def _handle_sku_command(self, chat_id: str, sender_id: str, arguments: str) -> None:
        state = self._state(chat_id)
        state.report_kind = "sku"
        state.category = None
        explicit_period = parse_date_range(arguments)
        state.requested_period = explicit_period
        query = _DATE_PATTERN.sub(" ", arguments)
        query = re.sub(r"\s+", " ", query).strip()
        if not query:
            state.awaiting = "sku_query"
            send_telegram_message(
                "Отправьте артикул или часть названия после команды /sku.",
                chat_id=chat_id,
            )
            return
        selected = self._find_sku(chat_id, query, show_period=explicit_period is None)
        if selected and explicit_period:
            state.requested_period = None
            self._run_report(chat_id, sender_id, *explicit_period)

    def _find_sku(self, chat_id: str, query: str, *, show_period: bool = True) -> bool:
        data = self._load_data()
        identity_column = "product_key" if "product_key" in data.columns else "product"
        if data.empty or identity_column not in data.columns:
            raise ValueError("В архиве пока нет SKU для поиска.")

        catalog_columns = [
            column for column in [identity_column, "item_code", "product"] if column in data.columns
        ]
        catalog = data[catalog_columns].drop_duplicates(subset=[identity_column]).copy()
        normalized_query = query.strip().casefold()
        matches: list[tuple[int, str, str]] = []
        for row in catalog.to_dict(orient="records"):
            product_key = _clean_catalog_value(row.get(identity_column))
            item_code = _clean_catalog_value(row.get("item_code"))
            product = _clean_catalog_value(row.get("product"))
            if not product_key:
                continue
            searchable = [product_key.casefold(), item_code.casefold(), product.casefold()]
            if normalized_query not in " ".join(searchable):
                continue
            if normalized_query == product_key.casefold():
                rank = 0
            elif normalized_query == item_code.casefold():
                rank = 1
            elif normalized_query == product.casefold():
                rank = 2
            elif any(value.startswith(normalized_query) for value in searchable if value):
                rank = 3
            else:
                rank = 4
            label_parts = [value for value in (item_code, product) if value]
            matches.append((rank, product_key, " · ".join(label_parts) or product_key))

        matches.sort(key=lambda item: (item[0], item[2].casefold()))
        options = [(product_key, label) for _, product_key, label in matches[:8]]
        state = self._state(chat_id)
        state.report_kind = "sku"
        state.awaiting = ""
        state.sku_options = options
        if not options:
            state.awaiting = "sku_query"
            send_telegram_message(
                "SKU не найден. Проверьте артикул или отправьте часть названия ещё раз.",
                chat_id=chat_id,
            )
            return False
        if len(options) == 1:
            state.product_key = options[0][0]
            if show_period:
                self._show_period_menu(chat_id)
            return True

        keyboard = [
            [{"text": _short_text(label), "callback_data": f"sku:{index}"}]
            for index, (_, label) in enumerate(options)
        ]
        send_telegram_message(
            "<b>Найдено несколько SKU</b>\nВыберите нужную позицию:",
            chat_id=chat_id,
            reply_markup={"inline_keyboard": keyboard},
        )
        return False

    def _run_report(
        self,
        chat_id: str,
        sender_id: str,
        start_date: date,
        end_date: date,
        *,
        data: pd.DataFrame | None = None,
    ) -> None:
        state = self._state(chat_id)
        report_data = self._load_data() if data is None else data
        try:
            send_telegram_message(
                f"Формирую отчёт за {start_date:%d.%m.%Y} - {end_date:%d.%m.%Y}...",
                chat_id=chat_id,
            )
            sent_files = send_targeted_telegram_report(
                report_data,
                report_kind=state.report_kind,
                date_from=start_date,
                date_to=end_date,
                category=state.category,
                product_key=state.product_key,
                with_file=True,
                chat_id=chat_id,
            )
            try:
                log_audit_event(
                    user_id=f"telegram:{sender_id or chat_id}",
                    action="telegram.bot_report_request",
                    details={
                        "chat_id": chat_id,
                        "report_kind": state.report_kind,
                        "date_from": start_date.isoformat(),
                        "date_to": end_date.isoformat(),
                        "category": state.category or "",
                        "product_key": state.product_key or "",
                        "sent_files": sent_files,
                    },
                )
            except Exception as error:
                print(f"Telegram bot audit error: {error}", flush=True)
        except Exception as error:
            send_telegram_message(
                f"Не удалось сформировать отчёт: {error}",
                chat_id=chat_id,
            )


def _update_state_name(bot_token: str) -> str:
    token_fingerprint = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()[:16]
    return f"{UPDATE_STATE_PREFIX}:{token_fingerprint}"


def _load_update_offset(state_name: str) -> int:
    if not database_enabled():
        return 0
    raw_value = get_service_state(state_name)
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 0


def _poll_updates(menu: TelegramReportMenu, lock_connection: object | None = None) -> None:
    offset = 0
    offset_state_name = ""
    configured_token = ""
    while True:
        try:
            settings = load_telegram_settings()
            if not settings.configured:
                if lock_connection is not None:
                    with lock_connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                time.sleep(15)
                continue
            if settings.bot_token != configured_token:
                configure_telegram_commands()
                configured_token = settings.bot_token
                offset_state_name = _update_state_name(configured_token)
                offset = _load_update_offset(offset_state_name)

            timeout = _env_int("TELEGRAM_BOT_POLL_TIMEOUT_SECONDS", 20, 5, 25)
            updates = get_telegram_updates(offset=offset, timeout=timeout)
            if lock_connection is not None:
                with lock_connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            for update in updates:
                update_id = int(update.get("update_id", -1))
                try:
                    menu.handle_update(update, allowed_chat_id=settings.chat_id)
                except Exception as error:
                    print(f"Telegram bot update error: {error}", flush=True)
                    chat_id, _ = menu._extract_identity(update)
                    if chat_id == settings.chat_id:
                        try:
                            send_telegram_message(
                                f"Не удалось обработать запрос: {error}",
                                chat_id=chat_id,
                            )
                        except Exception:
                            pass
                finally:
                    if update_id >= 0:
                        offset = max(offset, update_id + 1)
                        set_service_state(offset_state_name, str(offset))
        except Exception as error:
            print(f"Telegram bot polling error: {error}", flush=True)
            if lock_connection is not None:
                raise
            time.sleep(15)


def _bot_loop() -> None:
    menu = TelegramReportMenu()
    while True:
        if not database_enabled():
            _poll_updates(menu)
            return
        try:
            with get_db_connection(autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                        (ADVISORY_LOCK_NAME,),
                    )
                    row = cursor.fetchone()
                if row and bool(row.get("acquired")):
                    _poll_updates(menu, connection)
                else:
                    time.sleep(30)
        except Exception as error:
            print(f"Telegram bot lock error: {error}", flush=True)
            time.sleep(30)


def start_telegram_bot() -> bool:
    global _BOT_THREAD
    if not _env_flag("TELEGRAM_BOT_MENU_ENABLED", default=True):
        return False

    with _START_LOCK:
        if _BOT_THREAD is not None and _BOT_THREAD.is_alive():
            return True
        _BOT_THREAD = threading.Thread(
            target=_bot_loop,
            name="artdb-telegram-menu-bot",
            daemon=True,
        )
        _BOT_THREAD.start()
    return True
