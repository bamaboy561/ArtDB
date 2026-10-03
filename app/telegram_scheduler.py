from __future__ import annotations

from datetime import datetime
import os
import threading
import time

from db import database_enabled, get_service_state, set_service_state
from telegram_reports import get_timezone, send_telegram_report_pack
from telegram_settings_store import load_telegram_settings


SERVICE_NAME = "telegram-daily-summary"
_START_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None


def _scheduler_loop() -> None:
    while True:
        check_interval = 60
        try:
            settings = load_telegram_settings()
            if settings.daily_enabled and settings.configured:
                now = datetime.now(get_timezone())
                run_key = now.strftime("%Y-%m-%d")
                scheduled = (settings.report_hour, settings.report_minute)
                if (now.hour, now.minute) >= scheduled and get_service_state(SERVICE_NAME) != run_key:
                    send_telegram_report_pack(with_files=settings.send_report_files)
                    set_service_state(SERVICE_NAME, run_key)
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
