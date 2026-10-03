from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import database_enabled, get_service_state, set_service_state
from telegram_reports import (
    build_daily_summary,
    build_risk_alert_message,
    get_timezone,
    send_telegram_message,
    send_telegram_report_pack,
    send_supplier_order_files,
)
from telegram_settings_store import load_telegram_settings


SERVICE_NAME = "telegram-daily-summary"


def run_once(*, with_files: bool = False) -> None:
    if with_files:
        send_telegram_report_pack(with_files=True)
        return
    send_telegram_message(build_daily_summary())


def run_daemon() -> None:
    if not database_enabled():
        raise RuntimeError("Для daemon-режима Telegram нужен DATABASE_URL и PostgreSQL-хранилище.")

    timezone = get_timezone()
    check_interval = max(30, int(os.getenv("TELEGRAM_CHECK_INTERVAL_SECONDS", "60")))

    while True:
        settings = load_telegram_settings()
        now = datetime.now(timezone)
        run_key = now.strftime("%Y-%m-%d")
        already_sent = get_service_state(SERVICE_NAME)
        if (
            settings.daily_enabled
            and settings.configured
            and (
                now.hour > settings.report_hour
                or (now.hour == settings.report_hour and now.minute >= settings.report_minute)
            )
            and already_sent != run_key
        ):
            send_telegram_report_pack(with_files=settings.send_report_files)
            set_service_state(SERVICE_NAME, run_key)
        time.sleep(check_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram reports for ArtDB analytics.")
    parser.add_argument("mode", choices=["once", "daemon", "test", "report", "alerts", "orders"], nargs="?", default="once")
    parser.add_argument("--message", default="Тестовое сообщение из ArtDB.", help="Custom test message.")
    parser.add_argument("--with-files", action="store_true", help="Attach CSV report files.")
    args = parser.parse_args()

    if args.mode == "test":
        send_telegram_message(args.message)
        return 0
    if args.mode == "daemon":
        run_daemon()
        return 0
    if args.mode == "report":
        send_telegram_report_pack(with_files=True)
        return 0
    if args.mode == "alerts":
        send_telegram_message(build_risk_alert_message())
        return 0
    if args.mode == "orders":
        sent_files = send_supplier_order_files()
        if sent_files == 0:
            send_telegram_message("ArtDB: сейчас нет позиций к заказу по прогнозу потребности.")
        return 0

    run_once(with_files=args.with_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
