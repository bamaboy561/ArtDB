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
PROCUREMENT_ITEMS_PATH = DATA_DIR / "procurement_items.json"
INVENTORY_SNAPSHOTS_PATH = DATA_DIR / "inventory_snapshots.json"
PROCUREMENT_COLUMNS = [
    "product",
    "supplier",
    "stock_on_hand",
    "stock_in_transit",
    "min_order_qty",
    "order_multiple",
    "lead_time_days",
    "notes",
    "updated_by",
    "updated_at",
]
PROCUREMENT_EDITABLE_FIELDS = {
    "supplier",
    "stock_on_hand",
    "stock_in_transit",
    "min_order_qty",
    "order_multiple",
    "lead_time_days",
    "notes",
}


def ensure_procurement_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return
    DATA_DIR.mkdir(exist_ok=True)
    if not PROCUREMENT_ITEMS_PATH.exists():
        PROCUREMENT_ITEMS_PATH.write_text("[]", encoding="utf-8")
    if not INVENTORY_SNAPSHOTS_PATH.exists():
        INVENTORY_SNAPSHOTS_PATH.write_text("[]", encoding="utf-8")


def _normalize_inventory_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_value": _safe_float(record.get("total_value", 0)),
        "item_count": _safe_int(record.get("item_count", 0)),
        "filename": str(record.get("filename", "")).strip(),
        "uploaded_by": str(record.get("uploaded_by", "")).strip(),
        "uploaded_at": (
            str(record.get("uploaded_at", "")).strip()
            or datetime.now().isoformat(timespec="seconds")
        ),
    }


