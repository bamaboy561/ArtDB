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


@dataclass(frozen=True)
class CleanupPriority:
    label: str
    rank: int


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


def classify_cleanup_priority(
    issue_count: int,
    revenue_impact_pct: float,
) -> CleanupPriority:
    issues = max(int(issue_count or 0), 0)
    impact = max(float(revenue_impact_pct or 0.0), 0.0)

    if issues >= 3 or (issues >= 2 and impact >= 5.0):
        return CleanupPriority(label="Критично", rank=1)
    if issues >= 2 or impact >= 2.0:
        return CleanupPriority(label="Высокий", rank=2)
    if impact >= 0.5:
        return CleanupPriority(label="Средний", rank=3)
    return CleanupPriority(label="Низкий", rank=4)


def _clean_catalog_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "не назначен", "не указана", "без категории"} else text


def build_sku_change_preview(
    edited_rows: pd.DataFrame,
    *,
    bulk_supplier: str = "",
    bulk_category: str = "",
    bulk_brand: str = "",
) -> pd.DataFrame:
    columns = [
        "product_key",
        "product",
        "changed_fields",
        "before_supplier",
        "after_supplier",
        "before_category",
        "after_category",
        "before_brand",
        "after_brand",
        "before_item_code",
        "after_item_code",
    ]
    if edited_rows.empty or "select" not in edited_rows.columns:
        return pd.DataFrame(columns=columns)

    selected = edited_rows[edited_rows["select"].fillna(False).astype(bool)].copy()
    if selected.empty:
        return pd.DataFrame(columns=columns)

    bulk_values = {
        "supplier": _clean_catalog_value(bulk_supplier),
        "category": _clean_catalog_value(bulk_category),
        "brand": _clean_catalog_value(bulk_brand),
    }
    records: list[dict[str, str]] = []
    for row in selected.to_dict(orient="records"):
        before = {
            field: _clean_catalog_value(row.get(f"current_{field}"))
            for field in ("supplier", "category", "brand", "item_code")
        }
        after = {
            field: bulk_values.get(field) or _clean_catalog_value(row.get(f"new_{field}")) or before[field]
            for field in ("supplier", "category", "brand", "item_code")
        }
        changed = [
            field
            for field in ("supplier", "category", "brand", "item_code")
            if before[field].casefold() != after[field].casefold()
        ]
        if not changed:
            continue
        records.append(
            {
                "product_key": _clean_catalog_value(row.get("product_key")),
                "product": _clean_catalog_value(row.get("product")),
                "changed_fields": ", ".join(changed),
                **{f"before_{field}": before[field] for field in before},
                **{f"after_{field}": after[field] for field in after},
            }
        )

    return pd.DataFrame(records, columns=columns)
