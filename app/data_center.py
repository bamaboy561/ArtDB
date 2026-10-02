from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations
import re
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


def _normalized_catalog_token(value: Any) -> str:
    text = _clean_catalog_value(value).casefold().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()


def build_sku_catalog_summary(data: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "product_key",
        "product",
        "item_code",
        "category",
        "brand",
        "supplier",
        "revenue",
        "quantity",
        "line_count",
    ]
    if data.empty or "product" not in data.columns:
        return pd.DataFrame(columns=columns)

    working = data.copy()
    if "product_key" not in working.columns:
        working["product_key"] = working["product"]
    for column in ("item_code", "category", "brand", "supplier"):
        if column not in working.columns:
            working[column] = ""
    for column in ("revenue", "quantity"):
        if column not in working.columns:
            working[column] = 0.0
        working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0.0)
    working["product_key"] = working["product_key"].map(_clean_catalog_value)
    working = working[working["product_key"].ne("")]
    if working.empty:
        return pd.DataFrame(columns=columns)

    def first_value(series: pd.Series) -> str:
        return next((_clean_catalog_value(value) for value in series if _clean_catalog_value(value)), "")

    summary = (
        working.groupby("product_key", dropna=False)
        .agg(
            product=("product", first_value),
            item_code=("item_code", first_value),
            category=("category", first_value),
            brand=("brand", first_value),
            supplier=("supplier", first_value),
            revenue=("revenue", "sum"),
            quantity=("quantity", "sum"),
            line_count=("product", "size"),
        )
        .reset_index()
    )
    return summary[columns].sort_values(["revenue", "product"], ascending=[False, True]).reset_index(drop=True)


def build_sku_alias_candidates(
    data: pd.DataFrame,
    aliases: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "source_product_key",
        "source_product",
        "source_revenue",
        "canonical_product_key",
        "canonical_product",
        "canonical_revenue",
        "combined_revenue",
        "reason",
    ]
    summary = build_sku_catalog_summary(data)
    if len(summary) < 2:
        return pd.DataFrame(columns=columns)

    active_source_keys: set[str] = set()
    if aliases is not None and not aliases.empty and "source_product_key" in aliases.columns:
        active = aliases.copy()
        if "is_archived" in active.columns:
            active = active[~active["is_archived"].fillna(False).astype(bool)]
        active_source_keys = {
            _clean_catalog_value(value).casefold()
            for value in active["source_product_key"]
            if _clean_catalog_value(value)
        }

    summary = summary[
        ~summary["product_key"].fillna("").astype(str).str.strip().str.casefold().isin(active_source_keys)
    ].copy()
    summary["_name_token"] = summary["product"].map(_normalized_catalog_token)
    summary["_code_token"] = summary["item_code"].map(_normalized_catalog_token)

    suggestions: dict[tuple[str, str], dict[str, Any]] = {}
    match_rules = [
        ("_code_token", "Совпадает код товара", 1),
        ("_name_token", "Совпадает нормализованное название", 2),
    ]
    for token_column, reason, reason_rank in match_rules:
        eligible = summary[summary[token_column].ne("")]
        for _, group in eligible.groupby(token_column, sort=False):
            if len(group) < 2:
                continue
            rows = group.to_dict(orient="records")
            for left, right in combinations(rows, 2):
                left_key = _clean_catalog_value(left.get("product_key"))
                right_key = _clean_catalog_value(right.get("product_key"))
                if not left_key or not right_key or left_key.casefold() == right_key.casefold():
                    continue
                ranked = sorted(
                    [left, right],
                    key=lambda row: (
                        -float(row.get("revenue", 0.0) or 0.0),
                        _clean_catalog_value(row.get("product")).casefold(),
                    ),
                )
                canonical, source = ranked[0], ranked[1]
                pair_key = (
                    _clean_catalog_value(source.get("product_key")).casefold(),
                    _clean_catalog_value(canonical.get("product_key")).casefold(),
                )
                existing = suggestions.get(pair_key)
                if existing and int(existing["_reason_rank"]) <= reason_rank:
                    continue
                suggestions[pair_key] = {
                    "source_product_key": _clean_catalog_value(source.get("product_key")),
                    "source_product": _clean_catalog_value(source.get("product")),
                    "source_revenue": float(source.get("revenue", 0.0) or 0.0),
                    "canonical_product_key": _clean_catalog_value(canonical.get("product_key")),
                    "canonical_product": _clean_catalog_value(canonical.get("product")),
                    "canonical_revenue": float(canonical.get("revenue", 0.0) or 0.0),
                    "combined_revenue": float(source.get("revenue", 0.0) or 0.0)
                    + float(canonical.get("revenue", 0.0) or 0.0),
                    "reason": reason,
                    "_reason_rank": reason_rank,
                }

    records = list(suggestions.values())
    records.sort(key=lambda row: (int(row["_reason_rank"]), -float(row["combined_revenue"])))
    for record in records:
        record.pop("_reason_rank", None)
    return pd.DataFrame(records, columns=columns)


def build_sku_merge_preview(
    data: pd.DataFrame,
    *,
    source_product_key: str,
    canonical_product_key: str,
) -> pd.DataFrame:
    columns = [
        "source_product_key",
        "source_product",
        "source_revenue",
        "source_lines",
        "canonical_product_key",
        "canonical_product",
        "canonical_revenue",
        "canonical_lines",
        "combined_revenue",
        "combined_lines",
    ]
    summary = build_sku_catalog_summary(data)
    if summary.empty:
        return pd.DataFrame(columns=columns)

    normalized_keys = summary["product_key"].fillna("").astype(str).str.strip().str.casefold()
    source_rows = summary[normalized_keys.eq(_clean_catalog_value(source_product_key).casefold())]
    canonical_rows = summary[normalized_keys.eq(_clean_catalog_value(canonical_product_key).casefold())]
    if source_rows.empty or canonical_rows.empty:
        return pd.DataFrame(columns=columns)

    source = source_rows.iloc[0]
    canonical = canonical_rows.iloc[0]
    if str(source["product_key"]).casefold() == str(canonical["product_key"]).casefold():
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "source_product_key": source["product_key"],
                "source_product": source["product"],
                "source_revenue": float(source["revenue"]),
                "source_lines": int(source["line_count"]),
                "canonical_product_key": canonical["product_key"],
                "canonical_product": canonical["product"],
                "canonical_revenue": float(canonical["revenue"]),
                "canonical_lines": int(canonical["line_count"]),
                "combined_revenue": float(source["revenue"]) + float(canonical["revenue"]),
                "combined_lines": int(source["line_count"]) + int(canonical["line_count"]),
            }
        ],
        columns=columns,
    )
