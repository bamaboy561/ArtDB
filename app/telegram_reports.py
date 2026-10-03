from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
import json
import os
import re
import secrets
from typing import Iterable
from urllib import error as urlerror, parse, request
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from procurement_analytics import (
    build_procurement_forecast,
    build_procurement_overview,
    build_procurement_stock_risk_frames,
    build_procurement_supplier_summary,
)
from procurement_order_store import (
    build_open_order_summary,
    load_procurement_order_items,
    load_procurement_orders,
)
from procurement_store import load_procurement_items
from salon_data_store import load_archive_data, load_manifest, load_salons
from sales_analytics import (
    build_abc_analysis,
    build_monthly_summary,
    build_overview_metrics,
    build_product_summary,
    to_csv_bytes,
)
from telegram_settings_store import load_telegram_settings


@dataclass(frozen=True)
class TelegramReportFile:
    filename: str
    content: bytes
    caption: str = ""
    content_type: str = "text/csv"


TARGETED_TELEGRAM_REPORT_LABELS = {
    "summary": "Управленческая сводка",
    "categories": "Отчёт по категориям",
    "portfolio": "Портфель SKU",
    "sku": "Карточка SKU / артикула",
}


PROCUREMENT_ORDER_COLUMNS = [
    "supplier",
    "product",
    "category",
    "priority",
    "stock_status",
    "abc_class",
    "xyz_class",
    "forecast_qty",
    "stock_on_hand",
    "stock_in_transit",
    "available_stock_qty",
    "stock_coverage_days",
    "net_requirement_qty",
    "recommended_order_qty",
    "lead_time_days",
    "last_sale_date",
    "days_since_last_sale",
    "notes",
]

PROCUREMENT_ORDER_RENAME_MAP = {
    "supplier": "Поставщик",
    "product": "SKU / Товар",
    "category": "Категория",
    "priority": "Приоритет",
    "stock_status": "Статус остатка",
    "abc_class": "ABC",
    "xyz_class": "XYZ",
    "forecast_qty": "Прогноз спроса, шт",
    "stock_on_hand": "Остаток",
    "stock_in_transit": "В пути",
    "available_stock_qty": "Доступно",
    "stock_coverage_days": "Покрытие, дней",
    "net_requirement_qty": "Чистая потребность, шт",
    "recommended_order_qty": "К заказу, шт",
    "lead_time_days": "Срок поставки, дней",
    "last_sale_date": "Последняя продажа",
    "days_since_last_sale": "Дней без продаж",
    "notes": "Примечание",
}


def get_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TIMEZONE", os.getenv("TZ", "Asia/Omsk")))


def env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip().replace(",", ".")
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def telegram_is_configured() -> bool:
    token, chat_id = _get_telegram_credentials()
    return bool(token and chat_id)


def _get_telegram_credentials() -> tuple[str, str]:
    settings = load_telegram_settings()
    return settings.bot_token, settings.chat_id


