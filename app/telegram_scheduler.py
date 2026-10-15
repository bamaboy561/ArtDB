from __future__ import annotations

from datetime import datetime
import os
import threading
import time

from db import claim_service_run, database_enabled, get_service_state
from telegram_reports import get_timezone, send_telegram_report_pack
from telegram_settings_store import TelegramSettings, load_telegram_settings


SERVICE_NAME = "telegram-daily-summary"
_START_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None


def run_scheduled_report_if_due(now: datetime, settings: TelegramSettings) -> bool:
    if not (settings.daily_enabled and settings.configured):
        return False
    if (now.hour, now.minute) < (settings.report_hour, settings.report_minute):
        return False
    run_key = now.strftime("%Y-%m-%d")
    if get_service_state(SERVICE_NAME) == run_key:
        return False
    # The day is claimed before sending: a second instance or a failed upload
    # must never send the same daily report again.
    if not claim_service_run(SERVICE_NAME, run_key):
        return False
    send_telegram_report_pack(with_files=settings.send_report_files)
    return True


def _scheduler_loop() -> None:
    while True:
        check_interval = 60
        try:
            run_scheduled_report_if_due(datetime.now(get_timezone()), load_telegram_settings())
        except Exception as error:
            print(f"Telegram scheduler error: {error}", flush=True)
            check_interval = 120
        time.sleep(check_interval)


def start_telegram_scheduler() -> bool:
    global _SCHEDULER_THREAD
    scheduler_enabled = os.getenv("TELEGRAM_EMBEDDED_SCHEDULER", "true").strip().casefold()
    if scheduler_enabled in {"0", "false", "no", "off"} or not database_enabled():
        return False

    with _START_LOCK:
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return True
        _SCHEDULER_THREAD = threading.Thread(
            target=_scheduler_loop,
            name="artdb-telegram-scheduler",
            daemon=True,
        )
        _SCHEDULER_THREAD.start()
    return True
