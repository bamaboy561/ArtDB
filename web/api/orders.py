"""Supplier orders for the API: the same forms, files and rules as on the old site."""
from __future__ import annotations

from datetime import date, timedelta
import math

import pandas as pd

import data  # noqa: F401  (puts app/ on the import path)
from procurement_analytics import supplier_lead_time
from supplier_order_forms import (
    SECTION_BOARDS,
    SUPPLIER_FORMS,
    SupplierForm,
    build_order_form_rows,
    build_supplier_order_workbook,
    generic_supplier_forms,
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
        "rows": [
            {field: _clean(record.get(field)) for field in ROW_FIELDS}
            for record in rows.to_dict(orient="records")
        ],
    }


def order_workbook(forecast: pd.DataFrame, form: SupplierForm, quantities: dict[str, tuple[int, int]]) -> bytes:
    """The supplier's file with the quantities as edited on the screen."""
    rows = build_order_form_rows(forecast, form=form)
    rows["quantity"] = [quantities.get(row_id, (quantity, 0))[0] for row_id, quantity in zip(rows["row_id"], rows["quantity"])]
    rows["edge_or_sheets"] = [
        quantities.get(row_id, (0, edge))[1] for row_id, edge in zip(rows["row_id"], rows["edge_or_sheets"])
    ]
    return build_supplier_order_workbook(rows, form=form)
