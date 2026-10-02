from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from db import database_enabled, ensure_database_ready, get_db_connection, isoformat_seconds


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR.parent / "data"))).resolve()
SKU_ATTRIBUTE_OVERRIDES_PATH = DATA_DIR / "sku_attribute_overrides.json"

SKU_OVERRIDE_COLUMNS = [
    "product_key",
    "product",
    "category",
    "brand",
    "item_code",
    "updated_by",
    "updated_at",
    "is_archived",
]


def _normalize_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "не назначен", "не указана", "без категории"} else text


def _normalize_override(record: dict[str, Any]) -> dict[str, Any]:
    product = _normalize_text(record.get("product"))
    product_key = _normalize_text(record.get("product_key")) or product
    return {
        "product_key": product_key,
        "product": product,
        "category": _normalize_text(record.get("category")),
        "brand": _normalize_text(record.get("brand")),
        "item_code": _normalize_text(record.get("item_code")),
        "updated_by": _normalize_text(record.get("updated_by")),
        "updated_at": _normalize_text(record.get("updated_at")) or datetime.now().isoformat(timespec="seconds"),
        "is_archived": bool(record.get("is_archived", False)),
    }


def ensure_sku_catalog_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return

    DATA_DIR.mkdir(exist_ok=True)
    if not SKU_ATTRIBUTE_OVERRIDES_PATH.exists():
        SKU_ATTRIBUTE_OVERRIDES_PATH.write_text("[]", encoding="utf-8")


def _load_json_records() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SKU_ATTRIBUTE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = []
    return [record for record in payload if isinstance(record, dict)] if isinstance(payload, list) else []


def _write_json_records(records: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    SKU_ATTRIBUTE_OVERRIDES_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_sku_attribute_overrides(*, include_archived: bool = False) -> pd.DataFrame:
    ensure_sku_catalog_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        product_key,
                        product,
                        category,
                        brand,
                        item_code,
                        updated_by,
                        updated_at,
                        is_archived
                    FROM sku_attribute_overrides
                    WHERE %s OR is_archived = FALSE
                    ORDER BY LOWER(product), LOWER(product_key)
                    """,
                    (include_archived,),
                )
                rows = cursor.fetchall()
        records = [
            _normalize_override(
                {
                    **row,
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                }
            )
            for row in rows
        ]
        return pd.DataFrame(records, columns=SKU_OVERRIDE_COLUMNS)

    records = [_normalize_override(record) for record in _load_json_records()]
    if not include_archived:
        records = [record for record in records if not record["is_archived"]]
    records.sort(key=lambda item: (item["product"].casefold(), item["product_key"].casefold()))
    return pd.DataFrame(records, columns=SKU_OVERRIDE_COLUMNS)


def upsert_sku_attribute_overrides(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_sku_catalog_store()
    if frame.empty:
        return 0

    updated_at = datetime.now().isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        normalized = _normalize_override(
            {
                **row,
                "updated_by": updated_by,
                "updated_at": updated_at,
                "is_archived": False,
            }
        )
        if normalized["product_key"]:
            records.append(normalized)

    if not records:
        return 0

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(
                        """
                        INSERT INTO sku_attribute_overrides (
                            product_key,
                            product,
                            category,
                            brand,
                            item_code,
                            updated_by,
                            updated_at,
                            is_archived
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
                        ON CONFLICT (product_key) DO UPDATE SET
                            product = EXCLUDED.product,
                            category = EXCLUDED.category,
                            brand = EXCLUDED.brand,
                            item_code = EXCLUDED.item_code,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = EXCLUDED.updated_at,
                            is_archived = FALSE
                        """,
                        (
                            record["product_key"],
                            record["product"],
                            record["category"],
                            record["brand"],
                            record["item_code"],
                            record["updated_by"],
                            record["updated_at"],
                        ),
                    )
        return len(records)

    existing = {
        record["product_key"].casefold(): record
        for record in load_sku_attribute_overrides(include_archived=True).to_dict(orient="records")
        if str(record.get("product_key", "")).strip()
    }
    for record in records:
        existing[record["product_key"].casefold()] = record
    merged = sorted(existing.values(), key=lambda item: (item["product"].casefold(), item["product_key"].casefold()))
    _write_json_records(merged)
    return len(records)


def apply_sku_attribute_overrides(data: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    if data.empty or overrides.empty or "product_key" not in overrides.columns:
        return data

    enriched = data.copy()
    if "product" not in enriched.columns:
        return enriched

    active = overrides.copy()
    if "is_archived" in active.columns:
        active = active[~active["is_archived"].fillna(False).astype(bool)].copy()
    active["_override_key"] = active["product_key"].fillna("").astype(str).str.strip().str.casefold()
    if "product" not in active.columns:
        active["product"] = ""
    active["_product_key"] = active["product"].fillna("").astype(str).str.strip().str.casefold()
    active = active[active["_override_key"].ne("")].drop_duplicates("_override_key", keep="last")
    if active.empty:
        return enriched

    source_key = (
        enriched["product_key"]
        if "product_key" in enriched.columns
        else enriched["product"]
    ).fillna("").astype(str).str.strip().str.casefold()
    product_key = enriched["product"].fillna("").astype(str).str.strip().str.casefold()

    for column in ("category", "brand", "item_code"):
        if column not in enriched.columns:
            enriched[column] = ""
        if column not in active.columns:
            active[column] = ""
        active[column] = active[column].fillna("").astype(str).str.strip()
        key_lookup = active.set_index("_override_key")[column].to_dict()
        product_lookup = (
            active[active["_product_key"].ne("")]
            .drop_duplicates("_product_key", keep="last")
            .set_index("_product_key")[column]
            .to_dict()
        )
        mapped = source_key.map(key_lookup)
        mapped = mapped.where(mapped.fillna("").astype(str).str.strip().ne(""), product_key.map(product_lookup))
        update_mask = mapped.fillna("").astype(str).str.strip().ne("")
        enriched.loc[update_mask, column] = mapped.loc[update_mask].astype(str).str.strip()

    return enriched
