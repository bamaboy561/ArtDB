"""Supplier orders for the API: the same forms, files and rules as on the old site."""
from __future__ import annotations

from datetime import date, timedelta
import math

import pandas as pd

from web_api import data
from db import log_audit_event
from procurement_analytics import supplier_lead_time
from procurement_order_store import create_procurement_order, load_procurement_orders
from supplier_order_forms import (
    SECTION_BOARDS,
    SUPPLIER_FORMS,
    SupplierForm,
    apply_order_file,
    build_order_form_rows,
    build_supplier_order_workbook,
    generic_supplier_forms,
    order_file_comment,
    order_form_supplier,
    order_items_from_form,
    parse_order_form_file,
)
from swiss_krono_order import is_swiss_krono_supplier

ROW_FIELDS = (
    "row_id",
    "code",
    "name",
    "quantity",
    "edge_or_sheets",
    "section",
    "pack",
    "abc_class",
    "xyz_class",
    "priority",
    "stock_status",
    "available_stock_qty",
    "forecast_qty",
    "product",
    "edge_product",
)


def supplier_forms(forecast: pd.DataFrame) -> list[SupplierForm]:
    """Each supplier's form; a supplier without one of its own gets the common table."""
    names = forecast.get("supplier", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    others = sorted(
        {
            name
            for name in names
            if name
            and name.casefold() != "не назначен"
            and not is_swiss_krono_supplier(name)
            and not any(form.matches(name) for form in SUPPLIER_FORMS)
        },
        key=str.casefold,
    )
    return [*SUPPLIER_FORMS, *generic_supplier_forms(others)]


def find_form(forecast: pd.DataFrame, key: str) -> SupplierForm | None:
    return next((form for form in supplier_forms(forecast) if form.key == key), None)


def _layout(form: SupplierForm, rows: pd.DataFrame) -> dict[str, object]:
    """Which quantity columns the screen shows for this form."""
    has_hpl = bool(rows["section"].ne(SECTION_BOARDS).any()) if not rows.empty else False
    if form.goods_only:
        columns = [{"field": "edge_or_sheets", "title": form.quantity_title}]
    else:
        columns = [
            {"field": "quantity", "title": "Плиты, шт"},
            {"field": "edge_or_sheets", "title": "Кромка, м" if not has_hpl else "Кромка, м / HPL"},
        ]
    return {"columns": columns, "box_layout": form.box_layout, "unit": form.unit if form.goods_only else ""}


DAYS_PER_MONTH = 30.4
STOCK_LEVELS = ("out", "short", "low", "ok")


def _cover_days(available: float, monthly: float) -> float | None:
    """How many days the stock lasts at the forecast pace; None when the position does not sell."""
    if monthly <= 0:
        return None
    return max(available, 0.0) / (monthly / DAYS_PER_MONTH)


def _stock_level(available: float, monthly: float, wanted: bool, lead_time: int) -> str:
    """out: nothing left; short: runs out before an order placed today arrives; low: the forecast asks
    for more; ok: the stock is enough; idle: does not sell."""
    days = _cover_days(available, monthly)
    if days is None:
        return "idle"
    if available <= 0:
        return "out"
    if days < max(lead_time, 0):
        return "short"
    return "low" if wanted else "ok"


def _row_level(record: dict[str, object], lead_time: int) -> tuple[float | None, str]:
    available = float(record.get("available_stock_qty") or 0)
    monthly = float(record.get("forecast_qty") or 0)
    wanted = float(record.get("quantity") or 0) > 0 or float(record.get("edge_or_sheets") or 0) > 0
    return _cover_days(available, monthly), _stock_level(available, monthly, wanted, lead_time)


def _stock_counts(rows: pd.DataFrame, lead_time: int) -> dict[str, int]:
    counts = dict.fromkeys(STOCK_LEVELS, 0)
    for record in rows.to_dict(orient="records"):
        level = _row_level(record, lead_time)[1]
        if level in counts:
            counts[level] += 1
    return counts


def _to_order(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    return int((rows["quantity"].gt(0) | rows["edge_or_sheets"].gt(0)).sum())


def form_summaries(forecast: pd.DataFrame, default_lead_time: int) -> list[dict[str, object]]:
    summaries = []
    for form in supplier_forms(forecast):
        rows = build_order_form_rows(forecast, form=form)
        lead_time = supplier_lead_time(forecast, form.matches, default_lead_time)
        summaries.append(
            {
                "key": form.key,
                "title": form.title,
                "to_order": _to_order(rows),
                "positions": int(len(rows)),
                "lead_time_days": int(lead_time),
                "arrival": (date.today() + timedelta(days=max(int(lead_time), 0))).isoformat(),
                "stock": _stock_counts(rows, int(lead_time)),
            }
        )
    return summaries


def _clean(value: object) -> object:
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def form_detail(forecast: pd.DataFrame, form: SupplierForm, default_lead_time: int) -> dict[str, object]:
    rows = build_order_form_rows(forecast, form=form)
    lead_time = supplier_lead_time(forecast, form.matches, default_lead_time)
    return {
        "key": form.key,
        "title": form.title,
        "layout": _layout(form, rows),
        "lead_time_days": int(lead_time),
        "arrival": (date.today() + timedelta(days=max(int(lead_time), 0))).isoformat(),
        "rows": [_detail_row(record, int(lead_time)) for record in rows.to_dict(orient="records")],
    }


def _detail_row(record: dict[str, object], lead_time: int) -> dict[str, object]:
    row = {field: _clean(record.get(field)) for field in ROW_FIELDS}
    days, level = _row_level(record, lead_time)
    row["cover_days"] = None if days is None else round(days, 1)
    row["stock_level"] = level
    return row


def _edited_rows(forecast: pd.DataFrame, form: SupplierForm, quantities: dict[str, tuple[int, int]]) -> pd.DataFrame:
    rows = build_order_form_rows(forecast, form=form)
    rows["quantity"] = [quantities.get(row_id, (quantity, 0))[0] for row_id, quantity in zip(rows["row_id"], rows["quantity"])]
    rows["edge_or_sheets"] = [
        quantities.get(row_id, (0, edge))[1] for row_id, edge in zip(rows["row_id"], rows["edge_or_sheets"])
    ]
    return rows


def order_workbook(forecast: pd.DataFrame, form: SupplierForm, quantities: dict[str, tuple[int, int]]) -> bytes:
    """The supplier's file with the quantities as edited on the screen."""
    return build_supplier_order_workbook(_edited_rows(forecast, form, quantities), form=form)


def _create_in_transit(rows: pd.DataFrame, form: SupplierForm, *, lead_time: int, comment: str, username: str) -> str:
    items = order_items_from_form(rows)
    if items.empty:
        raise ValueError("В заказе нет ни одной позиции с количеством.")
    order = create_procurement_order(
        supplier=order_form_supplier(rows, default=form.title),
        items_frame=items,
        order_date=date.today(),
        expected_date=date.today() + timedelta(days=max(int(lead_time), 0)),
        comment=comment,
        created_by=username,
        status="in_transit",
    )
    try:
        log_audit_event(
            user_id=username,
            action="procurement.order_create",
            details={"order_id": order["order_id"], "supplier": order["supplier"], "status": "in_transit", "item_count": len(items)},
        )
    except Exception:  # The journal never stops the order itself.
        pass
    data.forget_forecasts()
    return str(order["order_id"])


def order_from_screen(
    forecast: pd.DataFrame, form: SupplierForm, quantities: dict[str, tuple[int, int]], *, lead_time: int, username: str
) -> str:
    """"В пути": the supplier confirmed the order as it stands on the screen."""
    rows = _edited_rows(forecast, form, quantities)
    return _create_in_transit(rows, form, lead_time=lead_time, comment=f"Форма заказа {form.title}", username=username)


def _already_saved(fingerprint: str) -> bool:
    orders = load_procurement_orders()
    if orders.empty:
        return False
    return bool((orders["comment"].astype(str).str.contains(fingerprint, regex=False) & orders["status"].ne("cancelled")).any())


def file_preview(forecast: pd.DataFrame, form: SupplierForm, content: bytes) -> tuple[dict[str, object], pd.DataFrame, object]:
    """What the supplier's file holds against the proposal, before it becomes an order."""
    rows = build_order_form_rows(forecast, form=form)
    site_products = tuple(forecast["product"].astype(str)) if "product" in forecast.columns else ()
    parsed = parse_order_form_file(content, rows, site_products, form=form)
    applied = apply_order_file(rows, parsed) if not parsed.errors else rows.assign(file_note="")
    proposed = rows.set_index("row_id")
    lines = []
    for record in applied.to_dict(orient="records"):
        was = proposed.loc[record["row_id"]] if record["row_id"] in proposed.index else None
        before = (int(was["quantity"]), int(was["edge_or_sheets"])) if was is not None else (0, 0)
        now = (int(record["quantity"]), int(record["edge_or_sheets"]))
        if now == (0, 0) and before == (0, 0):
            continue
        lines.append(
            {
                "code": _clean(record.get("code")) or "",
                "name": _clean(record.get("name")) or "",
                "quantity": now[0],
                "edge_or_sheets": now[1],
                "proposed_quantity": before[0],
                "proposed_edge_or_sheets": before[1],
                "changed": now != before,
                "note": _clean(record.get("file_note")) or "",
            }
        )
    ordered = applied[(applied["quantity"] > 0) | (applied["edge_or_sheets"] > 0)] if not parsed.errors else applied.iloc[0:0]
    preview = {
        "errors": list(parsed.errors),
        "fingerprint": parsed.fingerprint,
        "already_saved": _already_saved(parsed.fingerprint),
        "lines": lines,
        "ordered_lines": int(len(ordered)),
        "total_quantity": int(ordered["quantity"].sum()) if not ordered.empty else 0,
        "total_edge_or_sheets": int(ordered["edge_or_sheets"].sum()) if not ordered.empty else 0,
        "new_positions": [line["name"] for line in lines if line["note"]],
    }
    return preview, applied, parsed


def order_from_file(
    forecast: pd.DataFrame, form: SupplierForm, content: bytes, filename: str, *, lead_time: int, username: str
) -> str:
    """The supplier's corrected file becomes a new order on the way; the earlier orders stay as they are."""
    preview, applied, parsed = file_preview(forecast, form, content)
    if preview["errors"]:
        raise ValueError(" ".join(preview["errors"]))
    if preview["already_saved"]:
        raise ValueError("Этот файл уже оформлен как заказ.")
    return _create_in_transit(applied, form, lead_time=lead_time, comment=order_file_comment(filename, parsed, form=form), username=username)
