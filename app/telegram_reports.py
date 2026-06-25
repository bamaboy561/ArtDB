from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import json
import os
import secrets
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

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
    build_monthly_summary,
    build_overview_metrics,
    build_product_summary,
    to_csv_bytes,
)


@dataclass(frozen=True)
class TelegramReportFile:
    filename: str
    content: bytes
    caption: str = ""
    content_type: str = "text/csv"


def get_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TIMEZONE", os.getenv("TZ", "Asia/Omsk")))


def telegram_is_configured() -> bool:
    token, chat_id = _get_telegram_credentials()
    return bool(token and chat_id)


def _get_telegram_credentials() -> tuple[str, str]:
    token = os.getenv("TG_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = os.getenv("TG_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    return token, chat_id


def _telegram_api_request(method: str, data: bytes, headers: dict[str, str] | None = None) -> dict[str, object]:
    token, chat_id = _get_telegram_credentials()
    if not token or not chat_id:
        raise RuntimeError("Нужны TG_BOT_TOKEN и TG_CHAT_ID.")

    telegram_url = f"https://api.telegram.org/bot{token}/{method}"
    api_request = request.Request(telegram_url, data=data, headers=headers or {})
    with request.urlopen(api_request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")
    return body


def send_telegram_message(text: str) -> None:
    _, chat_id = _get_telegram_credentials()
    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    _telegram_api_request(
        "sendMessage",
        payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


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


def send_telegram_document(report_file: TelegramReportFile) -> None:
    _, chat_id = _get_telegram_credentials()
    fields = {
        "chat_id": chat_id,
        "caption": report_file.caption[:1024],
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
                f"Риск по марже (<15%): {risk_count}",
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


def _export_frame(frame: pd.DataFrame, columns: list[str], rename_map: dict[str, str]) -> bytes:
    export_columns = [column for column in columns if column in frame.columns]
    if not export_columns:
        return to_csv_bytes(pd.DataFrame())

    export_frame = frame[export_columns].copy()
    for column in export_frame.columns:
        if pd.api.types.is_datetime64_any_dtype(export_frame[column]):
            export_frame[column] = pd.to_datetime(export_frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    return to_csv_bytes(export_frame.rename(columns=rename_map))


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

    return report_files


def send_telegram_report_pack(*, with_files: bool = True, caption: str | None = None) -> int:
    send_telegram_message(caption or build_daily_summary())
    sent_files = 0
    if with_files:
        for report_file in build_telegram_report_files():
            send_telegram_document(report_file)
            sent_files += 1
    return sent_files

