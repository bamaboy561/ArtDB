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
SKU_ALIASES_PATH = DATA_DIR / "sku_aliases.json"

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

SKU_ALIAS_COLUMNS = [
    "source_product_key",
    "source_product",
    "canonical_product_key",
    "canonical_product",
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


def _normalize_alias(record: dict[str, Any]) -> dict[str, Any]:
    source_product = _normalize_text(record.get("source_product"))
    canonical_product = _normalize_text(record.get("canonical_product"))
    return {
        "source_product_key": _normalize_text(record.get("source_product_key")) or source_product,
        "source_product": source_product,
        "canonical_product_key": _normalize_text(record.get("canonical_product_key")) or canonical_product,
        "canonical_product": canonical_product,
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
    if not SKU_ALIASES_PATH.exists():
        SKU_ALIASES_PATH.write_text("[]", encoding="utf-8")


def _load_json_records(path: Path = SKU_ATTRIBUTE_OVERRIDES_PATH) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = []
    return [record for record in payload if isinstance(record, dict)] if isinstance(payload, list) else []


def _write_json_records(
    records: list[dict[str, Any]],
    path: Path = SKU_ATTRIBUTE_OVERRIDES_PATH,
) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(
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


def load_sku_aliases(*, include_archived: bool = False) -> pd.DataFrame:
    ensure_sku_catalog_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        source_product_key,
                        source_product,
                        canonical_product_key,
                        canonical_product,
                        updated_by,
                        updated_at,
                        is_archived
                    FROM sku_aliases
                    WHERE %s OR is_archived = FALSE
                    ORDER BY LOWER(source_product), LOWER(source_product_key)
                    """,
                    (include_archived,),
                )
                rows = cursor.fetchall()
        records = [
            _normalize_alias({**row, "updated_at": isoformat_seconds(row.get("updated_at"))})
            for row in rows
        ]
        return pd.DataFrame(records, columns=SKU_ALIAS_COLUMNS)

    records = [_normalize_alias(record) for record in _load_json_records(SKU_ALIASES_PATH)]
    if not include_archived:
        records = [record for record in records if not record["is_archived"]]
    records.sort(key=lambda item: (item["source_product"].casefold(), item["source_product_key"].casefold()))
    return pd.DataFrame(records, columns=SKU_ALIAS_COLUMNS)


def _validated_alias_records(
    frame: pd.DataFrame,
    *,
    updated_by: str,
    existing_aliases: pd.DataFrame,
) -> list[dict[str, Any]]:
    updated_at = datetime.now().isoformat(timespec="seconds")
    records = [
        _normalize_alias(
            {
                **row,
                "updated_by": updated_by,
                "updated_at": updated_at,
                "is_archived": False,
            }
        )
        for row in frame.to_dict(orient="records")
    ]
    records = [
        record
        for record in records
        if record["source_product_key"] and record["canonical_product_key"]
    ]
    if not records:
        return []

    graph: dict[str, str] = {}
    if not existing_aliases.empty:
        active = existing_aliases.copy()
        if "is_archived" in active.columns:
            active = active[~active["is_archived"].fillna(False).astype(bool)]
        for row in active.to_dict(orient="records"):
            source = _normalize_text(row.get("source_product_key")).casefold()
            target = _normalize_text(row.get("canonical_product_key")).casefold()
            if source and target:
                graph[source] = target

    for record in records:
        source = record["source_product_key"].casefold()
        target = record["canonical_product_key"].casefold()
        if source == target:
            raise ValueError("Нельзя объединить SKU с самим собой.")
        graph[source] = target

    for start in graph:
        cursor = start
        visited: set[str] = set()
        while cursor in graph:
            if cursor in visited:
                raise ValueError("Объединение создаёт цикл между SKU. Выберите другой основной товар.")
            visited.add(cursor)
            cursor = graph[cursor]

    return records


def upsert_sku_aliases(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_sku_catalog_store()
    if frame.empty:
        return 0

    existing = load_sku_aliases(include_archived=True)
    records = _validated_alias_records(frame, updated_by=updated_by, existing_aliases=existing)
    if not records:
        return 0

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(
                        """
                        INSERT INTO sku_aliases (
                            source_product_key,
                            source_product,
                            canonical_product_key,
                            canonical_product,
                            updated_by,
                            updated_at,
                            is_archived
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                        ON CONFLICT (source_product_key) DO UPDATE SET
                            source_product = EXCLUDED.source_product,
                            canonical_product_key = EXCLUDED.canonical_product_key,
                            canonical_product = EXCLUDED.canonical_product,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = EXCLUDED.updated_at,
                            is_archived = FALSE
                        """,
                        (
                            record["source_product_key"],
                            record["source_product"],
                            record["canonical_product_key"],
                            record["canonical_product"],
                            record["updated_by"],
                            record["updated_at"],
                        ),
                    )
        return len(records)

    existing_records = {
        _normalize_text(record.get("source_product_key")).casefold(): _normalize_alias(record)
        for record in existing.to_dict(orient="records")
        if _normalize_text(record.get("source_product_key"))
    }
    for record in records:
        existing_records[record["source_product_key"].casefold()] = record
    merged = sorted(
        existing_records.values(),
        key=lambda item: (item["source_product"].casefold(), item["source_product_key"].casefold()),
    )
    _write_json_records(merged, SKU_ALIASES_PATH)
    return len(records)


def archive_sku_alias(source_product_key: str, *, updated_by: str) -> int:
    ensure_sku_catalog_store()
    normalized_key = _normalize_text(source_product_key)
    if not normalized_key:
        return 0

    updated_at = datetime.now().isoformat(timespec="seconds")
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sku_aliases
                    SET is_archived = TRUE, updated_by = %s, updated_at = %s
                    WHERE LOWER(source_product_key) = LOWER(%s) AND is_archived = FALSE
                    """,
                    (updated_by, updated_at, normalized_key),
                )
                return int(cursor.rowcount or 0)

    records = load_sku_aliases(include_archived=True).to_dict(orient="records")
    archived = 0
    for record in records:
        if _normalize_text(record.get("source_product_key")).casefold() == normalized_key.casefold():
            record["is_archived"] = True
            record["updated_by"] = updated_by
            record["updated_at"] = updated_at
            archived += 1
    _write_json_records([_normalize_alias(record) for record in records], SKU_ALIASES_PATH)
    return archived


def _active_alias_maps(aliases: pd.DataFrame) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    if aliases.empty:
        return {}, {}, {}
    active = aliases.copy()
    if "is_archived" in active.columns:
        active = active[~active["is_archived"].fillna(False).astype(bool)]

    key_map: dict[str, str] = {}
    product_map: dict[str, str] = {}
    canonical_names: dict[str, str] = {}
    for row in active.to_dict(orient="records"):
        source_key = _normalize_text(row.get("source_product_key")).casefold()
        source_product = _normalize_text(row.get("source_product")).casefold()
        canonical_key = _normalize_text(row.get("canonical_product_key"))
        canonical_product = _normalize_text(row.get("canonical_product"))
        if not source_key or not canonical_key:
            continue
        key_map[source_key] = canonical_key
        if source_product:
            product_map[source_product] = canonical_key
        if canonical_product:
            canonical_names[canonical_key.casefold()] = canonical_product
    return key_map, product_map, canonical_names


def _resolve_alias_key(value: Any, key_map: dict[str, str]) -> str:
    current = _normalize_text(value)
    visited: set[str] = set()
    while current.casefold() in key_map and current.casefold() not in visited:
        visited.add(current.casefold())
        current = key_map[current.casefold()]
    return current


def _first_non_empty(series: pd.Series) -> Any:
    for value in series:
        if _normalize_text(value):
            return value
    return ""


def apply_sku_aliases(
    data: pd.DataFrame,
    aliases: pd.DataFrame,
    *,
    aggregate_inventory: bool = False,
) -> pd.DataFrame:
    if data.empty or aliases.empty or "product" not in data.columns:
        return data

    key_map, product_map, canonical_names = _active_alias_maps(aliases)
    if not key_map:
        return data

    enriched = data.copy()
    source_keys = (
        enriched["product_key"]
        if "product_key" in enriched.columns
        else enriched["product"]
    ).fillna("").astype(str).str.strip()
    product_keys = enriched["product"].fillna("").astype(str).str.strip().str.casefold()
    direct_targets = source_keys.str.casefold().map(key_map)
    direct_targets = direct_targets.where(direct_targets.notna(), product_keys.map(product_map))
    resolved_targets = direct_targets.map(
        lambda value: _resolve_alias_key(value, key_map) if _normalize_text(value) else ""
    )
    aliased_mask = resolved_targets.ne("")
    if not aliased_mask.any():
        return enriched

    original_key_series = (
        enriched["product_key"]
        if "product_key" in enriched.columns
        else enriched["product"]
    ).fillna("").astype(str).str.strip().str.casefold()
    canonical_rows = enriched.assign(_catalog_key=original_key_series).drop_duplicates("_catalog_key", keep="last")
    canonical_rows = canonical_rows.set_index("_catalog_key")

    if "product_key" in enriched.columns:
        enriched.loc[aliased_mask, "product_key"] = resolved_targets.loc[aliased_mask]

    canonical_key_norm = resolved_targets.str.casefold()
    canonical_product_lookup = canonical_rows["product"].to_dict()
    mapped_product = canonical_key_norm.map(canonical_product_lookup)
    fallback_product = canonical_key_norm.map(canonical_names)
    mapped_product = mapped_product.where(
        mapped_product.fillna("").astype(str).str.strip().ne(""),
        fallback_product,
    )
    mapped_product = mapped_product.where(
        mapped_product.fillna("").astype(str).str.strip().ne(""),
        resolved_targets,
    )
    enriched.loc[aliased_mask, "product"] = mapped_product.loc[aliased_mask]

    for column in ("item_code", "category", "brand", "supplier"):
        if column not in enriched.columns or column not in canonical_rows.columns:
            continue
        mapped = canonical_key_norm.map(canonical_rows[column].to_dict())
        replace_mask = aliased_mask & mapped.fillna("").astype(str).str.strip().ne("")
        enriched.loc[replace_mask, column] = mapped.loc[replace_mask]

    if not aggregate_inventory:
        return enriched

    numeric_sums = [
        column
        for column in ("stock_on_hand", "stock_value", "stock_in_transit")
        if column in enriched.columns
    ]
    if not numeric_sums:
        return enriched
    for column in numeric_sums:
        enriched[column] = pd.to_numeric(enriched[column], errors="coerce").fillna(0.0)

    aggregation: dict[str, Any] = {column: "sum" for column in numeric_sums}
    for column in enriched.columns:
        if column == "product" or column in aggregation:
            continue
        aggregation[column] = _first_non_empty
    return enriched.groupby("product", as_index=False, dropna=False).agg(aggregation)
