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
from PIL import Image, ImageDraw, ImageFont

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
from telegram_settings_store import load_telegram_settings, parse_telegram_chat_ids


@dataclass(frozen=True)
class TelegramReportFile:
    filename: str
    content: bytes
    caption: str = ""
    content_type: str = "text/csv"


TARGETED_TELEGRAM_REPORT_LABELS = {
    "summary": "Управленческая сводка",
    "categories": "Отчёт по категориям",
    "brand": "Отчёт по бренду",
    "supplier": "Отчёт по поставщику",
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
    return bool(token and parse_telegram_chat_ids(chat_id))


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
    if require_chat and not parse_telegram_chat_ids(chat_id):
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
    target_chat_ids = parse_telegram_chat_ids(chat_id if chat_id is not None else configured_chat_id)
    if not target_chat_ids:
        raise RuntimeError("Не выбран Telegram-чат для отчётов.")

    for target_chat_id in target_chat_ids:
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
        {"command": "chatid", "description": "Показать ID текущего чата"},
        {"command": "summary", "description": "Управленческая сводка"},
        {"command": "categories", "description": "Отчёт по категориям"},
        {"command": "brand", "description": "Отчёт по бренду"},
        {"command": "supplier", "description": "Отчёт по поставщику"},
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
    target_chat_ids = parse_telegram_chat_ids(chat_id if chat_id is not None else configured_chat_id)
    if not target_chat_ids:
        raise RuntimeError("Не выбран Telegram-чат для отчётов.")

    for target_chat_id in target_chat_ids:
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


def send_telegram_photo(
    report_file: TelegramReportFile,
    *,
    chat_id: str | None = None,
) -> None:
    _, configured_chat_id = _get_telegram_credentials()
    target_chat_ids = parse_telegram_chat_ids(chat_id if chat_id is not None else configured_chat_id)
    if not target_chat_ids:
        raise RuntimeError("Не выбран Telegram-чат для отчётов.")

    for target_chat_id in target_chat_ids:
        fields = {
            "chat_id": target_chat_id,
            "caption": _normalize_telegram_html(report_file.caption)[:1024],
            "parse_mode": "HTML",
        }
        payload, boundary = _encode_multipart_formdata(
            fields,
            [
                (
                    "photo",
                    report_file.filename,
                    report_file.content,
                    report_file.content_type,
                )
            ],
        )
        _telegram_api_request(
            "sendPhoto",
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


TELEGRAM_CARD_WIDTH = 1200
TELEGRAM_CARD_HEIGHT = 830
TELEGRAM_CARD_COLORS = {
    "navy": "#003461",
    "teal": "#006C49",
    "gold": "#D89A2B",
    "danger": "#D94D3D",
    "background": "#F4F7F9",
    "surface": "#FFFFFF",
    "border": "#DCE5EB",
    "text": "#0F172A",
    "muted": "#64748B",
    "grid": "#EAF0F4",
    "soft_blue": "#DDEBFA",
}


def _telegram_card_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    configured_font = os.getenv("ARTDB_REPORT_FONT", "").strip()
    candidates = [
        configured_font,
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fit_card_text(
    draw: ImageDraw.ImageDraw,
    value: object,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    while text and draw.textlength(f"{text}{suffix}", font=font) > max_width:
        text = text[:-1].rstrip()
    return f"{text}{suffix}" if text else suffix


def _build_telegram_card_trend(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns or "revenue" not in frame.columns:
        return pd.DataFrame(columns=["label", "revenue"])

    trend = frame[["date", "revenue"]].copy()
    trend["date"] = pd.to_datetime(trend["date"], errors="coerce")
    trend["revenue"] = pd.to_numeric(trend["revenue"], errors="coerce").fillna(0.0)
    trend = trend.dropna(subset=["date"])
    if trend.empty:
        return pd.DataFrame(columns=["label", "revenue"])

    span_days = max((trend["date"].max() - trend["date"].min()).days, 0)
    if span_days <= 45:
        trend["period"] = trend["date"].dt.normalize()
        label_format = "%d.%m"
        max_points = 12
    elif span_days <= 180:
        trend["period"] = trend["date"].dt.normalize() - pd.to_timedelta(
            trend["date"].dt.dayofweek,
            unit="D",
        )
        label_format = "%d.%m"
        max_points = 12
    else:
        trend["period"] = trend["date"].dt.to_period("M").dt.to_timestamp()
        label_format = "%m.%Y"
        max_points = 10

    grouped = (
        trend.groupby("period", as_index=False)["revenue"]
        .sum()
        .sort_values("period")
        .tail(max_points)
        .reset_index(drop=True)
    )
    grouped["label"] = grouped["period"].dt.strftime(label_format)
    return grouped[["label", "revenue"]]


def _draw_telegram_metric_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    label: str,
    value: str,
    accent: str,
) -> None:
    draw.rounded_rectangle(
        box,
        radius=22,
        fill=TELEGRAM_CARD_COLORS["surface"],
        outline=TELEGRAM_CARD_COLORS["border"],
        width=2,
    )
    left, top, right, _ = box
    draw.rounded_rectangle(
        (left + 20, top + 22, left + 28, top + 60),
        radius=4,
        fill=accent,
    )
    label_font = _telegram_card_font(20, bold=True)
    value_font = _telegram_card_font(30, bold=True)
    draw.text(
        (left + 44, top + 22),
        label.upper(),
        font=label_font,
        fill=TELEGRAM_CARD_COLORS["muted"],
    )
    fitted_value = _fit_card_text(draw, value, value_font, right - left - 48)
    draw.text(
        (left + 22, top + 76),
        fitted_value,
        font=value_font,
        fill=TELEGRAM_CARD_COLORS["text"],
    )


def _draw_telegram_trend_chart(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    trend: pd.DataFrame,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(
        box,
        radius=24,
        fill=TELEGRAM_CARD_COLORS["surface"],
        outline=TELEGRAM_CARD_COLORS["border"],
        width=2,
    )
    title_font = _telegram_card_font(24, bold=True)
    axis_font = _telegram_card_font(17)
    value_font = _telegram_card_font(20, bold=True)
    draw.text(
        (left + 28, top + 22),
        "Динамика выручки",
        font=title_font,
        fill=TELEGRAM_CARD_COLORS["navy"],
    )

    plot_left = left + 34
    plot_right = right - 34
    plot_top = top + 84
    plot_bottom = bottom - 48
    for step in range(4):
        y = int(plot_top + (plot_bottom - plot_top) * step / 3)
        draw.line(
            (plot_left, y, plot_right, y),
            fill=TELEGRAM_CARD_COLORS["grid"],
            width=2,
        )

    if trend.empty:
        draw.text(
            (plot_left, plot_top + 54),
            "Недостаточно данных для графика",
            font=axis_font,
            fill=TELEGRAM_CARD_COLORS["muted"],
        )
        return

    values = pd.to_numeric(trend["revenue"], errors="coerce").fillna(0.0).tolist()
    labels = trend["label"].fillna("").astype(str).tolist()
    value_min = min(0.0, min(values))
    value_max = max(values)
    if value_max == value_min:
        value_max = value_min + 1.0

    point_count = len(values)
    x_step = (plot_right - plot_left) / max(point_count - 1, 1)
    points: list[tuple[int, int]] = []
    for index, value in enumerate(values):
        x = int(plot_left + index * x_step) if point_count > 1 else int((plot_left + plot_right) / 2)
        y_ratio = (float(value) - value_min) / (value_max - value_min)
        y = int(plot_bottom - y_ratio * (plot_bottom - plot_top))
        points.append((x, y))

    if len(points) > 1:
        area_points = [points[0], *points, points[-1], (points[-1][0], plot_bottom), (points[0][0], plot_bottom)]
        draw.polygon(area_points, fill=TELEGRAM_CARD_COLORS["soft_blue"])
        draw.line(points, fill=TELEGRAM_CARD_COLORS["navy"], width=6, joint="curve")
    for x, y in points:
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=TELEGRAM_CARD_COLORS["gold"])

    if labels:
        draw.text((plot_left, plot_bottom + 14), labels[0], font=axis_font, fill=TELEGRAM_CARD_COLORS["muted"])
        last_label = labels[-1]
        label_width = draw.textlength(last_label, font=axis_font)
        draw.text(
            (plot_right - label_width, plot_bottom + 14),
            last_label,
            font=axis_font,
            fill=TELEGRAM_CARD_COLORS["muted"],
        )

    last_value = format_money_plain(values[-1])
    value_width = draw.textlength(last_value, font=value_font)
    label_x = min(max(points[-1][0] - value_width / 2, plot_left), plot_right - value_width)
    label_y = max(points[-1][1] - 38, plot_top)
    draw.rounded_rectangle(
        (label_x - 9, label_y - 4, label_x + value_width + 9, label_y + 28),
        radius=10,
        fill=TELEGRAM_CARD_COLORS["surface"],
        outline=TELEGRAM_CARD_COLORS["border"],
        width=1,
    )
    draw.text(
        (label_x, label_y),
        last_value,
        font=value_font,
        fill=TELEGRAM_CARD_COLORS["navy"],
    )


def _render_telegram_sales_card(
    *,
    title: str,
    period_label: str,
    scope_label: str,
    overview: dict[str, object],
    trend: pd.DataFrame,
    revenue_change: float | None,
    margin_change: float | None,
) -> bytes:
    image = Image.new(
        "RGB",
        (TELEGRAM_CARD_WIDTH, TELEGRAM_CARD_HEIGHT),
        TELEGRAM_CARD_COLORS["background"],
    )
    draw = ImageDraw.Draw(image)
    margin = 54

    eyebrow_font = _telegram_card_font(19, bold=True)
    title_font = _telegram_card_font(42, bold=True)
    subtitle_font = _telegram_card_font(21)
    draw.text((margin, 36), "ARTDB  /  BUSINESS REPORT", font=eyebrow_font, fill=TELEGRAM_CARD_COLORS["teal"])
    draw.text(
        (margin, 72),
        _fit_card_text(draw, title, title_font, TELEGRAM_CARD_WIDTH - margin * 2),
        font=title_font,
        fill=TELEGRAM_CARD_COLORS["navy"],
    )
    subtitle = f"{period_label}  |  {scope_label}"
    draw.text(
        (margin, 130),
        _fit_card_text(draw, subtitle, subtitle_font, TELEGRAM_CARD_WIDTH - margin * 2),
        font=subtitle_font,
        fill=TELEGRAM_CARD_COLORS["muted"],
    )

    metric_top = 180
    gap = 16
    metric_width = int((TELEGRAM_CARD_WIDTH - margin * 2 - gap * 3) / 4)
    metric_specs = [
        ("Выручка", format_money_plain(overview.get("total_revenue")), TELEGRAM_CARD_COLORS["navy"]),
        ("Валовая прибыль", format_money_plain(overview.get("total_margin")), TELEGRAM_CARD_COLORS["teal"]),
        ("Маржинальность", format_percent_plain(overview.get("margin_pct")), TELEGRAM_CARD_COLORS["gold"]),
        ("SKU", format_number_plain(overview.get("product_count")), "#2673B8"),
    ]
    for index, (label, value, accent) in enumerate(metric_specs):
        left = margin + index * (metric_width + gap)
        _draw_telegram_metric_card(
            draw,
            (left, metric_top, left + metric_width, metric_top + 142),
            label=label,
            value=value,
            accent=accent,
        )

    _draw_telegram_trend_chart(
        draw,
        (margin, 346, TELEGRAM_CARD_WIDTH - margin, 666),
        trend,
    )

    comparison_font = _telegram_card_font(21, bold=True)
    comparison_value_font = _telegram_card_font(24, bold=True)
    draw.text(
        (margin, 700),
        "К ПРЕДЫДУЩЕМУ ПЕРИОДУ",
        font=comparison_font,
        fill=TELEGRAM_CARD_COLORS["muted"],
    )
    comparisons = [
        ("Выручка", revenue_change),
        ("Валовая прибыль", margin_change),
    ]
    comparison_x = margin
    for label, change in comparisons:
        value_text = _format_change_plain(change)
        change_color = (
            TELEGRAM_CARD_COLORS["teal"]
            if change is not None and not pd.isna(change) and float(change) >= 0
            else TELEGRAM_CARD_COLORS["danger"]
        )
        if change is None or pd.isna(change):
            change_color = TELEGRAM_CARD_COLORS["muted"]
        line = f"{label}: {value_text}"
        draw.text(
            (comparison_x, 738),
            line,
            font=comparison_value_font,
            fill=change_color,
        )
        comparison_x += 380

    footer_font = _telegram_card_font(16)
    generated_label = datetime.now(get_timezone()).strftime("Сформировано %d.%m.%Y %H:%M")
    generated_width = draw.textlength(generated_label, font=footer_font)
    draw.text(
        (TELEGRAM_CARD_WIDTH - margin - generated_width, 795),
        generated_label,
        font=footer_font,
        fill=TELEGRAM_CARD_COLORS["muted"],
    )

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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


TELEGRAM_DIVIDER = "━━━━━━━━━━━━"


def _telegram_metric_line(label: str, value: object) -> str:
    """Render one compact metric line for mobile Telegram clients."""
    return f"<b>{label}:</b> {value}"


def _telegram_numbered_item(position: int, value: object) -> str:
    return f"<b>{position}.</b> {value}"


def _telegram_ranked_item(
    position: int,
    label: object,
    details: str,
) -> list[str]:
    return [
        f"<b>{position}. {escape(_short_label(label))}</b>",
        f"   {details}",
    ]


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
        "<b>ArtDB | Ежедневная сводка</b>",
        _telegram_metric_line("Дата", today.strftime("%d.%m.%Y")),
        TELEGRAM_DIVIDER,
        "<b>Загрузка данных</b>",
        _telegram_metric_line("Салоны", format_number_plain(len(salons))),
        _telegram_metric_line(
            "Загружено сегодня",
            f"{format_number_plain(len(uploaded_salons))} из {format_number_plain(len(salons))}",
        ),
    ]
    if app_url:
        summary_lines.append(_telegram_metric_line("Сайт", escape(app_url)))

    if missing_salons:
        summary_lines.extend(["", "<b>Ожидают загрузку</b>"])
        summary_lines.extend(f"• {escape(salon)}" for salon in missing_salons)
    else:
        summary_lines.append(_telegram_metric_line("Статус", "все салоны загрузили данные"))

    archive_result = load_archive_data(salons=salons if salons else None)
    procurement_forecast = pd.DataFrame()
    if not archive_result.data.empty:
        monthly_summary = build_monthly_summary(archive_result.data)
        latest_month = monthly_summary.iloc[-1] if not monthly_summary.empty else {}
        latest_data = archive_result.data
        latest_month_date = pd.to_datetime(latest_month.get("month"), errors="coerce")
        if pd.notna(latest_month_date) and "date" in archive_result.data.columns:
            archive_dates = pd.to_datetime(archive_result.data["date"], errors="coerce")
            latest_data = archive_result.data[
                archive_dates.dt.to_period("M") == latest_month_date.to_period("M")
            ].copy()
        overview = build_overview_metrics(latest_data)
        product_summary = build_product_summary(latest_data)
        risk_count = int((product_summary["margin_pct"].fillna(9999) < 15).sum()) if "margin_pct" in product_summary.columns else 0
        summary_lines.extend(
            [
                "",
                TELEGRAM_DIVIDER,
                f"<b>Продажи | {escape(str(latest_month.get('month_label', 'н/д')))}</b>",
                _telegram_metric_line("Выручка", format_money_plain(overview.get("total_revenue"))),
                _telegram_metric_line("Валовая прибыль", format_money_plain(overview.get("total_margin"))),
                _telegram_metric_line("Маржинальность", format_percent_plain(overview.get("margin_pct"))),
                _telegram_metric_line("Количество", format_number_plain(overview.get("total_quantity"))),
                _telegram_metric_line("SKU с маржой &lt;15%", format_number_plain(risk_count)),
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
                    TELEGRAM_DIVIDER,
                    "<b>Закупки и остатки</b>",
                    _telegram_metric_line(
                        "SKU к заказу",
                        format_number_plain(procurement_overview.get("reorder_sku_count")),
                    ),
                    _telegram_metric_line(
                        "Риск дефицита",
                        format_number_plain(procurement_overview.get("critical_stock_count")),
                    ),
                    _telegram_metric_line(
                        "Рекомендованный заказ",
                        f"{format_number_plain(procurement_overview.get('recommended_order_qty_total'))} шт.",
                    ),
                    _telegram_metric_line(
                        "Неликвид с остатком",
                        f"{format_number_plain(len(stock_frames['dormant']))} SKU",
                    ),
                ]
            )

    alerts = build_automation_alerts(
        archive_result.data,
        salons=salons,
        manifest=manifest,
        procurement_forecast=procurement_forecast,
    )
    if alerts:
        summary_lines.extend(["", TELEGRAM_DIVIDER, "<b>Что требует внимания</b>"])
        summary_lines.extend(
            _telegram_numbered_item(position, alert)
            for position, alert in enumerate(alerts[:6], start=1)
        )

    if archive_result.warnings:
        summary_lines.extend(["", TELEGRAM_DIVIDER, "<b>Предупреждения архива</b>"])
        summary_lines.extend(
            _telegram_numbered_item(position, escape(warning))
            for position, warning in enumerate(archive_result.warnings[:5], start=1)
        )

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
            "<b>ArtDB | Контроль рисков</b>",
            _telegram_metric_line("Дата", today),
            TELEGRAM_DIVIDER,
            "<b>Требует внимания</b>",
            *[
                _telegram_numbered_item(position, alert)
                for position, alert in enumerate(alerts, start=1)
            ],
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
    brand: str | None = None,
    supplier: str | None = None,
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

    for column, selected_value in (("brand", brand), ("supplier", supplier)):
        if not selected_value:
            continue
        if column not in filtered.columns:
            return filtered.iloc[0:0].copy()
        normalized = filtered[column].fillna("").astype(str).str.strip().replace("", "Не назначен")
        filtered = filtered[normalized.str.casefold() == str(selected_value).strip().casefold()]

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


def build_targeted_telegram_card(
    data: pd.DataFrame,
    *,
    report_kind: str,
    date_from: date,
    date_to: date,
    category: str | None = None,
    product_key: str | None = None,
    brand: str | None = None,
    supplier: str | None = None,
) -> TelegramReportFile:
    if report_kind not in TARGETED_TELEGRAM_REPORT_LABELS:
        raise ValueError("Выбран неизвестный тип Telegram-отчёта.")

    start_date = pd.Timestamp(date_from).date()
    end_date = pd.Timestamp(date_to).date()
    filtered = _filter_targeted_sales_data(
        data,
        date_from=start_date,
        date_to=end_date,
        category=category,
        product_key=product_key,
        brand=brand,
        supplier=supplier,
    )
    if filtered.empty:
        raise ValueError("За выбранный период и срез нет данных для визуальной карточки.")

    overview = build_overview_metrics(filtered)
    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    previous = _filter_targeted_sales_data(
        data,
        date_from=previous_start,
        date_to=previous_end,
        category=category,
        product_key=product_key,
        brand=brand,
        supplier=supplier,
    )
    previous_overview = build_overview_metrics(previous) if not previous.empty else {}
    revenue_change = _percentage_change(
        overview.get("total_revenue"),
        previous_overview.get("total_revenue"),
    )
    margin_change = _percentage_change(
        overview.get("total_margin"),
        previous_overview.get("total_margin"),
    )

    scope_parts: list[str] = []
    if category:
        scope_parts.append(f"Категория: {_short_label(category, 34)}")
    if brand:
        scope_parts.append(f"Бренд: {_short_label(brand, 34)}")
    if supplier:
        scope_parts.append(f"Поставщик: {_short_label(supplier, 34)}")
    if product_key:
        product_name = _first_text(filtered, "product") or product_key
        scope_parts.append(f"SKU: {_short_label(product_name, 46)}")
    scope_label = " / ".join(scope_parts) or "Все данные"
    report_label = TARGETED_TELEGRAM_REPORT_LABELS[report_kind]
    period_label = f"{start_date:%d.%m.%Y} - {end_date:%d.%m.%Y}"
    image_content = _render_telegram_sales_card(
        title=report_label,
        period_label=period_label,
        scope_label=scope_label,
        overview=overview,
        trend=_build_telegram_card_trend(filtered),
        revenue_change=revenue_change,
        margin_change=margin_change,
    )
    scope_filename = brand or supplier or product_key or category or "all"
    safe_scope = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "_", str(scope_filename)).strip("_")[:36]
    return TelegramReportFile(
        filename=f"artdb_{report_kind}_{safe_scope}_{start_date:%Y%m%d}_{end_date:%Y%m%d}.png",
        content=image_content,
        caption=f"<b>ArtDB | {escape(report_label)}</b>\n{period_label}",
        content_type="image/png",
    )


def build_daily_telegram_card() -> TelegramReportFile | None:
    salons = load_salons()
    archive_result = load_archive_data(salons=salons if salons else None)
    if archive_result.data.empty:
        return None

    data = archive_result.data.copy()
    monthly_summary = build_monthly_summary(data)
    if monthly_summary.empty:
        return None

    latest_row = monthly_summary.iloc[-1]
    latest_month = pd.to_datetime(latest_row.get("month"), errors="coerce")
    parsed_dates = (
        pd.to_datetime(data["date"], errors="coerce")
        if "date" in data.columns
        else pd.Series(pd.NaT, index=data.index, dtype="datetime64[ns]")
    )
    if pd.notna(latest_month) and parsed_dates.notna().any():
        latest_mask = parsed_dates.dt.to_period("M") == latest_month.to_period("M")
        latest_data = data[latest_mask].copy()
    else:
        latest_data = data.copy()
    overview = build_overview_metrics(latest_data)
    trend = monthly_summary.tail(10)[["month_label", "revenue"]].rename(
        columns={"month_label": "label"}
    )
    period_label = str(latest_row.get("month_label") or "Последний доступный месяц")
    image_content = _render_telegram_sales_card(
        title="Ежедневная управленческая сводка",
        period_label=period_label,
        scope_label=f"Салоны: {format_number_plain(len(salons))}",
        overview=overview,
        trend=trend,
        revenue_change=pd.to_numeric(latest_row.get("revenue_change_pct"), errors="coerce"),
        margin_change=pd.to_numeric(latest_row.get("margin_change_pct"), errors="coerce"),
    )
    today_label = datetime.now(get_timezone()).strftime("%Y%m%d")
    return TelegramReportFile(
        filename=f"artdb_daily_summary_{today_label}.png",
        content=image_content,
        caption=f"<b>ArtDB | Ежедневная сводка</b>\nПоследний период: {escape(period_label)}",
        content_type="image/png",
    )


def build_targeted_telegram_report(
    data: pd.DataFrame,
    *,
    report_kind: str,
    date_from: date,
    date_to: date,
    category: str | None = None,
    product_key: str | None = None,
    brand: str | None = None,
    supplier: str | None = None,
    include_file_note: bool = True,
    procurement_forecast: pd.DataFrame | None = None,
) -> tuple[str, TelegramReportFile]:
    if report_kind not in TARGETED_TELEGRAM_REPORT_LABELS:
        raise ValueError("Выбран неизвестный тип Telegram-отчёта.")
    if report_kind == "sku" and not product_key:
        raise ValueError("Для карточки SKU выберите товар или артикул.")
    if report_kind == "brand" and not brand:
        raise ValueError("Для отчёта выберите бренд.")
    if report_kind == "supplier" and not supplier:
        raise ValueError("Для отчёта выберите поставщика.")

    start_date = pd.Timestamp(date_from).date()
    end_date = pd.Timestamp(date_to).date()
    filtered = _filter_targeted_sales_data(
        data,
        date_from=start_date,
        date_to=end_date,
        category=category,
        product_key=product_key,
        brand=brand,
        supplier=supplier,
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
        brand=brand,
        supplier=supplier,
    )
    previous_overview = build_overview_metrics(previous) if not previous.empty else {}
    revenue_change = _percentage_change(overview.get("total_revenue"), previous_overview.get("total_revenue"))
    margin_change = _percentage_change(overview.get("total_margin"), previous_overview.get("total_margin"))

    scope_forecast = pd.DataFrame()
    scope_procurement_overview: dict[str, float] = {}
    scope_risks: dict[str, object] = {}
    if report_kind in {"brand", "supplier"}:
        forecast_source = (
            procurement_forecast.copy()
            if procurement_forecast is not None
            else build_default_procurement_forecast(data)
        )
        scope_column = "brand" if report_kind == "brand" else "supplier"
        scope_value = brand if report_kind == "brand" else supplier
        if not forecast_source.empty and scope_column in forecast_source.columns:
            normalized_scope = (
                forecast_source[scope_column]
                .fillna("")
                .astype(str)
                .str.strip()
                .replace("", "Не назначен")
            )
            scope_forecast = forecast_source[
                normalized_scope.str.casefold() == str(scope_value).strip().casefold()
            ].copy()
        scope_procurement_overview = build_procurement_overview(scope_forecast)
        scope_risks = build_procurement_stock_risk_frames(
            scope_forecast,
            total_window_days=max((end_date - start_date).days + 1, 30),
        )

    report_label = TARGETED_TELEGRAM_REPORT_LABELS[report_kind]
    period_label = f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
    message_lines = [
        f"<b>ArtDB | {escape(report_label)}</b>",
        _telegram_metric_line("Период", period_label),
    ]
    if category:
        message_lines.append(f"<b>Категория: {escape(_short_label(category))}</b>")
    if brand:
        message_lines.append(f"<b>Бренд: {escape(_short_label(brand))}</b>")
    if supplier:
        message_lines.append(f"<b>Поставщик: {escape(_short_label(supplier))}</b>")

    sku_label = ""
    if report_kind == "sku":
        sku_label = _short_label(product_summary.iloc[0].get("group_name", product_key))
        message_lines.append(f"<b>SKU: {escape(sku_label)}</b>")
        item_code = _first_text(filtered, "item_code")
        if item_code and item_code.casefold() not in sku_label.casefold():
            message_lines.append(_telegram_metric_line("Артикул", escape(item_code)))
        supplier = _first_text(filtered, "supplier")
        if supplier:
            message_lines.append(_telegram_metric_line("Поставщик", escape(_short_label(supplier))))

    message_lines.extend(
        [
            TELEGRAM_DIVIDER,
            "<b>Ключевые показатели</b>",
            _telegram_metric_line("Выручка", format_money_plain(overview.get("total_revenue"))),
            _telegram_metric_line("Валовая прибыль", format_money_plain(overview.get("total_margin"))),
            _telegram_metric_line("Маржинальность", format_percent_plain(overview.get("margin_pct"))),
            _telegram_metric_line("Количество", format_number_plain(overview.get("total_quantity"))),
            _telegram_metric_line("SKU", format_number_plain(overview.get("product_count"))),
            _telegram_metric_line("Строк продаж", format_number_plain(overview.get("line_count"))),
            "",
            "<b>Сравнение с предыдущим периодом</b>",
            _telegram_metric_line("Выручка", _format_change_plain(revenue_change)),
            _telegram_metric_line("Валовая прибыль", _format_change_plain(margin_change)),
        ]
    )

    if report_kind in {"summary", "categories"}:
        message_lines.extend(["", TELEGRAM_DIVIDER, "<b>Категории-лидеры</b>"])
        for position, (_, row) in enumerate(category_summary.head(5).iterrows(), start=1):
            message_lines.extend(
                _telegram_ranked_item(
                    position,
                    row.get("group_name"),
                    f"Выручка: {format_money_plain(row.get('revenue'))} | "
                    f"Маржа: {format_percent_plain(row.get('margin_pct'))}",
                )
            )
    elif report_kind == "portfolio":
        message_lines.extend(["", TELEGRAM_DIVIDER, "<b>SKU-лидеры портфеля</b>"])
        for position, (_, row) in enumerate(portfolio_summary.head(5).iterrows(), start=1):
            message_lines.extend(
                _telegram_ranked_item(
                    position,
                    row.get("group_name"),
                    f"Выручка: {format_money_plain(row.get('revenue'))} | "
                    f"ABC: {escape(str(row.get('abc_class', 'н/д')))}",
                )
            )
    elif report_kind in {"brand", "supplier"}:
        shortage_frame = scope_risks.get("shortage", pd.DataFrame())
        overstock_frame = scope_risks.get("overstock", pd.DataFrame())
        stock_value = pd.to_numeric(
            scope_forecast.get("stock_value", pd.Series(dtype="float64")),
            errors="coerce",
        ).fillna(0).sum()
        stock_on_hand = pd.to_numeric(
            scope_forecast.get("stock_on_hand", pd.Series(dtype="float64")),
            errors="coerce",
        ).fillna(0).sum()
        message_lines.extend(
            [
                "",
                TELEGRAM_DIVIDER,
                "<b>Текущий склад и закупки</b>",
                _telegram_metric_line("Остаток, шт.", format_number_plain(stock_on_hand)),
                _telegram_metric_line("Стоимость остатка", format_money_plain(stock_value)),
                _telegram_metric_line(
                    "В пути",
                    f"{format_number_plain(scope_procurement_overview.get('ordered_in_transit_qty_total'))} шт.",
                ),
                _telegram_metric_line(
                    "К заказу",
                    f"{format_number_plain(scope_procurement_overview.get('recommended_order_qty_total'))} шт.",
                ),
                _telegram_metric_line(
                    "SKU к заказу",
                    format_number_plain(scope_procurement_overview.get("reorder_sku_count")),
                ),
                _telegram_metric_line("Дефицит", f"{format_number_plain(len(shortage_frame))} SKU"),
                _telegram_metric_line("Излишек", f"{format_number_plain(len(overstock_frame))} SKU"),
                "",
                "<b>SKU-лидеры по продажам</b>",
            ]
        )
        for position, (_, row) in enumerate(portfolio_summary.head(5).iterrows(), start=1):
            message_lines.extend(
                _telegram_ranked_item(
                    position,
                    row.get("group_name"),
                    f"Выручка: {format_money_plain(row.get('revenue'))} | "
                    f"Маржа: {format_percent_plain(row.get('margin_pct'))}",
                )
            )
    else:
        message_lines.extend(["", TELEGRAM_DIVIDER, "<b>Динамика по месяцам</b>"])
        for _, row in monthly_summary.tail(4).iterrows():
            message_lines.extend(
                [
                    f"<b>{escape(str(row.get('month_label', 'н/д')))}</b>",
                    f"   Выручка: {format_money_plain(row.get('revenue'))} | "
                    f"Количество: {format_number_plain(row.get('quantity'))} шт.",
                ]
            )

    if include_file_note:
        message_lines.extend(
            [
                "",
                TELEGRAM_DIVIDER,
                "<b>Файл:</b> подробная детализация приложена в Excel.",
            ]
        )

    summary_sheet = pd.DataFrame(
        [
            {"Показатель": "Тип отчёта", "Значение": report_label, "Единица": ""},
            {"Показатель": "Период с", "Значение": start_date.isoformat(), "Единица": ""},
            {"Показатель": "Период по", "Значение": end_date.isoformat(), "Единица": ""},
            {"Показатель": "Категория", "Значение": category or "Все категории", "Единица": ""},
            {"Показатель": "Бренд", "Значение": brand or "Все бренды", "Единица": ""},
            {"Показатель": "Поставщик", "Значение": supplier or "Все поставщики", "Единица": ""},
            {"Показатель": "SKU / артикул", "Значение": sku_label or "Все SKU", "Единица": ""},
            {"Показатель": "Выручка", "Значение": overview.get("total_revenue"), "Единица": "сом"},
            {"Показатель": "Валовая прибыль", "Значение": overview.get("total_margin"), "Единица": "сом"},
            {"Показатель": "Маржинальность", "Значение": overview.get("margin_pct"), "Единица": "%"},
            {"Показатель": "Количество", "Значение": overview.get("total_quantity"), "Единица": "шт."},
            {"Показатель": "SKU", "Значение": overview.get("product_count"), "Единица": ""},
            {"Показатель": "Изменение выручки", "Значение": revenue_change, "Единица": "%"},
            {"Показатель": "Изменение прибыли", "Значение": margin_change, "Единица": "%"},
            {
                "Показатель": "Стоимость текущего остатка",
                "Значение": pd.to_numeric(
                    scope_forecast.get("stock_value", pd.Series(dtype="float64")),
                    errors="coerce",
                ).fillna(0).sum(),
                "Единица": "сом",
            },
            {
                "Показатель": "Рекомендовано к заказу",
                "Значение": scope_procurement_overview.get("recommended_order_qty_total", 0),
                "Единица": "шт.",
            },
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
    procurement_columns = [
        "product",
        "category",
        "supplier",
        "brand",
        "abc_class",
        "xyz_class",
        "priority",
        "stock_status",
        "stock_on_hand",
        "stock_value",
        "stock_in_transit",
        "available_stock_qty",
        "stock_coverage_days",
        "forecast_qty",
        "recommended_order_qty",
        "last_sale_date",
        "days_since_last_sale",
    ]
    procurement_rename_map = {
        "product": "SKU / Товар",
        "category": "Категория",
        "supplier": "Поставщик",
        "brand": "Бренд",
        "abc_class": "ABC",
        "xyz_class": "XYZ",
        "priority": "Приоритет",
        "stock_status": "Статус остатка",
        "stock_on_hand": "Остаток",
        "stock_value": "Стоимость остатка",
        "stock_in_transit": "В пути",
        "available_stock_qty": "Доступно",
        "stock_coverage_days": "Покрытие, дней",
        "forecast_qty": "Прогноз спроса, шт.",
        "recommended_order_qty": "К заказу, шт.",
        "last_sale_date": "Последняя продажа",
        "days_since_last_sale": "Дней без продаж",
    }
    procurement_sheet = _prepare_excel_frame(
        scope_forecast,
        procurement_columns,
        procurement_rename_map,
    )
    reorder_sheet = _prepare_excel_frame(
        scope_risks.get("reorder", pd.DataFrame()),
        procurement_columns,
        procurement_rename_map,
    )
    shortage_sheet = _prepare_excel_frame(
        scope_risks.get("shortage", pd.DataFrame()),
        procurement_columns,
        procurement_rename_map,
    )
    overstock_sheet = _prepare_excel_frame(
        scope_risks.get("overstock", pd.DataFrame()),
        procurement_columns,
        procurement_rename_map,
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
    elif report_kind in {"brand", "supplier"}:
        detail_sheet = _prepare_excel_frame(
            filtered.sort_values("date", ascending=False),
            [
                "date",
                "salon",
                "item_code",
                "product",
                "category",
                "brand",
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
                "brand": "Бренд",
                "supplier": "Поставщик",
                "manager": "Менеджер",
                "quantity": "Количество",
                "revenue": "Выручка",
                "cost": "Себестоимость",
                "margin": "Валовая прибыль",
                "margin_pct": "Маржинальность, %",
            },
        )
        sheets.update(
            {
                "Портфель SKU": portfolio_sheet,
                "Динамика": monthly_sheet,
                "Остатки и прогноз": procurement_sheet,
                "К заказу": reorder_sheet,
                "Дефицит": shortage_sheet,
                "Излишки": overstock_sheet,
                "Продажи": detail_sheet,
            }
        )
    else:
        sheets.update({"Динамика": monthly_sheet, "Категории": category_sheet, "Портфель SKU": portfolio_sheet})

    scope_filename = brand or supplier or ""
    safe_scope_filename = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "_", scope_filename).strip("_")[:40]
    scope_suffix = f"_{safe_scope_filename}" if safe_scope_filename else ""
    filename = f"artdb_{report_kind}{scope_suffix}_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx"
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
    brand: str | None = None,
    supplier: str | None = None,
    with_file: bool = True,
    chat_id: str | None = None,
) -> int:
    try:
        visual_card = build_targeted_telegram_card(
            data,
            report_kind=report_kind,
            date_from=date_from,
            date_to=date_to,
            category=category,
            product_key=product_key,
            brand=brand,
            supplier=supplier,
        )
        send_telegram_photo(visual_card, chat_id=chat_id)
    except Exception as error:
        print(f"Telegram visual card error: {error}", flush=True)

    message, report_file = build_targeted_telegram_report(
        data,
        report_kind=report_kind,
        date_from=date_from,
        date_to=date_to,
        category=category,
        product_key=product_key,
        brand=brand,
        supplier=supplier,
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
    try:
        visual_card = build_daily_telegram_card()
        if visual_card is not None:
            send_telegram_photo(visual_card)
    except Exception as error:
        print(f"Telegram daily visual card error: {error}", flush=True)

    send_telegram_message(caption or build_daily_summary())
    sent_files = 0
    if with_files:
        for report_file in build_telegram_report_files():
            send_telegram_document(report_file)
            sent_files += 1
    return sent_files