def _telegram_api_request(
    method: str,
    data: bytes,
    headers: dict[str, str] | None = None,
    *,
    require_chat: bool = True,
    bot_token: str | None = None,
) -> dict[str, object]:
    configured_token, chat_id = _get_telegram_credentials()
    token = str(bot_token or "").strip() or configured_token
    if not token:
        raise RuntimeError("Не задан токен Telegram-бота.")
    if require_chat and not chat_id:
        raise RuntimeError("Не выбран Telegram-чат для отчётов.")

    telegram_url = f"https://api.telegram.org/bot{token}/{method}"
    api_request = request.Request(telegram_url, data=data, headers=headers or {})
    try:
        with request.urlopen(api_request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as error:
        response_text = error.read().decode("utf-8", errors="replace")
        try:
            response_body = json.loads(response_text)
            description = str(response_body.get("description", "")).strip()
        except json.JSONDecodeError:
            description = response_text.strip()
        raise RuntimeError(description or f"Telegram API вернул HTTP {error.code}.") from error
    except urlerror.URLError as error:
        raise RuntimeError(f"Не удалось подключиться к Telegram: {error.reason}") from error
    if not body.get("ok"):
        raise RuntimeError(str(body.get("description", "Ошибка Telegram API.")))
    return body


def get_telegram_bot_profile(bot_token: str | None = None) -> dict[str, object]:
    response = _telegram_api_request(
        "getMe",
        b"",
        require_chat=False,
        bot_token=bot_token,
    )
    result = response.get("result", {})
    return result if isinstance(result, dict) else {}


def discover_telegram_chats() -> list[dict[str, str]]:
    payload = parse.urlencode({"limit": 100, "timeout": 0}).encode("utf-8")
    response = _telegram_api_request(
        "getUpdates",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        require_chat=False,
    )
    updates = response.get("result", [])
    if not isinstance(updates, list):
        return []

    chats: dict[str, dict[str, str]] = {}
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get("message") or update.get("channel_post") or update.get("my_chat_member")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue
        chat_id = str(chat.get("id", "")).strip()
        if not chat_id:
            continue
        title = str(chat.get("title") or "").strip()
        if not title:
            title = " ".join(
                value
                for value in (
                    str(chat.get("first_name") or "").strip(),
                    str(chat.get("last_name") or "").strip(),
                )
                if value
            )
        username = str(chat.get("username") or "").strip()
        label = title or (f"@{username}" if username else f"Чат {chat_id}")
        chats[chat_id] = {
            "chat_id": chat_id,
            "label": label,
            "type": str(chat.get("type") or "").strip(),
        }
    return list(chats.values())


def send_telegram_message(
    text: str,
    *,
    chat_id: str | None = None,
    reply_markup: dict[str, object] | None = None,
) -> None:
    _, configured_chat_id = _get_telegram_credentials()
    target_chat_id = str(chat_id or "").strip() or configured_chat_id
    fields = {
        "chat_id": target_chat_id,
        "text": _normalize_telegram_html(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    payload = parse.urlencode(fields).encode("utf-8")
    _telegram_api_request(
        "sendMessage",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def get_telegram_updates(*, offset: int = 0, timeout: int = 20) -> list[dict[str, object]]:
    payload = parse.urlencode(
        {
            "offset": max(0, int(offset)),
            "limit": 25,
            "timeout": max(0, min(25, int(timeout))),
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
    ).encode("utf-8")
    response = _telegram_api_request(
        "getUpdates",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        require_chat=False,
    )
    updates = response.get("result", [])
    if not isinstance(updates, list):
        return []
    return [item for item in updates if isinstance(item, dict)]


def answer_telegram_callback(
    callback_query_id: str,
    *,
    text: str = "",
    show_alert: bool = False,
) -> None:
    fields = {
        "callback_query_id": str(callback_query_id),
        "show_alert": "true" if show_alert else "false",
    }
    if text:
        fields["text"] = str(text)[:200]
    payload = parse.urlencode(fields).encode("utf-8")
    _telegram_api_request(
        "answerCallbackQuery",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        require_chat=False,
    )


def configure_telegram_commands() -> None:
    commands = [
        {"command": "menu", "description": "Открыть меню отчётов"},
        {"command": "summary", "description": "Управленческая сводка"},
        {"command": "categories", "description": "Отчёт по категориям"},
        {"command": "portfolio", "description": "Портфель SKU"},
        {"command": "sku", "description": "Карточка SKU или артикула"},
        {"command": "help", "description": "Помощь по командам"},
    ]
    payload = parse.urlencode(
        {"commands": json.dumps(commands, ensure_ascii=False)}
    ).encode("utf-8")
    _telegram_api_request(
        "setMyCommands",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        require_chat=False,
    )


def _normalize_telegram_html(value: object) -> str:
    """Escape arbitrary text while preserving the HTML tags used by ArtDB reports."""
    token_pattern = re.compile(
        r"(</?b>|&(?:lt|gt|amp|quot|apos|#\d+|#x[0-9a-fA-F]+);)",
        flags=re.IGNORECASE,
    )
    safe_parts: list[str] = []
    for part in token_pattern.split(str(value)):
        if not part:
            continue
        if token_pattern.fullmatch(part):
            safe_parts.append(part)
        else:
            safe_parts.append(escape(part, quote=False))
    return "".join(safe_parts)


def _encode_multipart_formdata(
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = f"----ArtDBTelegram{secrets.token_hex(16)}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for field_name, filename, content, content_type in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def send_telegram_document(
    report_file: TelegramReportFile,
    *,
    chat_id: str | None = None,
) -> None:
    _, configured_chat_id = _get_telegram_credentials()
    target_chat_id = str(chat_id or "").strip() or configured_chat_id
    fields = {
        "chat_id": target_chat_id,
        "caption": _normalize_telegram_html(report_file.caption)[:1024],
        "parse_mode": "HTML",
    }
    payload, boundary = _encode_multipart_formdata(
        fields,
        [
            (
                "document",
                report_file.filename,
                report_file.content,
                report_file.content_type,
            )
        ],
    )
    _telegram_api_request(
        "sendDocument",
        payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def format_money_plain(value: object) -> str:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "н/д"
    return f"{float(numeric):,.0f} сом".replace(",", " ")


def format_number_plain(value: object) -> str:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "н/д"
    return f"{float(numeric):,.0f}".replace(",", " ")


def format_percent_plain(value: object) -> str:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "н/д"
    return f"{float(numeric):.1f}%"


def _safe_excel_sheet_name(value: object, used_names: set[str]) -> str:
    raw_name = str(value or "Лист").strip() or "Лист"
    safe_name = re.sub(r"[\[\]\:\*\?\/\\]", " ", raw_name)
    safe_name = re.sub(r"\s+", " ", safe_name).strip()[:31] or "Лист"
    candidate = safe_name
    suffix = 2
    while candidate.casefold() in used_names:
        suffix_text = f" {suffix}"
        candidate = f"{safe_name[:31 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used_names.add(candidate.casefold())
    return candidate


def _prepare_excel_frame(frame: pd.DataFrame, columns: list[str], rename_map: dict[str, str]) -> pd.DataFrame:
    export_columns = [column for column in columns if column in frame.columns]
    export_frame = frame[export_columns].copy() if export_columns else pd.DataFrame()
    for column in export_frame.columns:
        if pd.api.types.is_datetime64_any_dtype(export_frame[column]):
            export_frame[column] = pd.to_datetime(export_frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    return export_frame.rename(columns=rename_map)


def _export_excel_workbook(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = BytesIO()
    used_names: set[str] = set()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for raw_sheet_name, frame in sheets.items():
            sheet_name = _safe_excel_sheet_name(raw_sheet_name, used_names)
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            worksheet.sheet_view.showGridLines = False
            worksheet.row_dimensions[1].height = 26
            for header_cell in worksheet[1]:
                header_cell.fill = PatternFill("solid", fgColor="003461")
                header_cell.font = Font(color="FFFFFF", bold=True)
                header_cell.alignment = Alignment(vertical="center", wrap_text=True)
            for column_cells in worksheet.columns:
                header = str(column_cells[0].value or "")
                max_length = min(
                    max([len(str(cell.value or "")) for cell in column_cells[:80]] + [len(header), 8]),
                    42,
                )
                worksheet.column_dimensions[column_cells[0].column_letter].width = max_length + 2
                header_lower = header.casefold()
                for cell in column_cells[1:]:
                    cell.alignment = Alignment(vertical="top", wrap_text=len(str(cell.value or "")) > 36)
                    if isinstance(cell.value, (int, float)):
                        if "%" in header:
                            cell.number_format = "0.0"
                        elif any(
                            marker in header_lower
                            for marker in ("выруч", "себесто", "прибыл", "марж", "сумм")
                        ):
                            cell.number_format = '#,##0.00'
                        else:
                            cell.number_format = '#,##0.##'
    return buffer.getvalue()


def build_upload_status(today: datetime.date, salons: Iterable[str], manifest: pd.DataFrame) -> tuple[list[str], list[str]]:
    if manifest.empty or "report_date" not in manifest.columns:
        return [], sorted({salon for salon in salons if salon})

    manifest_view = manifest.copy()
    manifest_view["report_date"] = pd.to_datetime(manifest_view["report_date"], errors="coerce").dt.date
    today_uploads = manifest_view[manifest_view["report_date"] == today]
    uploaded_salons = sorted({str(item).strip() for item in today_uploads["salon"].dropna().astype(str) if str(item).strip()})
    missing_salons = sorted({salon for salon in salons if salon and salon not in uploaded_salons})
    return uploaded_salons, missing_salons


def build_daily_summary() -> str:
    timezone = get_timezone()
    now = datetime.now(timezone)
    today = now.date()

    salons = load_salons()
    manifest = load_manifest()
    uploaded_salons, missing_salons = build_upload_status(today, salons, manifest)

    app_url = os.getenv("APP_PUBLIC_URL", os.getenv("DOMAIN_NAME", "")).strip()
    if app_url and not app_url.startswith(("http://", "https://")):
        app_url = f"https://{app_url}"

    summary_lines = [
        "<b>ArtDB: ежедневная сводка</b>",
        f"Дата: {today.strftime('%d.%m.%Y')}",
        f"Салонов в системе: {len(salons)}",
        f"С загрузкой за сегодня: {len(uploaded_salons)}",
    ]
    if app_url:
        summary_lines.append(f"Сайт: {escape(app_url)}")

    if missing_salons:
        summary_lines.append("Без загрузки сегодня:")
        summary_lines.extend(f"• {escape(salon)}" for salon in missing_salons)
    else:
        summary_lines.append("Все салоны загрузили данные за сегодня.")

    archive_result = load_archive_data(salons=salons if salons else None)
    procurement_forecast = pd.DataFrame()
    if not archive_result.data.empty:
        monthly_summary = build_monthly_summary(archive_result.data)
        overview = build_overview_metrics(archive_result.data)
        product_summary = build_product_summary(archive_result.data)
        latest_month = monthly_summary.iloc[-1] if not monthly_summary.empty else {}
        risk_count = int((product_summary["margin_pct"].fillna(9999) < 15).sum()) if "margin_pct" in product_summary.columns else 0
        summary_lines.extend(
            [
                "",
                f"Последний месяц: {escape(str(latest_month.get('month_label', 'н/д')))}",
                f"Выручка: {format_money_plain(overview.get('total_revenue'))}",
                f"Маржа: {format_money_plain(overview.get('total_margin'))}",
                f"Маржа %: {format_percent_plain(overview.get('margin_pct'))}",
                f"Количество: {format_number_plain(overview.get('total_quantity'))}",
                f"Риск по марже (&lt;15%): {risk_count}",
            ]
        )

        procurement_forecast = build_default_procurement_forecast(archive_result.data)
        if not procurement_forecast.empty:
            procurement_overview = build_procurement_overview(procurement_forecast)
            stock_frames = build_procurement_stock_risk_frames(
                procurement_forecast,
                total_window_days=51,
            )
            summary_lines.extend(
                [
                    "",
                    "<b>Закупки и остатки</b>",
                    f"SKU к заказу: {format_number_plain(procurement_overview.get('reorder_sku_count'))}",
                    f"Риск дефицита: {format_number_plain(procurement_overview.get('critical_stock_count'))}",
                    f"Рекомендованный заказ: {format_number_plain(procurement_overview.get('recommended_order_qty_total'))}",
                    f"Неликвид с остатком: {format_number_plain(len(stock_frames['dormant']))}",
                ]
            )

    alerts = build_automation_alerts(
        archive_result.data,
        salons=salons,
        manifest=manifest,
        procurement_forecast=procurement_forecast,
    )
    if alerts:
        summary_lines.extend(["", "<b>Что требует внимания</b>"])
        summary_lines.extend(f"• {alert}" for alert in alerts[:6])

    if archive_result.warnings:
        summary_lines.extend(["", "Предупреждения архива:"])
        summary_lines.extend(f"• {escape(warning)}" for warning in archive_result.warnings[:5])

    return "\n".join(summary_lines)


def build_default_procurement_forecast(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()

    monthly_summary = build_monthly_summary(data)
    history_months = min(max(int(monthly_summary["month_label"].nunique()) if not monthly_summary.empty else 6, 3), 6)
    procurement_items = load_procurement_items()
    procurement_orders = load_procurement_orders()
    procurement_order_items = load_procurement_order_items()
    open_procurement_orders = build_open_order_summary(procurement_orders, procurement_order_items)
    return build_procurement_forecast(
        data,
        history_months=history_months,
        coverage_days=30,
        lead_time_days=14,
        safety_days=7,
        min_active_months=2,
        procurement_items=procurement_items,
        inbound_orders=open_procurement_orders,
    )


def build_automation_alerts(
    data: pd.DataFrame,
    *,
    salons: Iterable[str],
    manifest: pd.DataFrame,
    procurement_forecast: pd.DataFrame,
) -> list[str]:
    alerts: list[str] = []
    now = datetime.now(get_timezone())
    today = now.date()
    margin_threshold = env_float("TELEGRAM_MARGIN_RISK_THRESHOLD", 15.0)
    revenue_drop_threshold = env_float("TELEGRAM_REVENUE_DROP_ALERT_PCT", 20.0)

    uploaded_salons, missing_salons = build_upload_status(today, salons, manifest)
    if missing_salons:
        missing_preview = ", ".join(missing_salons[:8])
        if len(missing_salons) > 8:
            missing_preview += f" +{len(missing_salons) - 8}"
        alerts.append(f"Нет загрузки за сегодня: {escape(missing_preview)}.")
    elif uploaded_salons:
        alerts.append("Все активные салоны загрузили данные за сегодня.")

    if data.empty:
        alerts.append("В архиве пока нет данных для автоматического анализа продаж.")
        return alerts

    monthly_summary = build_monthly_summary(data)
    if not monthly_summary.empty and "revenue_change_pct" in monthly_summary.columns:
        latest_month = monthly_summary.iloc[-1]
        revenue_change = pd.to_numeric(latest_month.get("revenue_change_pct"), errors="coerce")
        if pd.notna(revenue_change) and float(revenue_change) <= -abs(revenue_drop_threshold):
            alerts.append(
                f"Выручка за {escape(str(latest_month.get('month_label', 'последний месяц')))} "
                f"снизилась на {format_percent_plain(abs(float(revenue_change)))} к предыдущему месяцу."
            )

    product_summary = build_product_summary(data)
    if not product_summary.empty and "margin_pct" in product_summary.columns:
        low_margin = product_summary[
            pd.to_numeric(product_summary["margin_pct"], errors="coerce") < margin_threshold
        ].copy()
        if not low_margin.empty:
            top_low_margin = low_margin.sort_values("revenue", ascending=False).head(3)
            preview = "; ".join(
                f"{row['group_name']} ({format_percent_plain(row['margin_pct'])})"
                for _, row in top_low_margin.iterrows()
            )
            alerts.append(
                f"Маржа ниже {format_percent_plain(margin_threshold)} у {len(low_margin)} SKU. "
                f"Главные по выручке: {escape(preview)}."
            )

    if not procurement_forecast.empty:
        stock_frames = build_procurement_stock_risk_frames(procurement_forecast, total_window_days=51)
        shortage = stock_frames["shortage"]
        out_of_stock = stock_frames["out_of_stock"]
        overstock = stock_frames["overstock"]
        dormant = stock_frames["dormant"]
        reorder = stock_frames["reorder"]
        if len(out_of_stock) > 0:
            alerts.append(f"Без остатка при наличии спроса: {format_number_plain(len(out_of_stock))} SKU.")
        if len(shortage) > 0:
            top_shortage = shortage.head(3)
            preview = "; ".join(
                f"{row.get('product', '')} - {format_number_plain(row.get('recommended_order_qty'))}"
                for _, row in top_shortage.iterrows()
            )
            alerts.append(
                f"Риск дефицита: {format_number_plain(len(shortage))} SKU. "
                f"Срочно проверить: {escape(preview)}."
            )
        if len(reorder) > 0:
            alerts.append(
                f"К заказу: {format_number_plain(len(reorder))} SKU, "
                f"суммарно {format_number_plain(reorder['recommended_order_qty'].sum())} шт."
            )
        if len(overstock) > 0:
            alerts.append(f"Излишки по остаткам: {format_number_plain(len(overstock))} SKU.")
        if len(dormant) > 0:
            alerts.append(f"Неликвид с остатком без текущего спроса: {format_number_plain(len(dormant))} SKU.")

    if not alerts:
        alerts.append("Критичных автоматических предупреждений нет.")
    return alerts[:8]


def build_risk_alert_message() -> str:
    salons = load_salons()
    manifest = load_manifest()
    archive_result = load_archive_data(salons=salons if salons else None)
    procurement_forecast = build_default_procurement_forecast(archive_result.data)
    alerts = build_automation_alerts(
        archive_result.data,
        salons=salons,
        manifest=manifest,
        procurement_forecast=procurement_forecast,
    )
    today = datetime.now(get_timezone()).strftime("%d.%m.%Y")
    return "\n".join(
        [
            "<b>ArtDB: автоматические предупреждения</b>",
            f"Дата: {today}",
            "",
            *[f"• {alert}" for alert in alerts],
        ]
    )


def build_supplier_order_file(
    procurement_forecast: pd.DataFrame,
    *,
    today_label: str | None = None,
) -> TelegramReportFile | None:
    if procurement_forecast.empty or "recommended_order_qty" not in procurement_forecast.columns:
        return None

    order_frame = procurement_forecast[
        pd.to_numeric(procurement_forecast["recommended_order_qty"], errors="coerce").fillna(0) > 0
    ].copy()
    if order_frame.empty:
        return None

    if "supplier" not in order_frame.columns:
        order_frame["supplier"] = ""
    order_frame["supplier"] = (
        order_frame["supplier"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", "Не назначен")
    )
    for column, default_value in (("priority", ""), ("net_requirement_qty", 0)):
        if column not in order_frame.columns:
            order_frame[column] = default_value
    order_frame = order_frame.sort_values(
        ["supplier", "priority", "recommended_order_qty", "net_requirement_qty"],
        ascending=[True, True, False, False],
        na_position="last",
    )
    supplier_summary = build_procurement_supplier_summary(order_frame)
    today_label = today_label or datetime.now(get_timezone()).strftime("%Y%m%d")

    sheets: dict[str, pd.DataFrame] = {
        "Сводка": _prepare_excel_frame(
            supplier_summary,
            [
                "supplier",
                "sku_count",
                "reorder_sku_count",
                "critical_sku_count",
                "recommended_order_qty",
                "net_requirement_qty",
                "available_stock_qty",
                "forecast_qty",
                "max_lead_time_days",
            ],
            {
                "supplier": "Поставщик",
                "sku_count": "SKU",
                "reorder_sku_count": "SKU к заказу",
                "critical_sku_count": "Критичных SKU",
                "recommended_order_qty": "К заказу, шт",
                "net_requirement_qty": "Чистая потребность, шт",
                "available_stock_qty": "Доступный остаток",
                "forecast_qty": "Прогноз спроса, шт",
                "max_lead_time_days": "Макс. срок поставки, дней",
            },
        ),
        "Заказ общий": _prepare_excel_frame(
            order_frame,
            PROCUREMENT_ORDER_COLUMNS,
            PROCUREMENT_ORDER_RENAME_MAP,
        ),
    }

    supplier_limit = max(0, env_int("TELEGRAM_ORDER_SUPPLIER_SHEETS_LIMIT", 20))
    for supplier in supplier_summary["supplier"].astype(str).head(supplier_limit):
        supplier_frame = order_frame[order_frame["supplier"].astype(str) == supplier]
        if supplier_frame.empty:
            continue
        sheets[str(supplier)] = _prepare_excel_frame(
            supplier_frame,
            PROCUREMENT_ORDER_COLUMNS,
            PROCUREMENT_ORDER_RENAME_MAP,
        )

    return TelegramReportFile(
        filename=f"artdb_supplier_order_{today_label}.xlsx",
        content=_export_excel_workbook(sheets),
        caption="Excel-заказ поставщикам по прогнозу потребности",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def build_supplier_order_report_files() -> list[TelegramReportFile]:
    salons = load_salons()
    archive_result = load_archive_data(salons=salons if salons else None)
    procurement_forecast = build_default_procurement_forecast(archive_result.data)
    order_file = build_supplier_order_file(procurement_forecast)
    return [order_file] if order_file is not None else []


def send_supplier_order_files() -> int:
    sent_files = 0
    for report_file in build_supplier_order_report_files():
        send_telegram_document(report_file)
        sent_files += 1
    return sent_files


def _export_frame(frame: pd.DataFrame, columns: list[str], rename_map: dict[str, str]) -> bytes:
    export_columns = [column for column in columns if column in frame.columns]
    if not export_columns:
        return to_csv_bytes(pd.DataFrame())

    export_frame = frame[export_columns].copy()
    for column in export_frame.columns:
        if pd.api.types.is_datetime64_any_dtype(export_frame[column]):
            export_frame[column] = pd.to_datetime(export_frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    return to_csv_bytes(export_frame.rename(columns=rename_map))


def _filter_targeted_sales_data(
    data: pd.DataFrame,
    *,
    date_from: date,
    date_to: date,
    category: str | None = None,
    product_key: str | None = None,
) -> pd.DataFrame:
    if data.empty or "date" not in data.columns:
        return pd.DataFrame(columns=data.columns)

    start_date = pd.Timestamp(date_from).date()
    end_date = pd.Timestamp(date_to).date()
    if start_date > end_date:
        raise ValueError("Дата начала отчёта не может быть позже даты окончания.")

    filtered = data.copy()
    parsed_dates = pd.to_datetime(filtered["date"], errors="coerce")
    date_values = parsed_dates.dt.date
    filtered = filtered[(date_values >= start_date) & (date_values <= end_date)].copy()
    filtered["date"] = parsed_dates.loc[filtered.index]

    if category:
        if "category" not in filtered.columns:
            return filtered.iloc[0:0].copy()
        filtered = filtered[filtered["category"].fillna("").astype(str).str.strip() == str(category).strip()]

    if product_key:
        identity_column = "product_key" if "product_key" in filtered.columns else "product"
        if identity_column not in filtered.columns:
            return filtered.iloc[0:0].copy()
        filtered = filtered[
            filtered[identity_column].fillna("").astype(str).str.strip() == str(product_key).strip()
        ]

    return filtered.reset_index(drop=True)


def _first_text(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column].dropna().astype(str):
        text = value.strip()
        if text:
            return text
    return ""


def _percentage_change(current: object, previous: object) -> float | None:
    current_value = pd.to_numeric(current, errors="coerce")
    previous_value = pd.to_numeric(previous, errors="coerce")
    if pd.isna(current_value) or pd.isna(previous_value) or float(previous_value) == 0:
        return None
    return (float(current_value) / float(previous_value) - 1) * 100


def _format_change_plain(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "н/д"
    return f"{float(value):+.1f}%"


def _short_label(value: object, max_length: int = 76) -> str:
    text = "" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value)
    text = re.sub(r"\s+", " ", text).strip() or "Не указано"
    if len(text) <= max_length:
        return text
    return f"{text[:max_length - 1].rstrip()}…"


def build_targeted_telegram_report(
    data: pd.DataFrame,
    *,
    report_kind: str,
    date_from: date,
    date_to: date,
    category: str | None = None,
    product_key: str | None = None,
    include_file_note: bool = True,
) -> tuple[str, TelegramReportFile]:
    if report_kind not in TARGETED_TELEGRAM_REPORT_LABELS:
        raise ValueError("Выбран неизвестный тип Telegram-отчёта.")
    if report_kind == "sku" and not product_key:
        raise ValueError("Для карточки SKU выберите товар или артикул.")

    start_date = pd.Timestamp(date_from).date()
    end_date = pd.Timestamp(date_to).date()
    filtered = _filter_targeted_sales_data(
        data,
        date_from=start_date,
        date_to=end_date,
        category=category,
        product_key=product_key,
    )
    if filtered.empty:
        raise ValueError("За выбранный период и срез нет данных для отчёта.")

    product_group_column = "product_key" if "product_key" in filtered.columns else "product"
    overview = build_overview_metrics(filtered)
    monthly_summary = build_monthly_summary(filtered)
    category_summary = build_product_summary(filtered, "category")
    product_summary = build_product_summary(filtered, product_group_column)
    portfolio_summary = build_abc_analysis(product_summary, "revenue")

    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    previous = _filter_targeted_sales_data(
        data,
        date_from=previous_start,
        date_to=previous_end,
        category=category,
        product_key=product_key,
    )
    previous_overview = build_overview_metrics(previous) if not previous.empty else {}
    revenue_change = _percentage_change(overview.get("total_revenue"), previous_overview.get("total_revenue"))
    margin_change = _percentage_change(overview.get("total_margin"), previous_overview.get("total_margin"))

    report_label = TARGETED_TELEGRAM_REPORT_LABELS[report_kind]
    period_label = f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
    message_lines = [
        f"<b>ArtDB: {escape(report_label)}</b>",
        f"Период: {period_label}",
    ]
    if category:
        message_lines.append(f"Категория: {escape(_short_label(category))}")

    sku_label = ""
    if report_kind == "sku":
        sku_label = _short_label(product_summary.iloc[0].get("group_name", product_key))
        message_lines.append(f"SKU: {escape(sku_label)}")
        item_code = _first_text(filtered, "item_code")
        if item_code and item_code.casefold() not in sku_label.casefold():
            message_lines.append(f"Артикул: {escape(item_code)}")
        supplier = _first_text(filtered, "supplier")
        if supplier:
            message_lines.append(f"Поставщик: {escape(_short_label(supplier))}")

    message_lines.extend(
        [
            "",
            "<b>Ключевые показатели</b>",
            f"• Выручка: {format_money_plain(overview.get('total_revenue'))}",
            f"• Валовая прибыль: {format_money_plain(overview.get('total_margin'))}",
            f"• Маржинальность: {format_percent_plain(overview.get('margin_pct'))}",
            f"• Количество: {format_number_plain(overview.get('total_quantity'))}",
            f"• SKU: {format_number_plain(overview.get('product_count'))}",
            f"• Строк продаж: {format_number_plain(overview.get('line_count'))}",
            "",
            f"К предыдущим {period_days} дн.: выручка {_format_change_plain(revenue_change)}, "
            f"прибыль {_format_change_plain(margin_change)}.",
        ]
    )

    if report_kind in {"summary", "categories"}:
        message_lines.extend(["", "<b>Категории-лидеры</b>"])
        for position, (_, row) in enumerate(category_summary.head(5).iterrows(), start=1):
            message_lines.append(
                f"{position}. {escape(_short_label(row.get('group_name')))}: "
                f"{format_money_plain(row.get('revenue'))}; маржа {format_percent_plain(row.get('margin_pct'))}"
            )
    elif report_kind == "portfolio":
        message_lines.extend(["", "<b>SKU-лидеры портфеля</b>"])
        for position, (_, row) in enumerate(portfolio_summary.head(5).iterrows(), start=1):
            message_lines.append(
                f"{position}. {escape(_short_label(row.get('group_name')))}: "
                f"{format_money_plain(row.get('revenue'))}; ABC {escape(str(row.get('abc_class', 'н/д')))}"
            )
    else:
        message_lines.extend(["", "<b>Динамика по месяцам</b>"])
        for _, row in monthly_summary.tail(4).iterrows():
            message_lines.append(
                f"• {escape(str(row.get('month_label', 'н/д')))}: "
                f"{format_money_plain(row.get('revenue'))}; {format_number_plain(row.get('quantity'))} шт."
            )

    if include_file_note:
        message_lines.extend(["", "Детализация приложена в одном Excel-файле."])

    summary_sheet = pd.DataFrame(
        [
            {"Показатель": "Тип отчёта", "Значение": report_label, "Единица": ""},
            {"Показатель": "Период с", "Значение": start_date.isoformat(), "Единица": ""},
            {"Показатель": "Период по", "Значение": end_date.isoformat(), "Единица": ""},
            {"Показатель": "Категория", "Значение": category or "Все категории", "Единица": ""},
            {"Показатель": "SKU / артикул", "Значение": sku_label or "Все SKU", "Единица": ""},
            {"Показатель": "Выручка", "Значение": overview.get("total_revenue"), "Единица": "сом"},
            {"Показатель": "Валовая прибыль", "Значение": overview.get("total_margin"), "Единица": "сом"},
            {"Показатель": "Маржинальность", "Значение": overview.get("margin_pct"), "Единица": "%"},
            {"Показатель": "Количество", "Значение": overview.get("total_quantity"), "Единица": "шт."},
            {"Показатель": "SKU", "Значение": overview.get("product_count"), "Единица": ""},
            {"Показатель": "Изменение выручки", "Значение": revenue_change, "Единица": "%"},
            {"Показатель": "Изменение прибыли", "Значение": margin_change, "Единица": "%"},
        ]
    )
    monthly_sheet = _prepare_excel_frame(
        monthly_summary,
        ["month_label", "revenue", "cost", "margin", "quantity", "product_count", "revenue_change_pct"],
        {
            "month_label": "Месяц",
            "revenue": "Выручка",
            "cost": "Себестоимость",
            "margin": "Валовая прибыль",
            "quantity": "Количество",
            "product_count": "SKU",
            "revenue_change_pct": "Изменение выручки, %",
        },
    )
    category_sheet = _prepare_excel_frame(
        category_summary,
        ["group_name", "revenue", "cost", "margin", "margin_pct", "quantity", "sales_lines"],
        {
            "group_name": "Категория",
            "revenue": "Выручка",
            "cost": "Себестоимость",
            "margin": "Валовая прибыль",
            "margin_pct": "Маржинальность, %",
            "quantity": "Количество",
            "sales_lines": "Строк продаж",
        },
    )
    portfolio_sheet = _prepare_excel_frame(
        portfolio_summary,
        [
            "item_code",
            "group_name",
            "product_name",
            "revenue",
            "cost",
            "margin",
            "margin_pct",
            "quantity",
            "sales_lines",
            "abc_class",
            "share_pct",
            "cum_share_pct",
        ],
        {
            "item_code": "Артикул",
            "group_name": "SKU / товар",
            "product_name": "Наименование",
            "revenue": "Выручка",
            "cost": "Себестоимость",
            "margin": "Валовая прибыль",
            "margin_pct": "Маржинальность, %",
            "quantity": "Количество",
            "sales_lines": "Строк продаж",
            "abc_class": "ABC",
            "share_pct": "Доля выручки, %",
            "cum_share_pct": "Накопительная доля, %",
        },
    )

    sheets: dict[str, pd.DataFrame] = {"Сводка": summary_sheet}
    if report_kind == "categories":
        sheets.update({"Категории": category_sheet, "Портфель SKU": portfolio_sheet, "Динамика": monthly_sheet})
    elif report_kind == "portfolio":
        sheets.update({"Портфель SKU": portfolio_sheet, "Динамика": monthly_sheet, "Категории": category_sheet})
    elif report_kind == "sku":
        detail_sheet = _prepare_excel_frame(
            filtered.sort_values("date", ascending=False),
            [
                "date",
                "salon",
                "item_code",
                "product",
                "category",
                "supplier",
                "manager",
                "quantity",
                "revenue",
                "cost",
                "margin",
                "margin_pct",
            ],
            {
                "date": "Дата",
                "salon": "Салон",
                "item_code": "Артикул",
                "product": "Товар",
                "category": "Категория",
                "supplier": "Поставщик",
                "manager": "Менеджер",
                "quantity": "Количество",
                "revenue": "Выручка",
                "cost": "Себестоимость",
                "margin": "Валовая прибыль",
                "margin_pct": "Маржинальность, %",
            },
        )
        sheets.update({"Карточка SKU": portfolio_sheet, "Динамика SKU": monthly_sheet, "Продажи SKU": detail_sheet})
    else:
        sheets.update({"Динамика": monthly_sheet, "Категории": category_sheet, "Портфель SKU": portfolio_sheet})

    filename = f"artdb_{report_kind}_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx"
    report_file = TelegramReportFile(
        filename=filename,
        content=_export_excel_workbook(sheets),
        caption=f"{report_label}. Период: {period_label}",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return "\n".join(message_lines), report_file


def send_targeted_telegram_report(
    data: pd.DataFrame,
    *,
    report_kind: str,
    date_from: date,
    date_to: date,
    category: str | None = None,
    product_key: str | None = None,
    with_file: bool = True,
    chat_id: str | None = None,
) -> int:
    message, report_file = build_targeted_telegram_report(
        data,
        report_kind=report_kind,
        date_from=date_from,
        date_to=date_to,
        category=category,
        product_key=product_key,
        include_file_note=with_file,
    )
    send_telegram_message(message, chat_id=chat_id)
    if not with_file:
        return 0
    send_telegram_document(report_file, chat_id=chat_id)
    return 1


def build_telegram_report_files() -> list[TelegramReportFile]:
    salons = load_salons()
    archive_result = load_archive_data(salons=salons if salons else None)
    report_files: list[TelegramReportFile] = []
    today_label = datetime.now(get_timezone()).strftime("%Y%m%d")

    manifest = archive_result.manifest if not archive_result.manifest.empty else load_manifest()
    if not manifest.empty:
        report_files.append(
            TelegramReportFile(
                filename=f"artdb_uploads_{today_label}.csv",
                content=to_csv_bytes(manifest),
                caption="Реестр загрузок ArtDB",
            )
        )

    if archive_result.data.empty:
        return report_files

    data = archive_result.data.copy()
    monthly_summary = build_monthly_summary(data)
    product_summary = build_product_summary(data)

    if not monthly_summary.empty:
        report_files.append(
            TelegramReportFile(
                filename=f"artdb_monthly_summary_{today_label}.csv",
                content=_export_frame(
                    monthly_summary,
                    ["month_label", "revenue", "margin", "quantity", "revenue_change_pct", "margin_change_pct"],
                    {
                        "month_label": "Месяц",
                        "revenue": "Выручка",
                        "margin": "Маржа",
                        "quantity": "Количество",
                        "revenue_change_pct": "Изменение выручки, %",
                        "margin_change_pct": "Изменение маржи, %",
                    },
                ),
                caption="Помесячная сводка продаж",
            )
        )

    if not product_summary.empty:
        report_files.append(
            TelegramReportFile(
                filename=f"artdb_top_products_{today_label}.csv",
                content=_export_frame(
                    product_summary.head(int(os.getenv("TELEGRAM_REPORT_TOP_ROWS", "100"))),
                    ["group_name", "revenue", "margin", "margin_pct", "quantity", "sales_lines"],
                    {
                        "group_name": "Товар",
                        "revenue": "Выручка",
                        "margin": "Маржа",
                        "margin_pct": "Маржа, %",
                        "quantity": "Количество",
                        "sales_lines": "Строк продаж",
                    },
                ),
                caption="Топ товаров по продажам",
            )
        )

    procurement_forecast = build_default_procurement_forecast(data)
    if procurement_forecast.empty:
        return report_files

    procurement_columns = [
        "product",
        "category",
        "supplier",
        "abc_class",
        "xyz_class",
        "priority",
        "stock_status",
        "demand_state",
        "forecast_qty",
        "stock_on_hand",
        "stock_in_transit",
        "available_stock_qty",
        "stock_coverage_days",
        "net_requirement_qty",
        "recommended_order_qty",
        "last_sale_date",
        "days_since_last_sale",
        "notes",
    ]
    procurement_rename_map = {
        "product": "SKU / Товар",
        "category": "Категория",
        "supplier": "Поставщик",
        "abc_class": "ABC",
        "xyz_class": "XYZ",
        "priority": "Приоритет",
        "stock_status": "Статус остатка",
        "demand_state": "Состояние спроса",
        "forecast_qty": "Прогноз потребности, шт",
        "stock_on_hand": "Остаток",
        "stock_in_transit": "В пути",
        "available_stock_qty": "Доступно",
        "stock_coverage_days": "Покрытие, дней",
        "net_requirement_qty": "Чистая потребность, шт",
        "recommended_order_qty": "Рекомендованный заказ, шт",
        "last_sale_date": "Последняя продажа",
        "days_since_last_sale": "Дней без продаж",
        "notes": "Примечание",
    }
    report_files.append(
        TelegramReportFile(
            filename=f"artdb_procurement_forecast_{today_label}.csv",
            content=_export_frame(procurement_forecast, procurement_columns, procurement_rename_map),
            caption="Прогноз закупок",
        )
    )

    supplier_summary = build_procurement_supplier_summary(procurement_forecast)
    if not supplier_summary.empty:
        report_files.append(
            TelegramReportFile(
                filename=f"artdb_procurement_suppliers_{today_label}.csv",
                content=_export_frame(
                    supplier_summary,
                    [
                        "supplier",
                        "sku_count",
                        "reorder_sku_count",
                        "critical_sku_count",
                        "recommended_order_qty",
                        "net_requirement_qty",
                        "available_stock_qty",
                        "forecast_qty",
                    ],
                    {
                        "supplier": "Поставщик",
                        "sku_count": "SKU",
                        "reorder_sku_count": "SKU к заказу",
                        "critical_sku_count": "Критичных SKU",
                        "recommended_order_qty": "Рекомендованный заказ, шт",
                        "net_requirement_qty": "Чистая потребность, шт",
                        "available_stock_qty": "Доступный остаток",
                        "forecast_qty": "Прогноз спроса, шт",
                    },
                ),
                caption="Сводка закупок по поставщикам",
            )
        )

    stock_frames = build_procurement_stock_risk_frames(procurement_forecast, total_window_days=51)
    stock_risk_report = stock_frames["report"]
    if not stock_risk_report.empty:
        report_files.append(
            TelegramReportFile(
                filename=f"artdb_stock_risks_{today_label}.csv",
                content=_export_frame(
                    stock_risk_report,
                    ["risk_type", *procurement_columns],
                    {"risk_type": "Тип риска", **procurement_rename_map},
                ),
                caption="Остатки и риски",
            )
        )

    supplier_order_file = build_supplier_order_file(procurement_forecast, today_label=today_label)
    if supplier_order_file is not None:
        report_files.append(supplier_order_file)

    return report_files


def send_telegram_report_pack(*, with_files: bool = True, caption: str | None = None) -> int:
    send_telegram_message(caption or build_daily_summary())
    sent_files = 0
    if with_files:
        for report_file in build_telegram_report_files():
            send_telegram_document(report_file)
            sent_files += 1
    return sent_files
