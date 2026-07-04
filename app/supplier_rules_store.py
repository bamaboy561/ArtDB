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
SUPPLIER_KEYWORD_RULES_PATH = DATA_DIR / "supplier_keyword_rules.json"
SUPPLIER_PRODUCT_ASSIGNMENTS_PATH = DATA_DIR / "supplier_product_assignments.json"

KEYWORD_RULE_COLUMNS = [
    "rule_id",
    "supplier",
    "keyword",
    "is_active",
    "updated_by",
    "updated_at",
]
PRODUCT_ASSIGNMENT_COLUMNS = [
    "product_key",
    "product",
    "supplier",
    "updated_by",
    "updated_at",
]


def ensure_supplier_rules_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return

    DATA_DIR.mkdir(exist_ok=True)
    if not SUPPLIER_KEYWORD_RULES_PATH.exists():
        SUPPLIER_KEYWORD_RULES_PATH.write_text("[]", encoding="utf-8")
    if not SUPPLIER_PRODUCT_ASSIGNMENTS_PATH.exists():
        SUPPLIER_PRODUCT_ASSIGNMENTS_PATH.write_text("[]", encoding="utf-8")


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = []
    return [record for record in payload if isinstance(record, dict)] if isinstance(payload, list) else []


def _write_json_records(path: Path, records: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_keyword_rule(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(record.get("rule_id", "")).strip(),
        "supplier": str(record.get("supplier", "")).strip(),
        "keyword": str(record.get("keyword", "")).strip(),
        "is_active": bool(record.get("is_active", True)),
        "updated_by": str(record.get("updated_by", "")).strip(),
        "updated_at": str(record.get("updated_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
    }


def _normalize_product_assignment(record: dict[str, Any]) -> dict[str, Any]:
    product = str(record.get("product", "")).strip()
    product_key = str(record.get("product_key", "")).strip() or product
    return {
        "product_key": product_key,
        "product": product,
        "supplier": str(record.get("supplier", "")).strip(),
        "updated_by": str(record.get("updated_by", "")).strip(),
        "updated_at": str(record.get("updated_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
    }


def load_supplier_keyword_rules(*, include_inactive: bool = False) -> pd.DataFrame:
    ensure_supplier_rules_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        rule_id,
                        supplier,
                        keyword,
                        is_active,
                        updated_by,
                        updated_at
                    FROM supplier_keyword_rules
                    WHERE %s OR is_active = TRUE
                    ORDER BY LOWER(supplier), LOWER(keyword)
                    """,
                    (include_inactive,),
                )
                rows = cursor.fetchall()
        records = [
            _normalize_keyword_rule(
                {
                    **row,
                    "rule_id": row.get("rule_id"),
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                }
            )
            for row in rows
        ]
        return pd.DataFrame(records, columns=KEYWORD_RULE_COLUMNS)

    records = [
        _normalize_keyword_rule(record)
        for record in _load_json_records(SUPPLIER_KEYWORD_RULES_PATH)
    ]
    if not include_inactive:
        records = [record for record in records if record["is_active"]]
    records = sorted(records, key=lambda item: (item["supplier"].casefold(), item["keyword"].casefold()))
    return pd.DataFrame(records, columns=KEYWORD_RULE_COLUMNS)


def replace_supplier_keyword_rules(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_supplier_rules_store()
    updated_at = datetime.now().isoformat(timespec="seconds")

    records: list[dict[str, Any]] = []
    seen_keywords: set[str] = set()
    if not frame.empty:
        for row in frame.to_dict(orient="records"):
            normalized = _normalize_keyword_rule(
                {
                    **row,
                    "updated_by": updated_by,
                    "updated_at": updated_at,
                }
            )
            keyword_key = normalized["keyword"].casefold()
            if not normalized["supplier"] or not normalized["keyword"] or keyword_key in seen_keywords:
                continue
            seen_keywords.add(keyword_key)
            records.append(normalized)

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM supplier_keyword_rules")
                for record in records:
                    cursor.execute(
                        """
                        INSERT INTO supplier_keyword_rules (
                            supplier,
                            keyword,
                            is_active,
                            updated_by,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            record["supplier"],
                            record["keyword"],
                            record["is_active"],
                            record["updated_by"],
                            record["updated_at"],
                        ),
                    )
        return len(records)

    for index, record in enumerate(records, start=1):
        record["rule_id"] = str(index)
    _write_json_records(SUPPLIER_KEYWORD_RULES_PATH, records)
    return len(records)


def load_supplier_product_assignments() -> pd.DataFrame:
    ensure_supplier_rules_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        product_key,
                        product,
                        supplier,
                        updated_by,
                        updated_at
                    FROM supplier_product_assignments
                    ORDER BY LOWER(supplier), LOWER(product)
                    """
                )
                rows = cursor.fetchall()
        records = [
            _normalize_product_assignment(
                {
                    **row,
                    "updated_at": isoformat_seconds(row.get("updated_at")),
                }
            )
            for row in rows
        ]
        return pd.DataFrame(records, columns=PRODUCT_ASSIGNMENT_COLUMNS)

    records = [
        _normalize_product_assignment(record)
        for record in _load_json_records(SUPPLIER_PRODUCT_ASSIGNMENTS_PATH)
    ]
    records = [record for record in records if record["product_key"] and record["supplier"]]
    records = sorted(records, key=lambda item: (item["supplier"].casefold(), item["product"].casefold()))
    return pd.DataFrame(records, columns=PRODUCT_ASSIGNMENT_COLUMNS)


def upsert_supplier_product_assignments(frame: pd.DataFrame, *, updated_by: str) -> int:
    ensure_supplier_rules_store()
    if frame.empty:
        return 0

    updated_at = datetime.now().isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        normalized = _normalize_product_assignment(
            {
                **row,
                "updated_by": updated_by,
                "updated_at": updated_at,
            }
        )
        if normalized["product_key"] and normalized["supplier"]:
            records.append(normalized)

    if not records:
        return 0

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(
                        """
                        INSERT INTO supplier_product_assignments (
                            product_key,
                            product,
                            supplier,
                            updated_by,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (product_key) DO UPDATE SET
                            product = EXCLUDED.product,
                            supplier = EXCLUDED.supplier,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            record["product_key"],
                            record["product"],
                            record["supplier"],
                            record["updated_by"],
                            record["updated_at"],
                        ),
                    )
        return len(records)

    existing = {
        record["product_key"].casefold(): record
        for record in load_supplier_product_assignments().to_dict(orient="records")
        if str(record.get("product_key", "")).strip()
    }
    for record in records:
        existing[record["product_key"].casefold()] = record

    merged_records = sorted(existing.values(), key=lambda item: (item["supplier"].casefold(), item["product"].casefold()))
    _write_json_records(SUPPLIER_PRODUCT_ASSIGNMENTS_PATH, merged_records)
    return len(records)
