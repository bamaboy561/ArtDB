from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from typing import Any, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - local fallback without postgres client
    psycopg = None
    dict_row = None


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PGCRYPTO_KEY_ENV = "APP_PGCRYPTO_KEY"
PGCRYPTO_OPTIONS = "cipher-algo=aes256,compress-algo=1"
SUPPLIER_TO_BRAND_MIGRATION_SERVICE = "supplier_assignments_to_brands_v1"
SUPPLIER_ASSIGNMENT_RESTORE_SERVICE = "supplier_assignments_restore_v1"
DATABASE_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS salons (
    name TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    salon TEXT NOT NULL DEFAULT '',
    email_encrypted BYTEA,
    phone_encrypted BYTEA,
    email_hash TEXT,
    phone_hash TEXT,
    salt TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique
    ON users (email_hash)
    WHERE email_hash IS NOT NULL AND email_hash <> '';

CREATE UNIQUE INDEX IF NOT EXISTS users_phone_unique
    ON users (phone_hash)
    WHERE phone_hash IS NOT NULL AND phone_hash <> '';

CREATE TABLE IF NOT EXISTS auth_sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_sessions_username_idx
    ON auth_sessions (username);

CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx
    ON auth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS uploads (
    upload_id TEXT PRIMARY KEY,
    salon TEXT NOT NULL REFERENCES salons(name) ON DELETE CASCADE,
    report_date DATE NOT NULL,
    source_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    csv_separator TEXT NOT NULL DEFAULT ';',
    csv_encoding TEXT NOT NULL DEFAULT 'utf-8',
    sheet_name TEXT NOT NULL DEFAULT '',
    mapping_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uploads_salon_report_date_unique
    ON uploads (salon, report_date);

CREATE INDEX IF NOT EXISTS uploads_salon_idx
    ON uploads (salon);

CREATE INDEX IF NOT EXISTS uploads_report_date_idx
    ON uploads (report_date);

CREATE TABLE IF NOT EXISTS service_state (
    service_name TEXT PRIMARY KEY,
    last_run_key TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monthly_plans (
    plan_id BIGSERIAL PRIMARY KEY,
    plan_month DATE NOT NULL,
    salon TEXT NOT NULL DEFAULT '',
    revenue_plan DOUBLE PRECISION,
    margin_plan DOUBLE PRECISION,
    quantity_plan DOUBLE PRECISION,
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (plan_month, salon)
);

CREATE INDEX IF NOT EXISTS monthly_plans_month_idx
    ON monthly_plans (plan_month);

CREATE INDEX IF NOT EXISTS monthly_plans_salon_idx
    ON monthly_plans (salon);

CREATE TABLE IF NOT EXISTS procurement_items (
    product TEXT PRIMARY KEY,
    supplier TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    stock_on_hand DOUBLE PRECISION NOT NULL DEFAULT 0,
    stock_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    stock_in_transit DOUBLE PRECISION NOT NULL DEFAULT 0,
    min_order_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
    order_multiple DOUBLE PRECISION NOT NULL DEFAULT 0,
    lead_time_days INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS procurement_items_supplier_idx
    ON procurement_items (supplier);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    total_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    filename TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL DEFAULT '',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS inventory_snapshots_uploaded_at_idx
    ON inventory_snapshots (uploaded_at DESC, snapshot_id DESC);

CREATE TABLE IF NOT EXISTS supplier_keyword_rules (
    rule_id BIGSERIAL PRIMARY KEY,
    supplier TEXT NOT NULL DEFAULT '',
    keyword TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS supplier_keyword_rules_keyword_unique
    ON supplier_keyword_rules (LOWER(keyword))
    WHERE keyword <> '';

CREATE INDEX IF NOT EXISTS supplier_keyword_rules_active_idx
    ON supplier_keyword_rules (is_active, LOWER(supplier));

CREATE TABLE IF NOT EXISTS supplier_product_assignments (
    product_key TEXT PRIMARY KEY,
    product TEXT NOT NULL DEFAULT '',
    supplier TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS supplier_product_assignments_supplier_idx
    ON supplier_product_assignments (LOWER(supplier));

CREATE TABLE IF NOT EXISTS procurement_orders (
    order_id TEXT PRIMARY KEY,
    supplier TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    order_date DATE,
    expected_date DATE,
    comment TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS procurement_orders_status_idx
    ON procurement_orders (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS procurement_orders_supplier_idx
    ON procurement_orders (supplier, updated_at DESC);

CREATE TABLE IF NOT EXISTS procurement_order_items (
    line_id BIGSERIAL PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES procurement_orders(order_id) ON DELETE CASCADE,
    product TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS procurement_order_items_order_idx
    ON procurement_order_items (order_id);

CREATE INDEX IF NOT EXISTS procurement_order_items_product_idx
    ON procurement_order_items (product);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    "timestamp" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip INET,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS audit_logs_user_idx
    ON audit_logs (user_id);

CREATE INDEX IF NOT EXISTS audit_logs_timestamp_idx
    ON audit_logs ("timestamp" DESC);

CREATE INDEX IF NOT EXISTS audit_logs_active_idx
    ON audit_logs (is_archived, "timestamp" DESC);
"""

_DB_READY = False


def database_enabled() -> bool:
    return bool(DATABASE_URL)


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            "Для PostgreSQL-режима установите зависимость 'psycopg[binary]' из requirements.txt."
        )


def get_pgcrypto_key() -> str:
    key = os.getenv(PGCRYPTO_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"{PGCRYPTO_KEY_ENV} не задан. Укажите секретный ключ для pgcrypto в окружении приложения."
        )
    return key


@contextmanager
def get_db_connection(*, autocommit: bool = False) -> Iterator[Any]:
    if not database_enabled():
        raise RuntimeError("DATABASE_URL не задан. PostgreSQL-режим не активирован.")

    _require_psycopg()
    connection = psycopg.connect(
        DATABASE_URL,
        autocommit=autocommit,
        row_factory=dict_row,
        prepare_threshold=None,
    )
    try:
        yield connection
        if not autocommit:
            connection.commit()
    except Exception:
        if not autocommit:
            connection.rollback()
        raise
    finally:
        connection.close()


def _column_exists(cursor: Any, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = CURRENT_SCHEMA()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def _migrate_users_table(cursor: Any) -> None:
    pgcrypto_key = get_pgcrypto_key()

    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_encrypted BYTEA")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_encrypted BYTEA")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_hash TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_hash TEXT")
    cursor.execute("DROP INDEX IF EXISTS users_email_unique")
    cursor.execute("DROP INDEX IF EXISTS users_phone_unique")

    if _column_exists(cursor, "users", "email"):
        cursor.execute(
            """
            UPDATE users
            SET
                email_encrypted = CASE
                    WHEN email_encrypted IS NULL AND email IS NOT NULL AND email <> ''
                    THEN pgp_sym_encrypt(LOWER(email), %s, %s)
                    ELSE email_encrypted
                END,
                email_hash = CASE
                    WHEN email_hash IS NULL AND email IS NOT NULL AND email <> ''
                    THEN ENCODE(DIGEST(LOWER(email), 'sha256'), 'hex')
                    ELSE email_hash
                END
            WHERE email IS NOT NULL AND email <> ''
            """,
            (pgcrypto_key, PGCRYPTO_OPTIONS),
        )
        cursor.execute("ALTER TABLE users DROP COLUMN email")

    if _column_exists(cursor, "users", "phone"):
        cursor.execute(
            """
            UPDATE users
            SET
                phone_encrypted = CASE
                    WHEN phone_encrypted IS NULL AND phone IS NOT NULL AND phone <> ''
                    THEN pgp_sym_encrypt(phone, %s, %s)
                    ELSE phone_encrypted
                END,
                phone_hash = CASE
                    WHEN phone_hash IS NULL AND phone IS NOT NULL AND phone <> ''
                    THEN ENCODE(DIGEST(phone, 'sha256'), 'hex')
                    ELSE phone_hash
                END
            WHERE phone IS NOT NULL AND phone <> ''
            """,
            (pgcrypto_key, PGCRYPTO_OPTIONS),
        )
        cursor.execute("ALTER TABLE users DROP COLUMN phone")
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique
            ON users (email_hash)
            WHERE email_hash IS NOT NULL AND email_hash <> ''
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS users_phone_unique
            ON users (phone_hash)
            WHERE phone_hash IS NOT NULL AND phone_hash <> ''
        """
    )


def _migrate_audit_logs_table(cursor: Any) -> None:
    if _column_exists(cursor, "audit_logs", "event_timestamp") and not _column_exists(cursor, "audit_logs", "timestamp"):
        cursor.execute('ALTER TABLE audit_logs RENAME COLUMN event_timestamp TO "timestamp"')
    cursor.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("DROP INDEX IF EXISTS audit_logs_timestamp_idx")
    cursor.execute("DROP INDEX IF EXISTS audit_logs_active_idx")
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS audit_logs_timestamp_idx ON audit_logs ("timestamp" DESC)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS audit_logs_active_idx ON audit_logs (is_archived, "timestamp" DESC)'
    )


def _migrate_procurement_items_table(cursor: Any) -> None:
    cursor.execute(
        "ALTER TABLE procurement_items ADD COLUMN IF NOT EXISTS brand TEXT NOT NULL DEFAULT ''"
    )
    cursor.execute(
        "ALTER TABLE procurement_items ADD COLUMN IF NOT EXISTS stock_value DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS procurement_items_brand_idx ON procurement_items (brand)"
    )


def _migrate_supplier_assignments_to_brands(cursor: Any) -> None:
    cursor.execute(
        "SELECT last_run_key FROM service_state WHERE service_name = %s",
        (SUPPLIER_TO_BRAND_MIGRATION_SERVICE,),
    )
    migration_state = cursor.fetchone()
    if migration_state and str(migration_state.get("last_run_key", "")).strip() == "complete":
        return

    # These assignments were previously entered as suppliers, but represent product brands.
    cursor.execute(
        """
        INSERT INTO procurement_items (
            product,
            brand,
            updated_by,
            updated_at
        )
        SELECT
            COALESCE(NULLIF(BTRIM(product), ''), BTRIM(product_key)),
            BTRIM(supplier),
            'supplier-to-brand-migration',
            NOW()
        FROM supplier_product_assignments
        WHERE BTRIM(supplier) <> ''
          AND COALESCE(NULLIF(BTRIM(product), ''), BTRIM(product_key)) <> ''
        ON CONFLICT (product) DO UPDATE SET
            brand = CASE
                WHEN BTRIM(procurement_items.brand) = '' THEN EXCLUDED.brand
                ELSE procurement_items.brand
            END,
            updated_by = CASE
                WHEN BTRIM(procurement_items.brand) = '' THEN EXCLUDED.updated_by
                ELSE procurement_items.updated_by
            END,
            updated_at = CASE
                WHEN BTRIM(procurement_items.brand) = '' THEN NOW()
                ELSE procurement_items.updated_at
            END
        """
    )
    cursor.execute(
        """
        UPDATE procurement_items
        SET
            brand = CASE
                WHEN BTRIM(brand) = '' THEN BTRIM(supplier)
                ELSE brand
            END,
            supplier = '',
            updated_by = 'supplier-to-brand-migration',
            updated_at = NOW()
        WHERE BTRIM(supplier) <> ''
          AND (
              BTRIM(brand) = ''
              OR LOWER(BTRIM(brand)) = LOWER(BTRIM(supplier))
          )
        """
    )
    cursor.execute("DELETE FROM supplier_product_assignments")
    cursor.execute(
        """
        INSERT INTO service_state (service_name, last_run_key, updated_at)
        VALUES (%s, 'complete', NOW())
        ON CONFLICT (service_name) DO UPDATE SET
            last_run_key = EXCLUDED.last_run_key,
            updated_at = NOW()
        """,
        (SUPPLIER_TO_BRAND_MIGRATION_SERVICE,),
    )


def _restore_migrated_supplier_assignments(cursor: Any) -> None:
    cursor.execute(
        "SELECT last_run_key FROM service_state WHERE service_name = %s",
        (SUPPLIER_ASSIGNMENT_RESTORE_SERVICE,),
    )
    restore_state = cursor.fetchone()
    if restore_state and str(restore_state.get("last_run_key", "")).strip() == "complete":
        return

    cursor.execute(
        "SELECT last_run_key FROM service_state WHERE service_name = %s",
        (SUPPLIER_TO_BRAND_MIGRATION_SERVICE,),
    )
    migration_state = cursor.fetchone()
    if not migration_state or str(migration_state.get("last_run_key", "")).strip() != "complete":
        return

    # Restore only rows created by the previous supplier-to-brand migration.
    # Existing manual supplier corrections always take precedence.
    cursor.execute(
        """
        INSERT INTO supplier_product_assignments (
            product_key,
            product,
            supplier,
            updated_by,
            updated_at
        )
        SELECT
            BTRIM(product),
            BTRIM(product),
            BTRIM(brand),
            'supplier-assignment-restore',
            NOW()
        FROM procurement_items
        WHERE updated_by = 'supplier-to-brand-migration'
          AND BTRIM(product) <> ''
          AND BTRIM(brand) <> ''
        ON CONFLICT (product_key) DO NOTHING
        """
    )
    cursor.execute(
        """
        UPDATE procurement_items
        SET
            supplier = brand,
            updated_by = 'supplier-assignment-restore',
            updated_at = NOW()
        WHERE updated_by = 'supplier-to-brand-migration'
          AND BTRIM(supplier) = ''
          AND BTRIM(brand) <> ''
        """
    )
    cursor.execute(
        """
        INSERT INTO service_state (service_name, last_run_key, updated_at)
        VALUES (%s, 'complete', NOW())
        ON CONFLICT (service_name) DO UPDATE SET
            last_run_key = EXCLUDED.last_run_key,
            updated_at = NOW()
        """,
        (SUPPLIER_ASSIGNMENT_RESTORE_SERVICE,),
    )


def ensure_database_ready() -> None:
    global _DB_READY
    if _DB_READY or not database_enabled():
        return

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(DATABASE_SCHEMA_SQL)
            _migrate_users_table(cursor)
            _migrate_audit_logs_table(cursor)
            _migrate_procurement_items_table(cursor)
            _migrate_supplier_assignments_to_brands(cursor)
            _restore_migrated_supplier_assignments(cursor)
    _DB_READY = True


def isoformat_seconds(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if value is None:
        return ""
    return str(value)


def get_service_state(service_name: str) -> str:
    if not database_enabled():
        return ""
    ensure_database_ready()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT last_run_key FROM service_state WHERE service_name = %s",
                (service_name,),
            )
            row = cursor.fetchone()
    return str(row.get("last_run_key", "")).strip() if row else ""


def set_service_state(service_name: str, last_run_key: str) -> None:
    if not database_enabled():
        return
    ensure_database_ready()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO service_state (service_name, last_run_key, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (service_name)
                DO UPDATE SET
                    last_run_key = EXCLUDED.last_run_key,
                    updated_at = NOW()
                """,
                (service_name, last_run_key),
            )


def log_audit_event(
    *,
    user_id: str,
    action: str,
    ip: str = "",
    details: dict[str, Any] | None = None,
    event_timestamp: datetime | None = None,
) -> int | None:
    if not database_enabled():
        return None

    ensure_database_ready()
    normalized_user_id = user_id.strip()
    normalized_action = action.strip()
    if not normalized_user_id:
        raise ValueError("user_id для audit log обязателен.")
    if not normalized_action:
        raise ValueError("action для audit log обязателен.")

    details_payload = details or {}
    timestamp_value = (
        event_timestamp.isoformat(timespec="seconds")
        if isinstance(event_timestamp, datetime)
        else None
    )
    ip_value = ip.strip() or None

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_logs (user_id, action, "timestamp", ip, details)
                VALUES (%s, %s, COALESCE(%s::timestamptz, NOW()), %s, %s::jsonb)
                RETURNING log_id
                """,
                (
                    normalized_user_id,
                    normalized_action,
                    timestamp_value,
                    ip_value,
                    psycopg.types.json.Json(details_payload),
                ),
            )
            row = cursor.fetchone()
    return int(row["log_id"]) if row else None


def list_audit_logs(
    *,
    include_archived: bool = False,
    user_id: str | None = None,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    if not database_enabled():
        return []

    ensure_database_ready()
    clauses: list[str] = []
    params: list[Any] = []

    if not include_archived:
        clauses.append("is_archived = FALSE")
    if user_id and user_id.strip():
        clauses.append("LOWER(user_id) = LOWER(%s)")
        params.append(user_id.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_sql = ""
    if limit is not None and limit > 0:
        limit_sql = "LIMIT %s"
        params.append(int(limit))

    query = f"""
        SELECT
            log_id,
            user_id,
            action,
            "timestamp",
            COALESCE(HOST(ip), '') AS ip,
            details,
            is_archived
        FROM audit_logs
        {where_sql}
        ORDER BY "timestamp" DESC, log_id DESC
        {limit_sql}
    """

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

    return [
        {
            "log_id": int(row.get("log_id", 0)),
            "user_id": str(row.get("user_id", "")).strip(),
            "action": str(row.get("action", "")).strip(),
            "timestamp": isoformat_seconds(row.get("timestamp")),
            "ip": str(row.get("ip", "")).strip(),
            "details": row.get("details") or {},
            "is_archived": bool(row.get("is_archived", False)),
        }
        for row in rows
    ]


def archive_audit_logs(
    *,
    log_ids: list[int] | None = None,
    user_id: str | None = None,
) -> int:
    if not database_enabled():
        return 0

    ensure_database_ready()
    clauses: list[str] = ["is_archived = FALSE"]
    params: list[Any] = []

    if log_ids:
        normalized_ids = [int(log_id) for log_id in log_ids]
        clauses.append("log_id = ANY(%s)")
        params.append(normalized_ids)
    if user_id and user_id.strip():
        clauses.append("LOWER(user_id) = LOWER(%s)")
        params.append(user_id.strip())

    if len(clauses) == 1:
        raise ValueError("Укажите log_ids или user_id для архивации audit log.")

    where_sql = " AND ".join(clauses)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE audit_logs
                SET is_archived = TRUE
                WHERE {where_sql}
                """,
                params,
            )
            row_count = cursor.rowcount
    return int(row_count or 0)
