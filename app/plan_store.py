from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
from typing import Any

import pandas as pd

from db import database_enabled, ensure_database_ready, get_db_connection, isoformat_seconds


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR.parent / "data"))).resolve()
PLANS_PATH = DATA_DIR / "monthly_plans.csv"

PLAN_COLUMNS = [
    "plan_month",
    "salon",
    "revenue_plan",
    "margin_plan",
    "quantity_plan",
    "updated_at",
    "updated_by",
]


def ensure_plan_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PLANS_PATH.exists():
        pd.DataFrame(columns=PLAN_COLUMNS).to_csv(PLANS_PATH, index=False, encoding="utf-8-sig")


def normalize_plan_month(value: date | datetime | pd.Timestamp | str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("Не удалось определить месяц плана.")
    return date(int(parsed.year), int(parsed.month), 1)


def normalize_plan_salon(salon: str | None) -> str:
    return str(salon or "").strip()


def _normalize_plan_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in PLAN_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[PLAN_COLUMNS]
    normalized["plan_month"] = pd.to_datetime(normalized["plan_month"], errors="coerce").dt.normalize()
    normalized["salon"] = normalized["salon"].fillna("").astype(str).str.strip()
    for metric_column in ("revenue_plan", "margin_plan", "quantity_plan"):
        normalized[metric_column] = pd.to_numeric(normalized[metric_column], errors="coerce")
    normalized["updated_at"] = normalized["updated_at"].fillna("").astype(str)
    normalized["updated_by"] = normalized["updated_by"].fillna("").astype(str).str.strip()
    normalized = normalized.dropna(subset=["plan_month"]).sort_values(["plan_month", "salon"]).reset_index(drop=True)
    return normalized


def load_monthly_plans() -> pd.DataFrame:
    ensure_plan_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        plan_month,
                        COALESCE(salon, '') AS salon,
                        revenue_plan,
                        margin_plan,
                        quantity_plan,
                        updated_at,
                        COALESCE(updated_by, '') AS updated_by
                    FROM monthly_plans
                    ORDER BY plan_month, salon
                    """
                )
                rows = cursor.fetchall()
        frame = pd.DataFrame(
            [
                {
                    "plan_month": row.get("plan_month"),
                    "salon": str(row.get("salon", "")).strip(),
                    "revenue_plan": row.get("revenue_plan"),
                    "margin_plan": row.get("margin_plan"),
                    "quantity_plan": row.get("quantity_plan"),
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                    "updated_by": str(row.get("updated_by", "")).strip(),
                }
                for row in rows
            ],
            columns=PLAN_COLUMNS,
        )
        return _normalize_plan_frame(frame)

    if not PLANS_PATH.exists():
        return pd.DataFrame(columns=PLAN_COLUMNS)
    frame = pd.read_csv(PLANS_PATH, encoding="utf-8-sig")
    return _normalize_plan_frame(frame)


def upsert_monthly_plan(
    *,
    plan_month: date | datetime | pd.Timestamp | str,
    salon: str = "",
    revenue_plan: float | int | None = None,
    margin_plan: float | int | None = None,
    quantity_plan: float | int | None = None,
    updated_by: str = "",
) -> dict[str, Any]:
    ensure_plan_store()
    normalized_month = normalize_plan_month(plan_month)
    normalized_salon = normalize_plan_salon(salon)
    timestamp = datetime.now().isoformat(timespec="seconds")

    revenue_value = None if revenue_plan is None or pd.isna(revenue_plan) else float(revenue_plan)
    margin_value = None if margin_plan is None or pd.isna(margin_plan) else float(margin_plan)
    quantity_value = None if quantity_plan is None or pd.isna(quantity_plan) else float(quantity_plan)

    if revenue_value is None and margin_value is None and quantity_value is None:
        raise ValueError("Укажите хотя бы одно плановое значение: выручку, маржу или количество.")

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO monthly_plans (
                        plan_month,
                        salon,
                        revenue_plan,
                        margin_plan,
                        quantity_plan,
                        updated_by,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (plan_month, salon) DO UPDATE SET
                        revenue_plan = EXCLUDED.revenue_plan,
                        margin_plan = EXCLUDED.margin_plan,
                        quantity_plan = EXCLUDED.quantity_plan,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    RETURNING
                        plan_month,
                        COALESCE(salon, '') AS salon,
                        revenue_plan,
                        margin_plan,
                        quantity_plan,
                        updated_at,
                        COALESCE(updated_by, '') AS updated_by
                    """,
                    (
                        normalized_month.isoformat(),
                        normalized_salon,
                        revenue_value,
                        margin_value,
                        quantity_value,
                        str(updated_by).strip(),
                    ),
                )
                row = cursor.fetchone()
        return {
            "plan_month": isoformat_seconds(row.get("plan_month")),
            "salon": str(row.get("salon", "")).strip(),
            "revenue_plan": row.get("revenue_plan"),
            "margin_plan": row.get("margin_plan"),
            "quantity_plan": row.get("quantity_plan"),
            "updated_at": isoformat_seconds(row.get("updated_at")),
            "updated_by": str(row.get("updated_by", "")).strip(),
        }

    frame = load_monthly_plans()
    mask = (
        frame["plan_month"].dt.date == normalized_month
    ) & (frame["salon"].astype(str).str.casefold() == normalized_salon.casefold())
    record = {
        "plan_month": pd.Timestamp(normalized_month),
        "salon": normalized_salon,
        "revenue_plan": revenue_value,
        "margin_plan": margin_value,
        "quantity_plan": quantity_value,
        "updated_at": timestamp,
        "updated_by": str(updated_by).strip(),
    }
    if mask.any():
        for key, value in record.items():
            frame.loc[mask, key] = value
    else:
        frame = pd.concat([frame, pd.DataFrame([record])], ignore_index=True)
    frame = _normalize_plan_frame(frame)
    frame.to_csv(PLANS_PATH, index=False, encoding="utf-8-sig")
    return {
        "plan_month": normalized_month.isoformat(),
        "salon": normalized_salon,
        "revenue_plan": revenue_value,
        "margin_plan": margin_value,
        "quantity_plan": quantity_value,
        "updated_at": timestamp,
        "updated_by": str(updated_by).strip(),
    }


def delete_monthly_plan(*, plan_month: date | datetime | pd.Timestamp | str, salon: str = "") -> bool:
    ensure_plan_store()
    normalized_month = normalize_plan_month(plan_month)
    normalized_salon = normalize_plan_salon(salon)

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM monthly_plans
                    WHERE plan_month = %s AND COALESCE(salon, '') = %s
                    """,
                    (normalized_month.isoformat(), normalized_salon),
                )
                deleted = int(cursor.rowcount or 0)
        return deleted > 0

    frame = load_monthly_plans()
    if frame.empty:
        return False
    mask = (
        frame["plan_month"].dt.date == normalized_month
    ) & (frame["salon"].astype(str).str.casefold() == normalized_salon.casefold())
    deleted = int(mask.sum())
    if deleted:
        frame = frame.loc[~mask].copy()
        frame = _normalize_plan_frame(frame)
        frame.to_csv(PLANS_PATH, index=False, encoding="utf-8-sig")
    return deleted > 0
