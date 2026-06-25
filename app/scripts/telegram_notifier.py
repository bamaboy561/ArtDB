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
    get_timezone,
    send_telegram_message,
    send_telegram_report_pack,
)


SERVICE_NAME = "telegram-daily-summary"


def env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().casefold()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def run_once(*, with_files: bool = False) -> None:
    if with_files:
        send_telegram_report_pack(with_files=True)
        return
    send_telegram_message(build_daily_summary())


def run_daemon() -> None:
    if not database_enabled():
        raise RuntimeError("Для daemon-режима Telegram нужен DATABASE_URL и PostgreSQL-хранилище.")

    timezone = get_timezone()
    report_hour = int(os.getenv("TELEGRAM_DAILY_REPORT_HOUR", "9"))
    report_minute = int(os.getenv("TELEGRAM_DAILY_REPORT_MINUTE", "0"))
    check_interval = max(30, int(os.getenv("TELEGRAM_CHECK_INTERVAL_SECONDS", "60")))
    send_files = env_flag("TELEGRAM_SEND_REPORT_FILES", default=False)

    while True:
        now = datetime.now(timezone)
        run_key = now.strftime("%Y-%m-%d")
        already_sent = get_service_state(SERVICE_NAME)
        if (
            now.hour > report_hour
            or (now.hour == report_hour and now.minute >= report_minute)
        ) and already_sent != run_key:
            send_telegram_report_pack(with_files=send_files)
            set_service_state(SERVICE_NAME, run_key)
        time.sleep(check_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram reports for ArtDB analytics.")
    parser.add_argument("mode", choices=["once", "daemon", "test", "report"], nargs="?", default="once")
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

    run_once(with_files=args.with_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
