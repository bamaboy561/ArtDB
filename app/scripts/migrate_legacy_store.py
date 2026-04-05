from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auth_store import normalize_email, normalize_phone
from db import database_enabled, ensure_database_ready, get_db_connection, get_pgcrypto_key


def read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def hash_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy JSON/CSV storage into PostgreSQL.")
    parser.add_argument("--truncate", action="store_true", help="Clear PostgreSQL tables before import.")
    args = parser.parse_args()

    if not database_enabled():
        raise SystemExit("DATABASE_URL не задан. Миграция в PostgreSQL невозможна.")

    ensure_database_ready()

    legacy_dir = Path(os.getenv("LEGACY_DATA_DIR", os.getenv("APP_DATA_DIR", "data"))).resolve()
    users_path = legacy_dir / "users.json"
    sessions_path = legacy_dir / "auth_sessions.json"
    salons_path = legacy_dir / "salons.json"
    manifest_path = legacy_dir / "upload_manifest.csv"

    users = read_json_list(users_path)
    sessions = read_json_list(sessions_path)
    salons_payload = read_json_list(salons_path)
    manifest = pd.read_csv(manifest_path, encoding="utf-8-sig") if manifest_path.exists() else pd.DataFrame()

    salon_names = {
        str(item).strip()
        for item in salons_payload
        if str(item).strip()
    }
    if not manifest.empty and "salon" in manifest.columns:
        salon_names.update(str(item).strip() for item in manifest["salon"].dropna().astype(str) if str(item).strip())

    pgcrypto_key = get_pgcrypto_key()

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if args.truncate:
                cursor.execute("TRUNCATE TABLE auth_sessions, uploads, users, salons RESTART IDENTITY CASCADE")

            for salon_name in sorted(salon_names):
                cursor.execute(
                    "INSERT INTO salons (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (salon_name,),
                )

            for record in users:
                normalized_email = normalize_email(str(record.get("email", ""))) or None
                normalized_phone = normalize_phone(str(record.get("phone", ""))) or None
                cursor.execute(
                    """
                    INSERT INTO users (
                        username, display_name, role, salon, email_encrypted, phone_encrypted, email_hash, phone_hash, salt,
                        iterations, password_hash, created_at, is_active
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        CASE WHEN %s IS NULL THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256,compress-algo=1') END,
                        CASE WHEN %s IS NULL THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256,compress-algo=1') END,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (username) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        role = EXCLUDED.role,
                        salon = EXCLUDED.salon,
                        email_encrypted = EXCLUDED.email_encrypted,
                        phone_encrypted = EXCLUDED.phone_encrypted,
                        email_hash = EXCLUDED.email_hash,
                        phone_hash = EXCLUDED.phone_hash,
                        salt = EXCLUDED.salt,
                        iterations = EXCLUDED.iterations,
                        password_hash = EXCLUDED.password_hash,
                        created_at = EXCLUDED.created_at,
                        is_active = EXCLUDED.is_active
                    """,
                    (
                        str(record.get("username", "")).strip(),
                        str(record.get("display_name", "")).strip(),
                        str(record.get("role", "")).strip().lower(),
                        str(record.get("salon", "")).strip(),
                        normalized_email,
                        normalized_email,
                        pgcrypto_key,
                        normalized_phone,
                        normalized_phone,
                        pgcrypto_key,
                        hash_identifier(normalized_email),
                        hash_identifier(normalized_phone),
                        str(record.get("salt", "")).strip(),
                        int(record.get("iterations", 120000)),
                        str(record.get("password_hash", "")).strip(),
                        str(record.get("created_at", "")).strip() or None,
                        bool(record.get("is_active", True)),
                    ),
                )

            for record in sessions:
                cursor.execute(
                    """
                    INSERT INTO auth_sessions (token, username, created_at, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (token) DO UPDATE SET
                        username = EXCLUDED.username,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        str(record.get("token", "")).strip(),
                        str(record.get("username", "")).strip(),
                        str(record.get("created_at", "")).strip() or None,
                        str(record.get("expires_at", "")).strip() or None,
                    ),
                )

            if not manifest.empty:
                for row in manifest.to_dict(orient="records"):
                    mapping_json = row.get("mapping_json", "{}")
                    if isinstance(mapping_json, str):
                        try:
                            mapping_payload = json.loads(mapping_json) if mapping_json else {}
                        except json.JSONDecodeError:
                            mapping_payload = {}
                    else:
                        mapping_payload = mapping_json or {}
                    cursor.execute(
                        """
                        INSERT INTO uploads (
                            upload_id, salon, report_date, source_filename, stored_path,
                            uploaded_at, csv_separator, csv_encoding, sheet_name, mapping_json
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (upload_id) DO UPDATE SET
                            salon = EXCLUDED.salon,
                            report_date = EXCLUDED.report_date,
                            source_filename = EXCLUDED.source_filename,
                            stored_path = EXCLUDED.stored_path,
                            uploaded_at = EXCLUDED.uploaded_at,
                            csv_separator = EXCLUDED.csv_separator,
                            csv_encoding = EXCLUDED.csv_encoding,
                            sheet_name = EXCLUDED.sheet_name,
                            mapping_json = EXCLUDED.mapping_json
                        """,
                        (
                            str(row.get("upload_id", "")).strip(),
                            str(row.get("salon", "")).strip(),
                            str(row.get("report_date", "")).strip(),
                            str(row.get("source_filename", "")).strip(),
                            str(row.get("stored_path", "")).strip(),
                            str(row.get("uploaded_at", "")).strip() or None,
                            str(row.get("csv_separator", "") or ";"),
                            str(row.get("csv_encoding", "") or "utf-8"),
                            str(row.get("sheet_name", "") or ""),
                            json.dumps(mapping_payload, ensure_ascii=False),
                        ),
                    )

    print(f"Imported salons: {len(salon_names)}")
    print(f"Imported users: {len(users)}")
    print(f"Imported sessions: {len(sessions)}")
    print(f"Imported uploads: {0 if manifest.empty else len(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