def save_inventory_snapshot(
    *,
    total_value: float,
    item_count: int,
    filename: str,
    uploaded_by: str,
) -> dict[str, Any]:
    ensure_procurement_store()
    snapshot = _normalize_inventory_snapshot(
        {
            "total_value": total_value,
            "item_count": item_count,
            "filename": filename,
            "uploaded_by": uploaded_by,
        }
    )

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO inventory_snapshots (
                        total_value,
                        item_count,
                        filename,
                        uploaded_by
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING total_value, item_count, filename, uploaded_by, uploaded_at
                    """,
                    (
                        snapshot["total_value"],
                        snapshot["item_count"],
                        snapshot["filename"],
                        snapshot["uploaded_by"],
                    ),
                )
                saved = cursor.fetchone()
        return _normalize_inventory_snapshot(
            {
                **saved,
                "uploaded_at": isoformat_seconds(saved.get("uploaded_at")),
            }
        )

    try:
        payload = json.loads(INVENTORY_SNAPSHOTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    snapshots = payload if isinstance(payload, list) else []
    snapshots.append(snapshot)
    INVENTORY_SNAPSHOTS_PATH.write_text(
        json.dumps(snapshots[-365:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snapshot


def load_latest_inventory_snapshot() -> dict[str, Any] | None:
    ensure_procurement_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT total_value, item_count, filename, uploaded_by, uploaded_at
                    FROM inventory_snapshots
                    ORDER BY uploaded_at DESC, snapshot_id DESC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return _normalize_inventory_snapshot(
            {
                **row,
                "uploaded_at": isoformat_seconds(row.get("uploaded_at")),
            }
        )

    try:
        payload = json.loads(INVENTORY_SNAPSHOTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    if not isinstance(payload, list) or not payload:
        return None
    candidates = [
        _normalize_inventory_snapshot(record)
        for record in payload
        if isinstance(record, dict)
    ]
    return candidates[-1] if candidates else None


def _safe_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric >= 0 else 0.0


def _safe_int(value: Any) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return 0
    return numeric if numeric >= 0 else 0


def _normalize_procurement_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "product": str(record.get("product", "")).strip(),
        "supplier": str(record.get("supplier", "")).strip(),
        "stock_on_hand": _safe_float(record.get("stock_on_hand", 0)),
        "stock_in_transit": _safe_float(record.get("stock_in_transit", 0)),
        "min_order_qty": _safe_float(record.get("min_order_qty", 0)),
        "order_multiple": _safe_float(record.get("order_multiple", 0)),
        "lead_time_days": _safe_int(record.get("lead_time_days", 0)),
        "notes": str(record.get("notes", "")).strip(),
        "updated_by": str(record.get("updated_by", "")).strip(),
        "updated_at": str(record.get("updated_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
    }


def load_procurement_items() -> pd.DataFrame:
    ensure_procurement_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        product,
                        supplier,
                        stock_on_hand,
                        stock_in_transit,
                        min_order_qty,
                        order_multiple,
                        lead_time_days,
                        notes,
                        updated_by,
                        updated_at
                    FROM procurement_items
                    ORDER BY LOWER(product)
                    """
                )
                rows = cursor.fetchall()
        records = [
            _normalize_procurement_record(
                {
                    **row,
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                }
            )
            for row in rows
        ]
        return pd.DataFrame(records, columns=PROCUREMENT_COLUMNS)

    try:
        payload = json.loads(PROCUREMENT_ITEMS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []

    records = []
    for raw_record in payload if isinstance(payload, list) else []:
        normalized = _normalize_procurement_record(raw_record if isinstance(raw_record, dict) else {})
        if normalized["product"]:
            records.append(normalized)
    return pd.DataFrame(records, columns=PROCUREMENT_COLUMNS)


def upsert_procurement_items(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_procurement_store()
    if frame.empty:
        return 0

    updated_at = datetime.now().isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        normalized = _normalize_procurement_record(
            {
                **row,
                "updated_by": updated_by,
                "updated_at": updated_at,
            }
        )
        if normalized["product"]:
            records.append(normalized)

    if not records:
        return 0

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(
                        """
                        INSERT INTO procurement_items (
                            product,
                            supplier,
                            stock_on_hand,
                            stock_in_transit,
                            min_order_qty,
                            order_multiple,
                            lead_time_days,
                            notes,
                            updated_by,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (product) DO UPDATE SET
                            supplier = EXCLUDED.supplier,
                            stock_on_hand = EXCLUDED.stock_on_hand,
                            stock_in_transit = EXCLUDED.stock_in_transit,
                            min_order_qty = EXCLUDED.min_order_qty,
                            order_multiple = EXCLUDED.order_multiple,
                            lead_time_days = EXCLUDED.lead_time_days,
                            notes = EXCLUDED.notes,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            record["product"],
                            record["supplier"],
                            record["stock_on_hand"],
                            record["stock_in_transit"],
                            record["min_order_qty"],
                            record["order_multiple"],
                            record["lead_time_days"],
                            record["notes"],
                            record["updated_by"],
                            record["updated_at"],
                        ),
                    )
        return len(records)

    existing_frame = load_procurement_items()
    existing_map = {
        str(row.get("product", "")).strip().casefold(): _normalize_procurement_record(row)
        for row in existing_frame.to_dict(orient="records")
        if str(row.get("product", "")).strip()
    }
    for record in records:
        existing_map[record["product"].casefold()] = record

    merged_records = sorted(existing_map.values(), key=lambda item: item["product"].casefold())
    PROCUREMENT_ITEMS_PATH.write_text(
        json.dumps(merged_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(records)


def merge_procurement_upload(
    frame: pd.DataFrame,
    *,
    updated_by: str,
    override_fields: set[str] | None = None,
) -> int:
    ensure_procurement_store()
    if frame.empty or "product" not in frame.columns:
        return 0

    safe_override_fields = {
        field
        for field in (override_fields or PROCUREMENT_EDITABLE_FIELDS)
        if field in PROCUREMENT_EDITABLE_FIELDS
    }
    if not safe_override_fields:
        return 0

    existing = load_procurement_items()
    existing_map = {
        str(row.get("product", "")).strip().casefold(): _normalize_procurement_record(row)
        for row in existing.to_dict(orient="records")
        if str(row.get("product", "")).strip()
    }

    merged_records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        product = str(row.get("product", "")).strip()
        if not product:
            continue

        existing_record = existing_map.get(product.casefold(), _normalize_procurement_record({"product": product}))
        merged_record = {**existing_record, "product": product}

        for field in safe_override_fields:
            presence_key = f"__has_{field}"
            if presence_key in row and not bool(row.get(presence_key)):
                continue
            if field not in row:
                continue
            merged_record[field] = row.get(field)

        merged_records.append(merged_record)

    if not merged_records:
        return 0

    return upsert_procurement_items(pd.DataFrame.from_records(merged_records), updated_by=updated_by)


def apply_procurement_receipts(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_procurement_store()
    if frame.empty or "product" not in frame.columns or "quantity" not in frame.columns:
        return 0

    receipts = frame.copy()
    receipts["product"] = receipts["product"].fillna("").astype(str).str.strip()
    receipts["quantity"] = pd.to_numeric(receipts["quantity"], errors="coerce").fillna(0.0)
    receipts = receipts[(receipts["product"] != "") & (receipts["quantity"] > 0)].copy()
    if receipts.empty:
        return 0

    receipt_totals = (
        receipts.groupby("product", as_index=False)
        .agg(quantity=("quantity", "sum"))
        .reset_index(drop=True)
    )

    existing = load_procurement_items()
    if existing.empty:
        existing = pd.DataFrame(columns=PROCUREMENT_COLUMNS)

    existing["product"] = existing["product"].fillna("").astype(str).str.strip()
    existing = existing[existing["product"] != ""].copy()
    merged = existing.merge(receipt_totals, on="product", how="outer")

    for text_column in ("supplier", "notes", "updated_by", "updated_at"):
        if text_column not in merged.columns:
            merged[text_column] = ""
        merged[text_column] = merged[text_column].fillna("").astype(str)

    for numeric_column in ("stock_on_hand", "stock_in_transit", "min_order_qty", "order_multiple"):
        if numeric_column not in merged.columns:
            merged[numeric_column] = 0.0
        merged[numeric_column] = pd.to_numeric(merged[numeric_column], errors="coerce").fillna(0.0)

    if "lead_time_days" not in merged.columns:
        merged["lead_time_days"] = 0
    merged["lead_time_days"] = pd.to_numeric(merged["lead_time_days"], errors="coerce").fillna(0).astype(int)
    merged["quantity"] = pd.to_numeric(merged["quantity"], errors="coerce").fillna(0.0)
    merged["stock_on_hand"] = merged["stock_on_hand"] + merged["quantity"]

    upsert_frame = merged[
        [
            "product",
            "supplier",
            "stock_on_hand",
            "stock_in_transit",
            "min_order_qty",
            "order_multiple",
            "lead_time_days",
            "notes",
        ]
    ].copy()
    return upsert_procurement_items(upsert_frame, updated_by=updated_by)
