"""Orders on the way: the list, one order's lines, its changes and the history of what arrived."""
from __future__ import annotations

from datetime import date
import math

import pandas as pd

from web_api import data  # noqa: F401  (puts app/ on the import path)
from db import database_enabled, list_audit_logs, log_audit_event
from procurement_order_store import (
    ACTIVE_INBOUND_ORDER_STATUSES,
    PROCUREMENT_ORDER_STATUS_LABELS,
    PROCUREMENT_ORDER_STATUSES,
    apply_procurement_order_receipt,
    build_arrival_history,
    load_procurement_order_items,
    load_procurement_orders,
    update_procurement_order,
)

FILTERS = ("active", "overdue", "all")


def _number(value: object) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(number) else float(number)


def _iso(value: object) -> str | None:
    stamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(stamp) else stamp.date().isoformat()


def order_list(kind: str, today: date | None = None) -> list[dict[str, object]]:
    """Active orders, the overdue ones among them, or all; each with its progress in units."""
    today = today or date.today()
    orders = load_procurement_orders()
    items = load_procurement_order_items()
    if orders.empty:
        return []
    received = pd.to_numeric(items.get("received_quantity"), errors="coerce").fillna(0) if not items.empty else pd.Series(dtype=float)
    ordered = pd.to_numeric(items.get("quantity"), errors="coerce").fillna(0) if not items.empty else pd.Series(dtype=float)
    totals = (
        pd.DataFrame({"order_id": items["order_id"], "ordered": ordered, "received": received.clip(upper=ordered)})
        .groupby("order_id")
        .agg(ordered=("ordered", "sum"), received=("received", "sum"), lines=("ordered", "size"))
        if not items.empty
        else pd.DataFrame(columns=["ordered", "received", "lines"])
    )
    result = []
    for record in orders.to_dict(orient="records"):
        status = str(record.get("status", ""))
        expected = _iso(record.get("expected_date"))
        active = status in ACTIVE_INBOUND_ORDER_STATUSES
        days_left = (date.fromisoformat(expected) - today).days if expected and active else None
        overdue = days_left is not None and days_left < 0
        if kind == "active" and not active or kind == "overdue" and not overdue:
            continue
        order_id = str(record["order_id"])
        total = totals.loc[order_id] if order_id in totals.index else None
        result.append(
            {
                "order_id": order_id,
                "supplier": str(record.get("supplier") or ""),
                "status": status,
                "status_label": PROCUREMENT_ORDER_STATUS_LABELS.get(status, status),
                "order_date": _iso(record.get("order_date")),
                "expected_date": expected,
                "days_left": days_left,
                "overdue": overdue,
                "lines": int(total["lines"]) if total is not None else 0,
                "ordered": _number(total["ordered"]) if total is not None else 0.0,
                "received": _number(total["received"]) if total is not None else 0.0,
                "comment": str(record.get("comment") or ""),
            }
        )
    # Overdue first, then the nearest arrival.
    return sorted(result, key=lambda row: (not row["overdue"], row["expected_date"] or "9999-12-31", row["order_id"]))


def order_detail(order_id: str) -> dict[str, object] | None:
    summary = next((row for row in order_list("all") if row["order_id"] == order_id), None)
    if summary is None:
        return None
    items = load_procurement_order_items()
    lines = items[items["order_id"].astype(str) == order_id] if not items.empty else items
    summary["items"] = [
        {
            "product": str(line.get("product", "")),
            "quantity": _number(line.get("quantity")),
            "received": min(_number(line.get("received_quantity")), _number(line.get("quantity"))),
            "remaining": max(_number(line.get("quantity")) - _number(line.get("received_quantity")), 0.0),
        }
        for line in lines.to_dict(orient="records")
    ]
    return summary


def change_order(order_id: str, *, action: str, expected_date: date | None, username: str) -> None:
    """The order card's actions: receive the rest, set a new expected date, or cancel."""
    if action == "receive":
        apply_procurement_order_receipt(order_id, updated_by=username, expected_date=expected_date)
        _journal(username, "procurement.order_receive", {"order_id": order_id})
    elif action == "reschedule":
        if expected_date is None:
            raise ValueError("Укажите новую ожидаемую дату.")
        update_procurement_order(order_id, expected_date=expected_date, updated_by=username)
        _journal(username, "procurement.order_update", {"order_id": order_id, "expected_date": expected_date.isoformat()})
    elif action == "cancel":
        update_procurement_order(order_id, status="cancelled", updated_by=username)
        _journal(username, "procurement.order_update", {"order_id": order_id, "status": "cancelled"})
    else:
        raise ValueError("Неизвестное действие.")
    data.forget_forecasts()


def arrival_history() -> list[dict[str, object]]:
    logs = (
        [entry for entry in list_audit_logs(limit=2000) if entry.get("action") in {"procurement.stock_upload", "procurement.order_receive"}]
        if database_enabled()
        else []
    )
    history = build_arrival_history(logs, load_procurement_orders(), load_procurement_order_items())
    return [
        {
            "when": None if pd.isna(record["when"]) else pd.Timestamp(record["when"]).isoformat(),
            "user": record["user"],
            "source": record["source"],
            "order_id": record["order_id"],
            "supplier": record["supplier"],
            "product": record["product"],
            "quantity": record["quantity"] if not (isinstance(record["quantity"], float) and math.isnan(record["quantity"])) else 0.0,
        }
        for record in history.to_dict(orient="records")
    ]


def _journal(username: str, action: str, details: dict[str, object]) -> None:
    try:
        log_audit_event(user_id=username, action=action, details=details)
    except Exception:  # The journal never stops the action itself.
        pass


__all__ = ["FILTERS", "PROCUREMENT_ORDER_STATUSES", "arrival_history", "change_order", "order_detail", "order_list"]
