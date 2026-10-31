from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
import secrets
from typing import Any

import pandas as pd

from db import database_enabled, ensure_database_ready, get_db_connection, isoformat_seconds
from product_identity import map_unique, normalize_product_match_key
from procurement_store import apply_procurement_receipts


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR.parent / "data"))).resolve()
PROCUREMENT_ORDERS_PATH = DATA_DIR / "procurement_orders.json"
PROCUREMENT_ORDER_ITEMS_PATH = DATA_DIR / "procurement_order_items.json"

PROCUREMENT_ORDER_STATUSES = ("draft", "ordered", "in_transit", "received", "cancelled")
PROCUREMENT_ORDER_STATUS_LABELS = {
    "draft": "Черновик",
    "ordered": "Заказан",
    "in_transit": "В пути",
    "received": "Получен",
    "cancelled": "Отменён",
}
ACTIVE_INBOUND_ORDER_STATUSES = {"ordered", "in_transit"}

ORDER_COLUMNS = [
    "order_id",
    "supplier",
    "status",
    "order_date",
    "expected_date",
    "comment",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
]
ORDER_ITEM_COLUMNS = [
    "order_id",
    "product",
    "quantity",
    "unit_cost",
    "comment",
    "received_quantity",
]
ARRIVAL_COLUMNS = [
    "order_id",
    "supplier",
    "product",
    "remaining_qty",
    "stock_before",
    "stock_in_file",
    "sold_qty",
    "arrived_qty",
]


def ensure_procurement_order_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PROCUREMENT_ORDERS_PATH.exists():
        PROCUREMENT_ORDERS_PATH.write_text("[]", encoding="utf-8")
    if not PROCUREMENT_ORDER_ITEMS_PATH.exists():
        PROCUREMENT_ORDER_ITEMS_PATH.write_text("[]", encoding="utf-8")


def _safe_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric >= 0 else 0.0


def _normalize_date_string(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.normalize().date().isoformat()


def _normalize_status(value: Any) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in PROCUREMENT_ORDER_STATUSES else "draft"


def _normalize_order_record(record: dict[str, Any]) -> dict[str, Any]:
    timestamp = datetime.now().isoformat(timespec="seconds")
    return {
        "order_id": str(record.get("order_id", "")).strip(),
        "supplier": str(record.get("supplier", "")).strip(),
        "status": _normalize_status(record.get("status", "draft")),
        "order_date": _normalize_date_string(record.get("order_date")),
        "expected_date": _normalize_date_string(record.get("expected_date")),
        "comment": str(record.get("comment", "")).strip(),
        "created_by": str(record.get("created_by", "")).strip(),
        "updated_by": str(record.get("updated_by", "")).strip(),
        "created_at": str(record.get("created_at", "")).strip() or timestamp,
        "updated_at": str(record.get("updated_at", "")).strip() or timestamp,
    }


def _normalize_order_item_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": str(record.get("order_id", "")).strip(),
        "product": str(record.get("product", "")).strip(),
        "quantity": _safe_float(record.get("quantity", 0)),
        "unit_cost": _safe_float(record.get("unit_cost", 0)),
        "comment": str(record.get("comment", "")).strip(),
        "received_quantity": _safe_float(record.get("received_quantity", 0)),
    }


def _normalize_orders_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ORDER_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[ORDER_COLUMNS]
    normalized["order_id"] = normalized["order_id"].fillna("").astype(str).str.strip()
    normalized["supplier"] = normalized["supplier"].fillna("").astype(str).str.strip()
    normalized["status"] = normalized["status"].map(_normalize_status)
    normalized["order_date"] = pd.to_datetime(normalized["order_date"], errors="coerce").dt.normalize()
    normalized["expected_date"] = pd.to_datetime(normalized["expected_date"], errors="coerce").dt.normalize()
    normalized["comment"] = normalized["comment"].fillna("").astype(str).str.strip()
    normalized["created_by"] = normalized["created_by"].fillna("").astype(str).str.strip()
    normalized["updated_by"] = normalized["updated_by"].fillna("").astype(str).str.strip()
    normalized["created_at"] = normalized["created_at"].fillna("").astype(str)
    normalized["updated_at"] = normalized["updated_at"].fillna("").astype(str)
    normalized = normalized[normalized["order_id"] != ""].drop_duplicates(subset=["order_id"], keep="last")
    return normalized.sort_values(["updated_at", "order_id"], ascending=[False, False], ignore_index=True)


