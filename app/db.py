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
DATABASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS salons (
    name TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    salon TEXT NOT NULL DEFAULT '',
    email TEXT,
    phone TEXT,
    salt TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique
    ON users (LOWER(email))
    WHERE email IS NOT NULL AND email <> '';

CREATE UNIQUE INDEX IF NOT EXISTS users_phone_unique
    ON users (phone)
    WHERE phone IS NOT NULL AND phone <> '';

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
"""

_DB_READY = False


def database_enabled() -> bool:
    return bool(DATABASE_URL)


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            "Для PostgreSQL-режима установите зависимость 'psycopg[binary]' из requirements.txt."
        )


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


def ensure_database_ready() -> None:
    global _DB_READY
    if _DB_READY or not database_enabled():
        return

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(DATABASE_SCHEMA_SQL)
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
