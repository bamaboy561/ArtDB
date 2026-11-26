"""The network's own calendar day.

The server's clock is UTC, so date.today() turned over at 6:00 local time: uploads and
orders made earlier got yesterday's date, and the morning report listed those salons as
not uploaded. Calendar dates use the reports' time zone instead.
"""
from __future__ import annotations

from datetime import date, datetime
import os
from zoneinfo import ZoneInfo


def app_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TIMEZONE", os.getenv("TZ", "Asia/Omsk")))


def local_today() -> date:
    return datetime.now(app_timezone()).date()
