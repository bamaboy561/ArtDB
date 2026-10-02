from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FreshnessStatus:
    label: str
    tone: str
    age_days: int | None
    display_date: str


def evaluate_freshness(
    value: Any,
    *,
    today: date | None = None,
    fresh_days: int = 1,
    warning_days: int = 3,
) -> FreshnessStatus:
    current_date = today or date.today()
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return FreshnessStatus(
            label="Нет данных",
            tone="danger",
            age_days=None,
            display_date="—",
        )

    source_date = parsed.date()
    age_days = max((current_date - source_date).days, 0)
    if age_days <= max(fresh_days, 0):
        label = "Актуально"
        tone = "success"
    elif age_days <= max(warning_days, fresh_days):
        label = "Пора обновить"
        tone = "warning"
    else:
        label = "Устарело"
        tone = "danger"

    return FreshnessStatus(
        label=label,
        tone=tone,
        age_days=age_days,
        display_date=source_date.strftime("%d.%m.%Y"),
    )
