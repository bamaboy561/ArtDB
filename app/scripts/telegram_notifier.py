from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import database_enabled, get_service_state, set_service_state
from salon_data_store import load_archive_data, load_manifest, load_salons
from sales_analytics import build_monthly_summary, build_overview_metrics, build_product_summary


SERVICE_NAME = "telegram-daily-summary"


def get_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TIMEZONE", os.getenv("TZ", "Asia/Omsk")))


def send_telegram_message(text: str) -> None:
    token = os.getenv("TG_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = os.getenv("TG_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat_id:
        raise RuntimeError("Нужны TG_BOT_TOKEN и TG_CHAT_ID.")

    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    telegram_url = f"https://api.telegram.org/bot{token}/sendMessage"
    with request.urlopen(telegram_url, data=payload, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")


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

    summary_lines = [
        "<b>Ежедневная сводка сети</b>",
        f"Дата: {today.strftime('%d.%m.%Y')}",
        f"Салонов в системе: {len(salons)}",
        f"С загрузкой за сегодня: {len(uploaded_salons)}",
    ]

    if missing_salons:
        summary_lines.append("Без загрузки сегодня:")
        summary_lines.extend(f"• {salon}" for salon in missing_salons)
    else:
        summary_lines.append("Все салоны загрузили данные за сегодня.")

    archive_result = load_archive_data(salons=salons if salons else None)
    if not archive_result.data.empty:
        monthly_summary = build_monthly_summary(archive_result.data)
        overview = build_overview_metrics(archive_result.data)
        product_summary = build_product_summary(archive_result.data)
        latest_month = monthly_summary.iloc[-1]
        risk_count = int((product_summary["margin_pct"].fillna(9999) < 15).sum()) if "margin_pct" in product_summary.columns else 0
        summary_lines.extend(
            [
                "",
                f"Последний месяц: {latest_month['month_label']}",
                f"Выручка: {overview['total_revenue']:,.0f} сом".replace(",", " "),
                f"Маржа: {overview['total_margin']:,.0f} сом".replace(",", " "),
                f"Риск по марже (<15%): {risk_count}",
            ]
        )

    if archive_result.warnings:
        summary_lines.extend(["", "Предупреждения архива:"])
        summary_lines.extend(f"• {warning}" for warning in archive_result.warnings[:5])

    return "\n".join(summary_lines)


def run_once() -> None:
    send_telegram_message(build_daily_summary())


def run_daemon() -> None:
    if not database_enabled():
        raise RuntimeError("Для daemon-режима Telegram нужен DATABASE_URL и PostgreSQL-хранилище.")

    timezone = get_timezone()
    report_hour = int(os.getenv("TELEGRAM_DAILY_REPORT_HOUR", "9"))
    report_minute = int(os.getenv("TELEGRAM_DAILY_REPORT_MINUTE", "0"))
    check_interval = max(30, int(os.getenv("TELEGRAM_CHECK_INTERVAL_SECONDS", "60")))

    while True:
        now = datetime.now(timezone)
        run_key = now.strftime("%Y-%m-%d")
        already_sent = get_service_state(SERVICE_NAME)
        if (
            now.hour > report_hour
            or (now.hour == report_hour and now.minute >= report_minute)
        ) and already_sent != run_key:
            send_telegram_message(build_daily_summary())
            set_service_state(SERVICE_NAME, run_key)
        time.sleep(check_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram notifications for sales analytics.")
    parser.add_argument("mode", choices=["once", "daemon", "test"], nargs="?", default="once")
    parser.add_argument("--message", default="Тестовое сообщение из sales analytics.", help="Custom test message.")
    args = parser.parse_args()

    if args.mode == "test":
        send_telegram_message(args.message)
        return 0
    if args.mode == "daemon":
        run_daemon()
        return 0

    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
