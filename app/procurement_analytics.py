from __future__ import annotations

import math

import pandas as pd

from sales_analytics import build_abc_analysis, build_product_summary


PROCUREMENT_ITEM_COLUMNS = [
    "product",
    "supplier",
    "brand",
    "stock_on_hand",
    "stock_value",
    "stock_in_transit",
    "min_order_qty",
    "order_multiple",
    "lead_time_days",
    "notes",
]


def _classify_xyz(variation_pct: float) -> str:
    if pd.isna(variation_pct):
        return "Z"
    if variation_pct <= 25:
        return "X"
    if variation_pct <= 50:
        return "Y"
    return "Z"


def _safe_non_negative_number(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric >= 0 else 0.0


def _safe_signed_number(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0


def _safe_non_negative_int(value: object) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return 0
    return numeric if numeric >= 0 else 0


def _round_order_quantity(
    required_qty: float,
    *,
    min_order_qty: float,
    order_multiple: float,
) -> float:
    safe_required_qty = max(float(required_qty or 0), 0.0)
    safe_min_order_qty = max(float(min_order_qty or 0), 0.0)
    safe_order_multiple = max(float(order_multiple or 0), 0.0)

    if safe_required_qty <= 0:
        return 0.0

    target_qty = max(safe_required_qty, safe_min_order_qty)
    if safe_order_multiple > 0:
        return math.ceil(target_qty / safe_order_multiple) * safe_order_multiple
    return target_qty


def build_procurement_forecast(
    frame: pd.DataFrame,
    *,
    history_months: int = 6,
    coverage_days: int = 30,
    lead_time_days: int = 14,
    safety_days: int = 7,
    min_active_months: int = 2,
    procurement_items: pd.DataFrame | None = None,
    inbound_orders: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "product",
        "category",
        "supplier",
        "brand",
        "abc_class",
        "xyz_class",
        "priority",
        "stock_status",
        "demand_state",
        "active_months",
        "history_months_used",
        "last_month_qty",
        "avg_monthly_qty",
        "recent_avg_qty",
        "trend_factor",
        "forecast_qty",
        "avg_daily_qty",
        "stock_on_hand",
        "stock_value",
        "manual_stock_in_transit",
        "ordered_in_transit_qty",
        "stock_in_transit",
        "available_stock_qty",
        "open_order_count",
        "stock_coverage_days",
        "lead_time_days",
        "lead_time_requirement_qty",
        "coverage_requirement_qty",
        "safety_stock_qty",
        "gross_requirement_qty",
        "net_requirement_qty",
        "min_order_qty",
        "order_multiple",
        "recommended_order_qty",
        "demand_variability_pct",
        "sales_lines",
        "last_sale_date",
        "days_since_last_sale",
        "avg_revenue_per_unit",
        "avg_margin_per_unit",
        "forecast_revenue",
        "forecast_margin",
        "notes",
    ]
    if frame.empty or "product" not in frame.columns or "date" not in frame.columns or "quantity" not in frame.columns:
        return pd.DataFrame(columns=columns)

    import numpy as np

    safe_history_months = max(int(history_months), 1)
    safe_coverage_days = max(int(coverage_days), 0)
    safe_lead_time_days = max(int(lead_time_days), 0)
    safe_safety_days = max(int(safety_days), 0)
    safe_min_active_months = max(int(min_active_months), 1)

    working = frame.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date", "product"]).copy()
    working["product"] = working["product"].fillna("").astype(str).str.strip()
    working = working[working["product"].ne("")].copy()
    if working.empty:
        return pd.DataFrame(columns=columns)

    working["_product_key"] = working["product"].str.casefold()
    canonical_products = (
        working.sort_values("date")
        .drop_duplicates("_product_key", keep="last")
        .set_index("_product_key")["product"]
    )
    working["product"] = working["_product_key"].map(canonical_products)

    if "month" not in working.columns:
        working["month"] = working["date"].dt.to_period("M").dt.to_timestamp()
    else:
        working["month"] = pd.to_datetime(working["month"], errors="coerce").dt.to_period("M").dt.to_timestamp()

    if "month_label" not in working.columns:
        working["month_label"] = working["month"].dt.strftime("%Y-%m")

    for metric_column in ("quantity", "revenue", "margin"):
        if metric_column not in working.columns:
            working[metric_column] = float("nan")
        working[metric_column] = pd.to_numeric(working[metric_column], errors="coerce")
        if metric_column in {"quantity", "revenue"}:
            working[metric_column] = working[metric_column].fillna(0.0)

    # For procurement we only use positive demand lines, excluding returns.
    working = working[working["quantity"] > 0].copy()
    if working.empty:
        return pd.DataFrame(columns=columns)

    month_order = sorted(pd.to_datetime(working["month"].dropna().unique()).tolist())
    if not month_order:
        return pd.DataFrame(columns=columns)

    selected_months = month_order[-safe_history_months:]
    history = working[working["month"].isin(selected_months)].copy()
    if history.empty:
        return pd.DataFrame(columns=columns)

    monthly_by_product = (
        history.groupby(["product", "month"], as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            revenue=("revenue", "sum"),
            margin=("margin", "sum"),
        )
        .sort_values(["product", "month"])
        .reset_index(drop=True)
    )
    quantity_pivot = (
        monthly_by_product.pivot(index="product", columns="month", values="quantity")
        .reindex(columns=selected_months, fill_value=0.0)
        .fillna(0.0)
    )

    category_summary = pd.DataFrame(columns=["product", "category"])
    if "category" in history.columns:
        category_summary = (
            history.assign(category=history["category"].fillna("").astype(str).str.strip())
            .groupby(["product", "category"], as_index=False)
            .agg(category_quantity=("quantity", "sum"))
            .sort_values(["product", "category_quantity", "category"], ascending=[True, False, True])
            .drop_duplicates("product")
            [["product", "category"]]
        )

    supplier_summary = pd.DataFrame(columns=["product", "sales_supplier"])
    if "supplier" in history.columns:
        supplier_history = history.assign(
            sales_supplier=history["supplier"].fillna("").astype(str).str.strip()
        )
        supplier_history = supplier_history[
            supplier_history["sales_supplier"].ne("")
            & supplier_history["sales_supplier"].str.casefold().ne("не назначен")
        ]
        if not supplier_history.empty:
            supplier_summary = (
                supplier_history.groupby(["product", "sales_supplier"], as_index=False)
                .agg(supplier_quantity=("quantity", "sum"), supplier_revenue=("revenue", "sum"))
                .sort_values(
                    ["product", "supplier_quantity", "supplier_revenue", "sales_supplier"],
                    ascending=[True, False, False, True],
                )
                .drop_duplicates("product")
                [["product", "sales_supplier"]]
            )

    base_summary = (
        history.groupby("product", as_index=False)
        .agg(
            sales_lines=("product", "size"),
            last_sale_date=("date", "max"),
            total_quantity=("quantity", "sum"),
            total_revenue=("revenue", "sum"),
            total_margin=("margin", "sum"),
        )
        .reset_index(drop=True)
    )

    abc_quantity = (
        build_abc_analysis(build_product_summary(history), metric="quantity")[["group_name", "abc_class"]]
        .rename(columns={"group_name": "product"})
    )

    history_months_used = len(selected_months)
    records: list[dict[str, object]] = []

    for product_name, quantity_series in quantity_pivot.iterrows():
        qty_values = quantity_series.astype(float).to_numpy(dtype=float)
        last_month_qty = float(qty_values[-1]) if len(qty_values) else 0.0
        avg_monthly_qty = float(np.mean(qty_values)) if len(qty_values) else 0.0
        recent_window = min(3, len(qty_values))
        recent_avg_qty = float(np.mean(qty_values[-recent_window:])) if recent_window else avg_monthly_qty
        previous_slice = qty_values[-(recent_window * 2):-recent_window] if len(qty_values) > recent_window else np.array([])
        previous_avg_qty = float(np.mean(previous_slice)) if previous_slice.size else float("nan")

        trend_factor = 1.0
        if previous_slice.size and previous_avg_qty > 0:
            trend_factor = float(np.clip(recent_avg_qty / previous_avg_qty, 0.7, 1.45))
        elif avg_monthly_qty > 0 and last_month_qty > 0:
            trend_factor = float(np.clip(last_month_qty / avg_monthly_qty, 0.8, 1.25))

        base_forecast_qty = recent_avg_qty * 0.65 + avg_monthly_qty * 0.35
        forecast_qty = max(base_forecast_qty * trend_factor, 0.0)
        active_months = int((qty_values > 0).sum())
        demand_variability_pct = (
            float(np.std(qty_values, ddof=0) / avg_monthly_qty * 100)
            if avg_monthly_qty > 0
            else float("nan")
        )
        xyz_class = _classify_xyz(demand_variability_pct) if active_months >= safe_min_active_months else "Z"
        avg_daily_qty = forecast_qty / 30.4 if forecast_qty > 0 else 0.0

        records.append(
            {
                "product": str(product_name).strip(),
                "active_months": active_months,
                "history_months_used": history_months_used,
                "last_month_qty": last_month_qty,
                "avg_monthly_qty": avg_monthly_qty,
                "recent_avg_qty": recent_avg_qty,
                "trend_factor": trend_factor,
                "forecast_qty": forecast_qty,
                "avg_daily_qty": avg_daily_qty,
                "demand_variability_pct": demand_variability_pct,
                "xyz_class": xyz_class,
            }
        )

    procurement = pd.DataFrame.from_records(records)
    if procurement.empty:
        return pd.DataFrame(columns=columns)

    procurement = procurement.merge(base_summary, on="product", how="left")
    if not category_summary.empty:
        procurement = procurement.merge(category_summary, on="product", how="left")
    else:
        procurement["category"] = "Без категории"
    procurement["category"] = procurement["category"].fillna("").astype(str).str.strip().replace("", "Без категории")
    if not supplier_summary.empty:
        procurement = procurement.merge(supplier_summary, on="product", how="left")
    else:
        procurement["sales_supplier"] = ""
    procurement["sales_supplier"] = procurement["sales_supplier"].fillna("").astype(str).str.strip()

    procurement = procurement.merge(abc_quantity, on="product", how="left")
    procurement["abc_class"] = procurement["abc_class"].fillna("C")

    item_settings = procurement_items.copy() if procurement_items is not None else pd.DataFrame(columns=PROCUREMENT_ITEM_COLUMNS)
    if item_settings.empty:
        item_settings = pd.DataFrame(columns=PROCUREMENT_ITEM_COLUMNS)
    for column in PROCUREMENT_ITEM_COLUMNS:
        if column not in item_settings.columns:
            item_settings[column] = pd.NA
    item_settings["product"] = item_settings["product"].fillna("").astype(str).str.strip()
    item_settings = item_settings[item_settings["product"] != ""].copy()
    item_settings["_product_key"] = item_settings["product"].str.casefold()
    item_settings = item_settings.drop_duplicates(subset=["_product_key"], keep="last")
    item_settings["supplier"] = item_settings["supplier"].fillna("").astype(str).str.strip()
    item_settings["brand"] = item_settings["brand"].fillna("").astype(str).str.strip()
    item_settings["notes"] = item_settings["notes"].fillna("").astype(str).str.strip()
    for numeric_column in ("stock_on_hand", "stock_value"):
        item_settings[numeric_column] = item_settings[numeric_column].map(_safe_signed_number)
    for numeric_column in ("stock_in_transit", "min_order_qty", "order_multiple"):
        item_settings[numeric_column] = item_settings[numeric_column].map(_safe_non_negative_number)
    item_settings["lead_time_days"] = item_settings["lead_time_days"].map(_safe_non_negative_int)

    procurement["_product_key"] = (
        procurement["product"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    inventory_only_settings = item_settings[
        ~item_settings["_product_key"].isin(procurement["_product_key"])
    ].copy()
    if not inventory_only_settings.empty:
        inventory_only_records: list[dict[str, object]] = []
        for row in inventory_only_settings.to_dict(orient="records"):
            product_name = str(row.get("product", "")).strip()
            if not product_name:
                continue
            inventory_only_records.append(
                {
                    "product": product_name,
                    "_product_key": product_name.casefold(),
                    "active_months": 0,
                    "history_months_used": history_months_used,
                    "last_month_qty": 0.0,
                    "avg_monthly_qty": 0.0,
                    "recent_avg_qty": 0.0,
                    "trend_factor": 1.0,
                    "forecast_qty": 0.0,
                    "avg_daily_qty": 0.0,
                    "demand_variability_pct": float("nan"),
                    "xyz_class": "Z",
                    "sales_lines": 0,
                    "last_sale_date": pd.NaT,
                    "total_quantity": 0.0,
                    "total_revenue": 0.0,
                    "total_margin": 0.0,
                    "category": "Без категории",
                    "abc_class": "C",
                }
            )
        if inventory_only_records:
            procurement = pd.concat(
                [procurement, pd.DataFrame.from_records(inventory_only_records)],
                ignore_index=True,
            )

    settings_columns = [
        column for column in PROCUREMENT_ITEM_COLUMNS if column != "product"
    ]
    procurement = procurement.merge(
        item_settings[["_product_key", *settings_columns]],
        on="_product_key",
        how="left",
    )
    procurement["supplier"] = procurement["supplier"].fillna("").astype(str).str.strip()
    procurement["brand"] = (
        procurement["brand"].fillna("").astype(str).str.strip().replace("", "Не назначен")
    )
    missing_procurement_supplier = procurement["supplier"].eq("") | procurement["supplier"].str.casefold().eq("не назначен")
    sales_supplier = procurement["sales_supplier"].fillna("").astype(str).str.strip()
    procurement.loc[missing_procurement_supplier & sales_supplier.ne(""), "supplier"] = (
        sales_supplier.loc[missing_procurement_supplier & sales_supplier.ne("")]
    )
    procurement["notes"] = procurement["notes"].fillna("").astype(str).str.strip()
    for numeric_column in ("stock_on_hand", "stock_value"):
        procurement[numeric_column] = procurement[numeric_column].map(_safe_signed_number)
    for numeric_column in ("stock_in_transit", "min_order_qty", "order_multiple"):
        procurement[numeric_column] = procurement[numeric_column].map(_safe_non_negative_number)
    procurement["lead_time_days"] = procurement["lead_time_days"].map(_safe_non_negative_int)
    procurement["lead_time_days"] = procurement["lead_time_days"].where(procurement["lead_time_days"] > 0, safe_lead_time_days)

    procurement["manual_stock_in_transit"] = procurement["stock_in_transit"]
    inbound_summary = inbound_orders.copy() if inbound_orders is not None else pd.DataFrame(columns=["product", "ordered_in_transit_qty", "open_order_count"])
    if inbound_summary.empty:
        inbound_summary = pd.DataFrame(columns=["product", "ordered_in_transit_qty", "open_order_count"])
    for column in ("product", "ordered_in_transit_qty", "open_order_count"):
        if column not in inbound_summary.columns:
            inbound_summary[column] = pd.NA
    inbound_summary["product"] = inbound_summary["product"].fillna("").astype(str).str.strip()
    inbound_summary = inbound_summary[inbound_summary["product"] != ""].copy()
    inbound_summary["_product_key"] = inbound_summary["product"].str.casefold()
    inbound_summary["ordered_in_transit_qty"] = inbound_summary["ordered_in_transit_qty"].map(_safe_non_negative_number)
    inbound_summary["open_order_count"] = pd.to_numeric(inbound_summary["open_order_count"], errors="coerce").fillna(0).astype(int)
    inbound_summary = (
        inbound_summary.groupby("_product_key", as_index=False)
        .agg(
            ordered_in_transit_qty=("ordered_in_transit_qty", "sum"),
            open_order_count=("open_order_count", "sum"),
        )
    )

    procurement = procurement.merge(
        inbound_summary[["_product_key", "ordered_in_transit_qty", "open_order_count"]],
        on="_product_key",
        how="left",
    )
    procurement["ordered_in_transit_qty"] = procurement["ordered_in_transit_qty"].map(_safe_non_negative_number)
    procurement["open_order_count"] = pd.to_numeric(procurement["open_order_count"], errors="coerce").fillna(0).astype(int)
    procurement["stock_in_transit"] = procurement["manual_stock_in_transit"] + procurement["ordered_in_transit_qty"]
    procurement["lead_time_requirement_qty"] = procurement["avg_daily_qty"] * procurement["lead_time_days"]
    procurement["coverage_requirement_qty"] = procurement["avg_daily_qty"] * safe_coverage_days
    procurement["safety_stock_qty"] = procurement["avg_daily_qty"] * safe_safety_days
    procurement["gross_requirement_qty"] = (
        procurement["lead_time_requirement_qty"]
        + procurement["coverage_requirement_qty"]
        + procurement["safety_stock_qty"]
    )
    procurement["available_stock_qty"] = procurement["stock_on_hand"] + procurement["stock_in_transit"]
    procurement["net_requirement_qty"] = (
        procurement["gross_requirement_qty"] - procurement["available_stock_qty"]
    ).clip(lower=0.0)
    procurement["recommended_order_qty"] = procurement.apply(
        lambda row: _round_order_quantity(
            row.get("net_requirement_qty", 0.0),
            min_order_qty=row.get("min_order_qty", 0.0),
            order_multiple=row.get("order_multiple", 0.0),
        ),
        axis=1,
    )
    procurement["stock_coverage_days"] = procurement.apply(
        lambda row: (
            row["available_stock_qty"] / row["avg_daily_qty"]
            if float(row.get("avg_daily_qty", 0) or 0) > 0
            else float("nan")
        ),
        axis=1,
    )

    procurement["avg_revenue_per_unit"] = (
        procurement["total_revenue"] / procurement["total_quantity"].replace(0, pd.NA)
    )
    procurement["avg_margin_per_unit"] = (
        procurement["total_margin"] / procurement["total_quantity"].replace(0, pd.NA)
    )
    procurement["forecast_revenue"] = procurement["forecast_qty"] * procurement["avg_revenue_per_unit"].fillna(0.0)
    procurement["forecast_margin"] = procurement["forecast_qty"] * procurement["avg_margin_per_unit"].fillna(0.0)

    analysis_date = pd.to_datetime(history["date"].max(), errors="coerce")
    last_sale_dates = pd.to_datetime(procurement["last_sale_date"], errors="coerce")
    if pd.notna(analysis_date):
        procurement["days_since_last_sale"] = (analysis_date.normalize() - last_sale_dates.dt.normalize()).dt.days
    else:
        procurement["days_since_last_sale"] = pd.Series(pd.NA, index=procurement.index, dtype="Int64")

    abc_score = procurement["abc_class"].map({"A": 4, "B": 3, "C": 2}).fillna(1)
    xyz_score = procurement["xyz_class"].map({"X": 3, "Y": 2, "Z": 1}).fillna(1)
    trend_score = (procurement["trend_factor"] >= 1.1).astype(int)
    freshness_score = (procurement["days_since_last_sale"].fillna(9999) <= 45).astype(int)
    restock_score = (procurement["recommended_order_qty"] > 0).astype(int)
    shortage_score = (procurement["available_stock_qty"] < procurement["lead_time_requirement_qty"]).astype(int) * 2
    history_penalty = (procurement["active_months"] < safe_min_active_months).astype(int)
    procurement["priority_score"] = (
        abc_score
        + xyz_score
        + trend_score
        + freshness_score
        + restock_score
        + shortage_score
        - history_penalty
    )

    def _priority_label(row: pd.Series) -> str:
        if float(row.get("forecast_qty", 0) or 0) <= 0:
            return "Спящий"
        if int(row.get("active_months", 0) or 0) < safe_min_active_months:
            return "Новый"
        if float(row.get("recommended_order_qty", 0) or 0) > 0 and float(row.get("available_stock_qty", 0) or 0) <= 0:
            return "Критичный"
        score = float(row.get("priority_score", 0) or 0)
        if score >= 10:
            return "Критичный"
        if score >= 8:
            return "Высокий"
        if score >= 5:
            return "Средний"
        return "Низкий"

    def _stock_status(row: pd.Series) -> str:
        if float(row.get("forecast_qty", 0) or 0) <= 0:
            return "Нет спроса"
        available_qty = float(row.get("available_stock_qty", 0) or 0)
        lead_time_need = float(row.get("lead_time_requirement_qty", 0) or 0)
        recommended_qty = float(row.get("recommended_order_qty", 0) or 0)
        if available_qty <= 0 and recommended_qty > 0:
            return "Нет остатка"
        if available_qty < lead_time_need:
            return "Риск дефицита"
        if recommended_qty > 0:
            return "Нужно пополнение"
        return "Покрыто остатком"

    def _demand_state(row: pd.Series) -> str:
        if float(row.get("forecast_qty", 0) or 0) <= 0:
            return "Нет текущего спроса"
        if int(row.get("active_months", 0) or 0) < safe_min_active_months:
            return "Мало истории"
        if str(row.get("xyz_class", "")) == "Z":
            return "Нестабильный спрос"
        if float(row.get("trend_factor", 1) or 1) >= 1.15:
            return "Спрос ускоряется"
        return "Рабочий ассортимент"

    procurement["priority"] = procurement.apply(_priority_label, axis=1)
    procurement["stock_status"] = procurement.apply(_stock_status, axis=1)
    procurement["demand_state"] = procurement.apply(_demand_state, axis=1)

    priority_rank = {"Критичный": 5, "Высокий": 4, "Средний": 3, "Низкий": 2, "Новый": 1, "Спящий": 0}
    procurement["_priority_rank"] = procurement["priority"].map(priority_rank).fillna(0)
    procurement = procurement.sort_values(
        ["_priority_rank", "recommended_order_qty", "net_requirement_qty", "forecast_revenue"],
        ascending=[False, False, False, False],
        na_position="last",
        ignore_index=True,
    )
    procurement = procurement.drop(
        columns=["_priority_rank", "priority_score", "total_quantity", "total_revenue", "total_margin"],
        errors="ignore",
    )

    for column in columns:
        if column not in procurement.columns:
            procurement[column] = pd.NA

    return procurement[columns]


def build_procurement_stock_risk_frames(
    procurement_frame: pd.DataFrame,
    *,
    total_window_days: int,
) -> dict[str, object]:
    stock_numeric_columns = [
        "forecast_qty",
        "avg_daily_qty",
        "stock_on_hand",
        "manual_stock_in_transit",
        "ordered_in_transit_qty",
        "stock_in_transit",
        "available_stock_qty",
        "stock_coverage_days",
        "gross_requirement_qty",
        "net_requirement_qty",
        "recommended_order_qty",
        "lead_time_days",
        "days_since_last_sale",
    ]
    stock_risk_frame = procurement_frame.copy()
    for stock_column in stock_numeric_columns:
        if stock_column not in stock_risk_frame.columns:
            stock_risk_frame[stock_column] = pd.NA
        stock_risk_frame[stock_column] = pd.to_numeric(
            stock_risk_frame[stock_column],
            errors="coerce",
        )
        if stock_column not in {"stock_coverage_days", "days_since_last_sale"}:
            stock_risk_frame[stock_column] = stock_risk_frame[stock_column].fillna(0.0)

    if "priority" not in stock_risk_frame.columns:
        stock_risk_frame["priority"] = ""
    if "stock_status" not in stock_risk_frame.columns:
        stock_risk_frame["stock_status"] = ""

    stock_risk_frame["stock_coverage_days"] = stock_risk_frame["stock_coverage_days"].replace(
        [float("inf"), -float("inf")],
        pd.NA,
    )
    stock_coverage_compare = pd.to_numeric(
        stock_risk_frame["stock_coverage_days"],
        errors="coerce",
    ).fillna(-1.0)
    priority_rank_map = {
        "Критичный": 5,
        "Высокий": 4,
        "Средний": 3,
        "Низкий": 2,
        "Новый": 1,
        "Спящий": 0,
    }
    stock_risk_frame["_priority_rank"] = stock_risk_frame["priority"].map(priority_rank_map).fillna(0)
    shortage_statuses = ["Нет остатка", "Риск дефицита"]
    shortage_risks = stock_risk_frame[
        stock_risk_frame["stock_status"].isin(shortage_statuses)
    ].sort_values(
        ["_priority_rank", "recommended_order_qty", "net_requirement_qty", "forecast_qty"],
        ascending=[False, False, False, False],
        na_position="last",
    )
    out_of_stock_risks = stock_risk_frame[
        (stock_risk_frame["forecast_qty"] > 0)
        & (stock_risk_frame["available_stock_qty"] <= 0)
    ].copy()
    reorder_risks = stock_risk_frame[
        stock_risk_frame["recommended_order_qty"] > 0
    ].sort_values(
        ["recommended_order_qty", "net_requirement_qty", "_priority_rank"],
        ascending=[False, False, False],
        na_position="last",
    )
    overstock_days_threshold = max(float(total_window_days) * 2, 90.0)
    overstock_risks = stock_risk_frame[
        (stock_risk_frame["forecast_qty"] > 0)
        & (stock_risk_frame["available_stock_qty"] > 0)
        & (stock_coverage_compare > overstock_days_threshold)
    ].sort_values(
        ["stock_coverage_days", "available_stock_qty"],
        ascending=[False, False],
        na_position="last",
    )
    dormant_stock_risks = stock_risk_frame[
        (stock_risk_frame["forecast_qty"] <= 0)
        & (stock_risk_frame["available_stock_qty"] > 0)
    ].sort_values(
        ["available_stock_qty", "stock_on_hand"],
        ascending=[False, False],
        na_position="last",
    )

    report_parts = []
    for risk_label, risk_source in [
        ("Дефицит", shortage_risks),
        ("Излишек", overstock_risks),
        ("Неликвид", dormant_stock_risks),
        ("К заказу", reorder_risks),
    ]:
        if risk_source.empty:
            continue
        risk_part = risk_source.copy()
        risk_part.insert(0, "risk_type", risk_label)
        report_parts.append(risk_part)

    stock_risk_report = (
        pd.concat(report_parts, ignore_index=True)
        if report_parts
        else pd.DataFrame(columns=["risk_type", *stock_risk_frame.columns])
    )
    return {
        "all": stock_risk_frame,
        "shortage": shortage_risks,
        "out_of_stock": out_of_stock_risks,
        "reorder": reorder_risks,
        "overstock": overstock_risks,
        "dormant": dormant_stock_risks,
        "report": stock_risk_report,
        "overstock_days_threshold": overstock_days_threshold,
    }


def build_procurement_overview(procurement_frame: pd.DataFrame) -> dict[str, float]:
    if procurement_frame.empty:
        return {
            "sku_count": 0.0,
            "active_sku_count": 0.0,
            "high_priority_count": 0.0,
            "reorder_sku_count": 0.0,
            "critical_stock_count": 0.0,
            "stable_sku_count": 0.0,
            "supplier_count": 0.0,
            "brand_count": 0.0,
            "available_stock_qty_total": 0.0,
            "ordered_in_transit_qty_total": 0.0,
            "forecast_qty_total": 0.0,
            "coverage_requirement_qty_total": 0.0,
            "gross_requirement_qty_total": 0.0,
            "net_requirement_qty_total": 0.0,
            "recommended_order_qty_total": 0.0,
            "forecast_revenue_total": float("nan"),
        }

    high_priority_mask = procurement_frame["priority"].isin(["Критичный", "Высокий"])
    active_mask = procurement_frame["forecast_qty"].fillna(0) > 0
    stable_mask = procurement_frame["xyz_class"] == "X"
    reorder_mask = procurement_frame["recommended_order_qty"].fillna(0) > 0
    critical_stock_mask = procurement_frame["stock_status"].isin(["Нет остатка", "Риск дефицита"])
    supplier_count = procurement_frame["supplier"].fillna("").astype(str).str.strip().replace("", pd.NA).nunique(dropna=True)
    brand_count = (
        procurement_frame.get("brand", pd.Series(dtype="object"))
        .fillna("")
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "Не назначен": pd.NA})
        .nunique(dropna=True)
    )
    forecast_revenue_total = procurement_frame["forecast_revenue"].sum(min_count=1)

    return {
        "sku_count": float(len(procurement_frame)),
        "active_sku_count": float(active_mask.sum()),
        "high_priority_count": float(high_priority_mask.sum()),
        "reorder_sku_count": float(reorder_mask.sum()),
        "critical_stock_count": float(critical_stock_mask.sum()),
        "stable_sku_count": float(stable_mask.sum()),
        "supplier_count": float(supplier_count),
        "brand_count": float(brand_count),
        "available_stock_qty_total": float(procurement_frame["available_stock_qty"].sum()),
        "ordered_in_transit_qty_total": float(procurement_frame["ordered_in_transit_qty"].sum()),
        "forecast_qty_total": float(procurement_frame["forecast_qty"].sum()),
        "coverage_requirement_qty_total": float(procurement_frame["coverage_requirement_qty"].sum()),
        "gross_requirement_qty_total": float(procurement_frame["gross_requirement_qty"].sum()),
        "net_requirement_qty_total": float(procurement_frame["net_requirement_qty"].sum()),
        "recommended_order_qty_total": float(procurement_frame["recommended_order_qty"].sum()),
        "forecast_revenue_total": float(forecast_revenue_total) if pd.notna(forecast_revenue_total) else float("nan"),
    }


def build_procurement_supplier_summary(procurement_frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "supplier",
        "sku_count",
        "reorder_sku_count",
        "critical_sku_count",
        "recommended_order_qty",
        "net_requirement_qty",
        "available_stock_qty",
        "forecast_qty",
        "forecast_revenue",
        "max_lead_time_days",
    ]
    if procurement_frame.empty:
        return pd.DataFrame(columns=columns)

    working = procurement_frame.copy()
    working["supplier"] = (
        working["supplier"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", "Не назначен")
    )
    working["reorder_flag"] = working["recommended_order_qty"].fillna(0) > 0
    working["critical_flag"] = working["stock_status"].isin(["Нет остатка", "Риск дефицита"])

    summary = (
        working.groupby("supplier", as_index=False)
        .agg(
            sku_count=("product", "size"),
            reorder_sku_count=("reorder_flag", "sum"),
            critical_sku_count=("critical_flag", "sum"),
            recommended_order_qty=("recommended_order_qty", "sum"),
            net_requirement_qty=("net_requirement_qty", "sum"),
            available_stock_qty=("available_stock_qty", "sum"),
            forecast_qty=("forecast_qty", "sum"),
            forecast_revenue=("forecast_revenue", "sum"),
            max_lead_time_days=("lead_time_days", "max"),
        )
        .sort_values(
            ["recommended_order_qty", "critical_sku_count", "reorder_sku_count", "forecast_revenue"],
            ascending=[False, False, False, False],
            ignore_index=True,
        )
    )

    return summary[columns]