def _normalize_order_items_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ORDER_ITEM_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[ORDER_ITEM_COLUMNS]
    normalized["order_id"] = normalized["order_id"].fillna("").astype(str).str.strip()
    normalized["product"] = normalized["product"].fillna("").astype(str).str.strip()
    normalized["quantity"] = pd.to_numeric(normalized["quantity"], errors="coerce").fillna(0.0).clip(lower=0.0)
    normalized["unit_cost"] = pd.to_numeric(normalized["unit_cost"], errors="coerce").fillna(0.0).clip(lower=0.0)
    normalized["comment"] = normalized["comment"].fillna("").astype(str).str.strip()
    normalized["received_quantity"] = pd.to_numeric(normalized["received_quantity"], errors="coerce").fillna(0.0).clip(lower=0.0)
    normalized = normalized[(normalized["order_id"] != "") & (normalized["product"] != "") & (normalized["quantity"] > 0)].copy()
    return normalized.reset_index(drop=True)


def _write_order_items(items: pd.DataFrame) -> None:
    PROCUREMENT_ORDER_ITEMS_PATH.write_text(
        json.dumps(
            [
                _normalize_order_item_record(row)
                for row in _normalize_order_items_frame(items).to_dict(orient="records")
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _remaining_quantity(items: pd.DataFrame) -> pd.Series:
    """What is still on the way: an order can arrive in several deliveries."""
    if "received_quantity" not in items.columns:
        return items["quantity"]
    return (items["quantity"] - items["received_quantity"]).clip(lower=0.0)


def _generate_order_id() -> str:
    return f"PO-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2).upper()}"


def load_procurement_orders() -> pd.DataFrame:
    ensure_procurement_order_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        order_id,
                        supplier,
                        status,
                        order_date,
                        expected_date,
                        comment,
                        created_by,
                        updated_by,
                        created_at,
                        updated_at
                    FROM procurement_orders
                    ORDER BY updated_at DESC, order_id DESC
                    """
                )
                rows = cursor.fetchall()
        frame = pd.DataFrame(
            [
                {
                    "order_id": str(row.get("order_id", "")).strip(),
                    "supplier": str(row.get("supplier", "")).strip(),
                    "status": str(row.get("status", "draft")).strip(),
                    "order_date": isoformat_seconds(row.get("order_date")),
                    "expected_date": isoformat_seconds(row.get("expected_date")),
                    "comment": str(row.get("comment", "")).strip(),
                    "created_by": str(row.get("created_by", "")).strip(),
                    "updated_by": str(row.get("updated_by", "")).strip(),
                    "created_at": isoformat_seconds(row.get("created_at")),
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                }
                for row in rows
            ],
            columns=ORDER_COLUMNS,
        )
        return _normalize_orders_frame(frame)

    try:
        payload = json.loads(PROCUREMENT_ORDERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    records = [
        _normalize_order_record(raw_record if isinstance(raw_record, dict) else {})
        for raw_record in payload if isinstance(payload, list)
    ]
    return _normalize_orders_frame(pd.DataFrame(records, columns=ORDER_COLUMNS))


def load_procurement_order_items() -> pd.DataFrame:
    ensure_procurement_order_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        order_id,
                        product,
                        quantity,
                        COALESCE(unit_cost, 0) AS unit_cost,
                        comment,
                        COALESCE(received_quantity, 0) AS received_quantity
                    FROM procurement_order_items
                    ORDER BY order_id, product
                    """
                )
                rows = cursor.fetchall()
        frame = pd.DataFrame(
            [
                {
                    "order_id": str(row.get("order_id", "")).strip(),
                    "product": str(row.get("product", "")).strip(),
                    "quantity": row.get("quantity"),
                    "unit_cost": row.get("unit_cost"),
                    "comment": str(row.get("comment", "")).strip(),
                    "received_quantity": row.get("received_quantity"),
                }
                for row in rows
            ],
            columns=ORDER_ITEM_COLUMNS,
        )
        return _normalize_order_items_frame(frame)

    try:
        payload = json.loads(PROCUREMENT_ORDER_ITEMS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    records = [
        _normalize_order_item_record(raw_record if isinstance(raw_record, dict) else {})
        for raw_record in payload if isinstance(payload, list)
    ]
    return _normalize_order_items_frame(pd.DataFrame(records, columns=ORDER_ITEM_COLUMNS))


def create_procurement_order(
    *,
    supplier: str,
    items_frame: pd.DataFrame,
    order_date: date | datetime | pd.Timestamp | str | None = None,
    expected_date: date | datetime | pd.Timestamp | str | None = None,
    comment: str = "",
    created_by: str = "",
    status: str = "draft",
) -> dict[str, Any]:
    ensure_procurement_order_store()
    new_order_id = _generate_order_id()
    normalized_items = _normalize_order_items_frame(items_frame.assign(order_id=new_order_id))
    if normalized_items.empty:
        raise ValueError("Для создания заказа нужен хотя бы один товар с количеством больше нуля.")

    normalized_order = _normalize_order_record(
        {
            "order_id": new_order_id,
            "supplier": supplier,
            "status": status,
            "order_date": order_date,
            "expected_date": expected_date,
            "comment": comment,
            "created_by": created_by,
            "updated_by": created_by,
        }
    )
    normalized_items["order_id"] = normalized_order["order_id"]

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO procurement_orders (
                        order_id,
                        supplier,
                        status,
                        order_date,
                        expected_date,
                        comment,
                        created_by,
                        updated_by,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        normalized_order["order_id"],
                        normalized_order["supplier"],
                        normalized_order["status"],
                        normalized_order["order_date"] or None,
                        normalized_order["expected_date"] or None,
                        normalized_order["comment"],
                        normalized_order["created_by"],
                        normalized_order["updated_by"],
                    ),
                )
                for row in normalized_items.to_dict(orient="records"):
                    cursor.execute(
                        """
                        INSERT INTO procurement_order_items (
                            order_id,
                            product,
                            quantity,
                            unit_cost,
                            comment
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            row["order_id"],
                            row["product"],
                            float(row["quantity"]),
                            float(row.get("unit_cost", 0) or 0),
                            row.get("comment", ""),
                        ),
                    )
        return normalized_order

    orders = load_procurement_orders()
    items = load_procurement_order_items()
    orders = pd.concat([orders, pd.DataFrame([normalized_order])], ignore_index=True)
    items = pd.concat([items, normalized_items[ORDER_ITEM_COLUMNS]], ignore_index=True)
    PROCUREMENT_ORDERS_PATH.write_text(
        json.dumps(
            [
                _normalize_order_record(row)
                for row in _normalize_orders_frame(orders).to_dict(orient="records")
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_order_items(items)
    return normalized_order


def update_procurement_order(
    order_id: str,
    *,
    status: str | None = None,
    order_date: date | datetime | pd.Timestamp | str | None = None,
    expected_date: date | datetime | pd.Timestamp | str | None = None,
    comment: str | None = None,
    updated_by: str = "",
) -> dict[str, Any] | None:
    ensure_procurement_order_store()
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None

    normalized_status = None if status is None else _normalize_status(status)
    normalized_order_date = None if order_date is None else _normalize_date_string(order_date)
    normalized_expected_date = None if expected_date is None else _normalize_date_string(expected_date)
    normalized_comment = None if comment is None else str(comment).strip()
    updated_by_value = str(updated_by or "").strip()

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if normalized_status in ACTIVE_INBOUND_ORDER_STATUSES:
                    # Reopening a received order puts its goods back on the way.
                    cursor.execute(
                        """
                        UPDATE procurement_order_items
                        SET received_quantity = 0
                        WHERE order_id = %s
                          AND EXISTS (
                              SELECT 1 FROM procurement_orders
                              WHERE order_id = %s AND status = 'received'
                          )
                        """,
                        (normalized_order_id, normalized_order_id),
                    )
                cursor.execute(
                    """
                    UPDATE procurement_orders
                    SET
                        status = COALESCE(%s, status),
                        order_date = COALESCE(%s::date, order_date),
                        expected_date = COALESCE(%s::date, expected_date),
                        comment = COALESCE(%s, comment),
                        updated_by = CASE
                            WHEN %s = '' THEN updated_by
                            ELSE %s
                        END,
                        updated_at = NOW()
                    WHERE order_id = %s
                    RETURNING
                        order_id,
                        supplier,
                        status,
                        order_date,
                        expected_date,
                        comment,
                        created_by,
                        updated_by,
                        created_at,
                        updated_at
                    """,
                    (
                        normalized_status,
                        normalized_order_date,
                        normalized_expected_date,
                        normalized_comment,
                        updated_by_value,
                        updated_by_value,
                        normalized_order_id,
                    ),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return _normalize_order_record(
            {
                "order_id": row.get("order_id"),
                "supplier": row.get("supplier"),
                "status": row.get("status"),
                "order_date": row.get("order_date"),
                "expected_date": row.get("expected_date"),
                "comment": row.get("comment"),
                "created_by": row.get("created_by"),
                "updated_by": row.get("updated_by"),
                "created_at": isoformat_seconds(row.get("created_at")),
                "updated_at": isoformat_seconds(row.get("updated_at")),
            }
        )

    orders = load_procurement_orders()
    if orders.empty:
        return None
    mask = orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not mask.any():
        return None

    if normalized_status in ACTIVE_INBOUND_ORDER_STATUSES and orders.loc[mask, "status"].eq("received").any():
        items = load_procurement_order_items()
        items.loc[items["order_id"] == normalized_order_id, "received_quantity"] = 0.0
        _write_order_items(items)
    if normalized_status is not None:
        orders.loc[mask, "status"] = normalized_status
    if normalized_order_date is not None:
        orders.loc[mask, "order_date"] = pd.to_datetime(normalized_order_date, errors="coerce")
    if normalized_expected_date is not None:
        orders.loc[mask, "expected_date"] = pd.to_datetime(normalized_expected_date, errors="coerce")
    if normalized_comment is not None:
        orders.loc[mask, "comment"] = normalized_comment
    if updated_by_value:
        orders.loc[mask, "updated_by"] = updated_by_value
    orders.loc[mask, "updated_at"] = datetime.now().isoformat(timespec="seconds")

    normalized_orders = _normalize_orders_frame(orders)
    PROCUREMENT_ORDERS_PATH.write_text(
        json.dumps(
            [
                _normalize_order_record(row)
                for row in normalized_orders.to_dict(orient="records")
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    updated_rows = normalized_orders[normalized_orders["order_id"] == normalized_order_id]
    return updated_rows.iloc[0].to_dict() if not updated_rows.empty else None


def apply_procurement_order_receipt(
    order_id: str,
    *,
    updated_by: str,
    expected_date: date | datetime | pd.Timestamp | str | None = None,
    comment: str | None = None,
) -> dict[str, Any] | None:
    orders = load_procurement_orders()
    items = load_procurement_order_items()
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or orders.empty or items.empty:
        return None

    order_rows = orders[orders["order_id"].astype(str).str.strip() == normalized_order_id].copy()
    if order_rows.empty:
        return None
    current_status = str(order_rows.iloc[0].get("status", "")).strip()
    if current_status == "received":
        return order_rows.iloc[0].to_dict()
    if current_status == "cancelled":
        raise ValueError("Нельзя принять отменённый заказ.")

    order_items = items[items["order_id"].astype(str).str.strip() == normalized_order_id]
    if order_items.empty:
        raise ValueError("В заказе нет товарных строк для приёмки.")

    # What an earlier stock file already brought in is on the stock, only the rest is added.
    apply_procurement_receipts(
        order_items.assign(quantity=_remaining_quantity(order_items))[["product", "quantity"]],
        updated_by=updated_by,
    )
    updated_order = update_procurement_order(
        normalized_order_id,
        status="received",
        expected_date=expected_date,
        updated_by=updated_by,
        comment=comment if comment is not None else str(order_rows.iloc[0].get("comment", "")).strip(),
    )
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE procurement_order_items SET received_quantity = quantity WHERE order_id = %s",
                    (normalized_order_id,),
                )
    else:
        mask = items["order_id"] == normalized_order_id
        items.loc[mask, "received_quantity"] = items.loc[mask, "quantity"]
        _write_order_items(items)
    return updated_order


def build_arrival_lines(
    orders_frame: pd.DataFrame,
    order_items_frame: pd.DataFrame,
    stock_before: pd.DataFrame,
    stock_in_file: pd.DataFrame,
    sales: pd.DataFrame,
    *,
    full_snapshot: bool,
) -> pd.DataFrame:
    """Open order lines with the quantity a new stock file suggests has arrived.

    arrived = stock in the file - stored stock + sales since the stock was stored.
    The oldest order of a product takes the arrival first. A partial file says
    nothing about the products it does not list, so they are left out.
    """
    if orders_frame.empty or order_items_frame.empty:
        return pd.DataFrame(columns=ARRIVAL_COLUMNS)
    active_orders = orders_frame[orders_frame["status"].isin(ACTIVE_INBOUND_ORDER_STATUSES)]
    lines = order_items_frame.merge(
        active_orders[["order_id", "supplier", "order_date"]],
        on="order_id",
        how="inner",
    )
    lines = lines.assign(remaining_qty=_remaining_quantity(lines))
    lines = lines[lines["remaining_qty"] > 0].copy()
    if lines.empty:
        return pd.DataFrame(columns=ARRIVAL_COLUMNS)
    lines["_key"] = lines["product"].map(normalize_product_match_key)

    file_stock = (
        stock_in_file.assign(
            _key=stock_in_file["product"].map(normalize_product_match_key),
            stock_on_hand=pd.to_numeric(stock_in_file["stock_on_hand"], errors="coerce").fillna(0.0),
        )
        .groupby("_key")["stock_on_hand"]
        .sum()
    )
    if not full_snapshot:
        lines = lines[lines["_key"].isin(file_stock.index)].copy()
        if lines.empty:
            return pd.DataFrame(columns=ARRIVAL_COLUMNS)
    open_keys = set(lines["_key"])

    # The latest record of each product, as the procurement forecast reads it.
    stored: dict[str, tuple[float, pd.Timestamp | None]] = {}
    if not stock_before.empty:
        stored_rows = stock_before.assign(_key=stock_before["product"].map(normalize_product_match_key))
        stored_rows = stored_rows[stored_rows["_key"].isin(open_keys)]
        if "updated_at" not in stored_rows.columns:
            stored_rows = stored_rows.assign(updated_at="")
        stored_rows = stored_rows.assign(_updated=stored_rows["updated_at"].fillna("").astype(str))
        for row in stored_rows.sort_values("_updated", kind="stable").to_dict(orient="records"):
            parsed = pd.to_datetime(row["_updated"] or None, errors="coerce", utc=True)
            stock_value = pd.to_numeric(row.get("stock_on_hand"), errors="coerce")
            stored[row["_key"]] = (
                0.0 if pd.isna(stock_value) else float(stock_value),
                None if pd.isna(parsed) else parsed.tz_localize(None).normalize(),
            )

    sold: dict[str, float] = {}
    cutoffs = [cutoff for _, cutoff in stored.values() if cutoff is not None]
    if cutoffs and not sales.empty:
        recent = sales.loc[sales["date"] >= min(cutoffs) + pd.Timedelta(days=1), ["product", "date", "quantity"]]
        recent = recent.assign(_key=map_unique(recent["product"], normalize_product_match_key))
        recent = recent[recent["_key"].isin(open_keys)]
        for key, group in recent.groupby("_key"):
            cutoff = stored.get(key, (0.0, None))[1]
            if cutoff is not None:
                sold[key] = float(
                    pd.to_numeric(group.loc[group["date"] >= cutoff + pd.Timedelta(days=1), "quantity"], errors="coerce").sum()
                )

    lines = lines.sort_values(["order_date", "order_id"], na_position="first", kind="stable")
    available: dict[str, float] = {}
    records: list[dict[str, Any]] = []
    for row in lines.to_dict(orient="records"):
        key = row["_key"]
        before = stored.get(key, (0.0, None))[0]
        in_file = float(file_stock.get(key, 0.0))
        sold_qty = max(sold.get(key, 0.0), 0.0)
        if key not in available:
            available[key] = max(in_file - before + sold_qty, 0.0)
        arrived = min(available[key], float(row["remaining_qty"]))
        available[key] -= arrived
        records.append(
            {
                "order_id": row["order_id"],
                "supplier": row["supplier"],
                "product": row["product"],
                "remaining_qty": float(row["remaining_qty"]),
                "stock_before": before,
                "stock_in_file": in_file,
                "sold_qty": sold_qty,
                "arrived_qty": arrived,
            }
        )
    return pd.DataFrame(records, columns=ARRIVAL_COLUMNS)


def _apply_arrivals_to_lines(lines: list[dict[str, Any]], arrivals: dict[tuple[str, str], float]) -> set[str]:
    """Raise `received_quantity` in place; returns the orders that have fully arrived."""
    left = dict(arrivals)
    for line in lines:
        key = (line["order_id"], line["product"])
        room = max(float(line["quantity"]) - float(line["received_quantity"]), 0.0)
        taken = min(left.get(key, 0.0), room)
        if taken > 0:
            line["received_quantity"] = float(line["received_quantity"]) + taken
            left[key] -= taken
    touched = {order_id for (order_id, _), quantity in arrivals.items() if quantity > 0}
    return {
        order_id
        for order_id in touched
        if all(
            float(line["quantity"]) - float(line["received_quantity"]) <= 1e-9
            for line in lines
            if line["order_id"] == order_id
        )
    }


def apply_order_arrivals(arrivals_frame: pd.DataFrame, *, updated_by: str) -> set[str]:
    """Record what arrived, without touching the stock: the stock file already holds it.

    An order whose lines have all arrived becomes received. Returns those orders.
    """
    ensure_procurement_order_store()
    arrivals = arrivals_frame[arrivals_frame["arrived_qty"] > 0]
    if arrivals.empty:
        return set()
    amounts = {
        (str(row["order_id"]), str(row["product"])): float(row["arrived_qty"])
        for row in arrivals.to_dict(orient="records")
    }
    order_ids = sorted({order_id for order_id, _ in amounts})

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT items.line_id, items.order_id, items.product, items.quantity, items.received_quantity
                    FROM procurement_order_items AS items
                    JOIN procurement_orders AS orders ON orders.order_id = items.order_id
                    WHERE items.order_id = ANY(%s) AND orders.status = ANY(%s)
                    ORDER BY items.line_id
                    FOR UPDATE OF items
                    """,
                    (order_ids, sorted(ACTIVE_INBOUND_ORDER_STATUSES)),
                )
                lines = [dict(row) for row in cursor.fetchall()]
                # An order closed by someone else meanwhile has no lines here and stays closed.
                open_ids = {line["order_id"] for line in lines}
                completed = _apply_arrivals_to_lines(
                    lines,
                    {key: quantity for key, quantity in amounts.items() if key[0] in open_ids},
                )
                for line in lines:
                    cursor.execute(
                        "UPDATE procurement_order_items SET received_quantity = %s WHERE line_id = %s",
                        (line["received_quantity"], line["line_id"]),
                    )
                for order_id in open_ids:
                    cursor.execute(
                        """
                        UPDATE procurement_orders
                        SET status = CASE WHEN %s THEN 'received' ELSE status END,
                            updated_by = %s,
                            updated_at = NOW()
                        WHERE order_id = %s
                        """,
                        (order_id in completed, str(updated_by or "").strip(), order_id),
                    )
        return completed

    orders = load_procurement_orders()
    open_ids = set(orders.loc[orders["status"].isin(ACTIVE_INBOUND_ORDER_STATUSES), "order_id"]) & set(order_ids)
    items = load_procurement_order_items()
    lines = items[items["order_id"].isin(open_ids)].to_dict(orient="records")
    completed = _apply_arrivals_to_lines(
        lines,
        {key: quantity for key, quantity in amounts.items() if key[0] in open_ids},
    )
    untouched = items[~items["order_id"].isin(open_ids)]
    _write_order_items(pd.concat([untouched, pd.DataFrame(lines, columns=ORDER_ITEM_COLUMNS)], ignore_index=True))
    for order_id in open_ids:
        update_procurement_order(
            order_id,
            status="received" if order_id in completed else None,
            updated_by=updated_by,
        )
    return completed


def build_open_order_summary(
    orders_frame: pd.DataFrame,
    order_items_frame: pd.DataFrame,
    *,
    active_statuses: set[str] | None = None,
) -> pd.DataFrame:
    columns = ["product", "ordered_in_transit_qty", "open_order_count"]
    if orders_frame.empty or order_items_frame.empty:
        return pd.DataFrame(columns=columns)

    active_statuses = active_statuses or ACTIVE_INBOUND_ORDER_STATUSES
    active_orders = orders_frame[orders_frame["status"].isin(active_statuses)][["order_id"]].drop_duplicates()
    if active_orders.empty:
        return pd.DataFrame(columns=columns)

    working = order_items_frame.merge(active_orders, on="order_id", how="inner")
    working = working.assign(quantity=_remaining_quantity(working))
    working = working[working["quantity"] > 0]
    if working.empty:
        return pd.DataFrame(columns=columns)

    summary = (
        working.groupby("product", as_index=False)
        .agg(
            ordered_in_transit_qty=("quantity", "sum"),
            open_order_count=("order_id", "nunique"),
        )
        .sort_values(["ordered_in_transit_qty", "product"], ascending=[False, True], ignore_index=True)
    )
    return summary[columns]


def build_procurement_order_overview(
    orders_frame: pd.DataFrame,
    order_items_frame: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "order_id",
        "supplier",
        "status",
        "status_label",
        "order_date",
        "expected_date",
        "sku_count",
        "total_quantity",
        "remaining_quantity",
        "created_by",
        "updated_by",
        "updated_at",
        "comment",
    ]
    if orders_frame.empty:
        return pd.DataFrame(columns=columns)

    items_summary = pd.DataFrame(columns=["order_id", "sku_count", "total_quantity", "remaining_quantity"])
    if not order_items_frame.empty:
        items_summary = (
            order_items_frame.assign(remaining_quantity=_remaining_quantity(order_items_frame))
            .groupby("order_id", as_index=False)
            .agg(
                sku_count=("product", "nunique"),
                total_quantity=("quantity", "sum"),
                remaining_quantity=("remaining_quantity", "sum"),
            )
        )

    overview = orders_frame.merge(items_summary, on="order_id", how="left")
    overview["status_label"] = overview["status"].map(PROCUREMENT_ORDER_STATUS_LABELS).fillna(overview["status"])
    overview["sku_count"] = pd.to_numeric(overview["sku_count"], errors="coerce").fillna(0).astype(int)
    overview["total_quantity"] = pd.to_numeric(overview["total_quantity"], errors="coerce").fillna(0.0)
    overview["remaining_quantity"] = pd.to_numeric(overview["remaining_quantity"], errors="coerce").fillna(0.0)
    overview = overview.sort_values(["updated_at", "order_id"], ascending=[False, False], ignore_index=True)
    for column in columns:
        if column not in overview.columns:
            overview[column] = pd.NA
    return overview[columns]
