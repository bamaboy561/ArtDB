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


def ensure_database_ready() -> None:
    global _DB_READY
    if _DB_READY or not database_enabled():
        return

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(DATABASE_SCHEMA_SQL)
            _migrate_users_table(cursor)
            _migrate_audit_logs_table(cursor)
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
