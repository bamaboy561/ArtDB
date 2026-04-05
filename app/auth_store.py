from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any

from db import database_enabled, ensure_database_ready, get_db_connection, get_pgcrypto_key, isoformat_seconds


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR.parent / "data"))).resolve()
USERS_PATH = DATA_DIR / "users.json"
SESSIONS_PATH = DATA_DIR / "auth_sessions.json"
PBKDF2_ITERATIONS = 120_000
SUPPORTED_ROLES = {"admin", "manager", "salon"}
ROLE_SORT_ORDER = {"admin": 0, "manager": 1, "salon": 2}
SESSION_TTL_DAYS = 30


def ensure_user_store() -> None:
    if database_enabled():
        ensure_database_ready()
        return
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_PATH.exists():
        USERS_PATH.write_text("[]", encoding="utf-8")
    if not SESSIONS_PATH.exists():
        SESSIONS_PATH.write_text("[]", encoding="utf-8")


def _load_raw_users() -> list[dict[str, Any]]:
    ensure_user_store()
    if database_enabled():
        pgcrypto_key = get_pgcrypto_key()
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        username,
                        display_name,
                        role,
                        COALESCE(salon, '') AS salon,
                        COALESCE(pgp_sym_decrypt(email_encrypted, %s), '') AS email,
                        COALESCE(pgp_sym_decrypt(phone_encrypted, %s), '') AS phone,
                        salt,
                        iterations,
                        password_hash,
                        created_at,
                        is_active
                    FROM users
                    ORDER BY
                        CASE role
                            WHEN 'admin' THEN 0
                            WHEN 'manager' THEN 1
                            WHEN 'salon' THEN 2
                            ELSE 99
                        END,
                        LOWER(username)
                    """,
                    (pgcrypto_key, pgcrypto_key),
                )
                rows = cursor.fetchall()
        return [
            _normalize_record(
                {
                    **row,
                    "created_at": isoformat_seconds(row.get("created_at")),
                }
            )
            for row in rows
        ]
    try:
        users = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        users = []
    raw_users = users if isinstance(users, list) else []
    normalized_users = [_normalize_record(record) for record in raw_users]
    if normalized_users != raw_users:
        _save_raw_users(normalized_users)
    return normalized_users


def _save_raw_users(users: list[dict[str, Any]]) -> None:
    ensure_user_store()
    if database_enabled():
        pgcrypto_key = get_pgcrypto_key()
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                usernames = [str(record.get("username", "")).strip() for record in users if str(record.get("username", "")).strip()]
                for record in users:
                    normalized_email = normalize_email(str(record.get("email", ""))) or None
                    normalized_phone = normalize_phone(str(record.get("phone", ""))) or None
                    cursor.execute(
                        """
                        INSERT INTO users (
                            username,
                            display_name,
                            role,
                            salon,
                            email_encrypted,
                            phone_encrypted,
                            email_hash,
                            phone_hash,
                            salt,
                            iterations,
                            password_hash,
                            created_at,
                            is_active
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            CASE
                                WHEN %s IS NULL THEN NULL
                                ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256,compress-algo=1')
                            END,
                            CASE
                                WHEN %s IS NULL THEN NULL
                                ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256,compress-algo=1')
                            END,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
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
                            _hash_identifier(normalized_email),
                            _hash_identifier(normalized_phone),
                            str(record.get("salt", "")),
                            int(record.get("iterations", PBKDF2_ITERATIONS)),
                            str(record.get("password_hash", "")),
                            str(record.get("created_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
                            bool(record.get("is_active", True)),
                        ),
                    )
                if usernames:
                    cursor.execute(
                        "DELETE FROM users WHERE username <> ALL(%s)",
                        (usernames,),
                    )
                else:
                    cursor.execute("DELETE FROM users")
        return
    USERS_PATH.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_raw_sessions() -> list[dict[str, Any]]:
    ensure_user_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT token, username, created_at, expires_at
                    FROM auth_sessions
                    ORDER BY created_at
                    """
                )
                rows = cursor.fetchall()
        return [
            {
                "token": str(row.get("token", "")).strip(),
                "username": str(row.get("username", "")).strip(),
                "created_at": isoformat_seconds(row.get("created_at")),
                "expires_at": isoformat_seconds(row.get("expires_at")),
            }
            for row in rows
        ]
    try:
        sessions = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sessions = []
    return sessions if isinstance(sessions, list) else []


def _save_raw_sessions(sessions: list[dict[str, Any]]) -> None:
    ensure_user_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM auth_sessions")
                for record in sessions:
                    cursor.execute(
                        """
                        INSERT INTO auth_sessions (token, username, created_at, expires_at)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            str(record.get("token", "")).strip(),
                            str(record.get("username", "")).strip(),
                            str(record.get("created_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
                            str(record.get("expires_at", "")).strip(),
                        ),
                    )
        return
    SESSIONS_PATH.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_sessions(sessions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    current_sessions = list(sessions) if sessions is not None else _load_raw_sessions()
    now = datetime.now()
    cleaned: list[dict[str, Any]] = []
    changed = False

    for record in current_sessions:
        token = str(record.get("token", "")).strip()
        username = str(record.get("username", "")).strip()
        expires_at_raw = str(record.get("expires_at", "")).strip()
        if not token or not username or not expires_at_raw:
            changed = True
            continue

        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            changed = True
            continue

        if expires_at <= now:
            changed = True
            continue

        cleaned.append(
            {
                "token": token,
                "username": username,
                "created_at": str(record.get("created_at", "")).strip(),
                "expires_at": expires_at.isoformat(timespec="seconds"),
            }
        )

    if changed or sessions is None:
        _save_raw_sessions(cleaned)

    return cleaned


def _build_hash(password: str, salt: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return digest.hex()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return digits


def _hash_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized["username"] = str(record.get("username", "")).strip()
    normalized["display_name"] = str(record.get("display_name", normalized["username"])).strip()
    normalized["role"] = str(record.get("role", "")).strip().lower()
    normalized["salon"] = str(record.get("salon", "")).strip()
    normalized["email"] = normalize_email(str(record.get("email", "")))
    normalized["phone"] = normalize_phone(str(record.get("phone", "")))
    normalized["is_active"] = bool(record.get("is_active", True))
    return normalized


def _public_user(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": record["username"],
        "display_name": record.get("display_name") or record["username"],
        "role": record["role"],
        "salon": record.get("salon") or "",
        "email": record.get("email") or "",
        "phone": record.get("phone") or "",
        "is_active": bool(record.get("is_active", True)),
        "created_at": record.get("created_at", ""),
    }


def create_auth_session(username: str, *, ttl_days: int = SESSION_TTL_DAYS) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires_at = now + timedelta(days=ttl_days)
    sessions = _cleanup_sessions()
    sessions = [
        record
        for record in sessions
        if str(record.get("username", "")).strip().casefold() != username.strip().casefold()
    ]
    sessions.append(
        {
            "token": token,
            "username": username.strip(),
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": expires_at.isoformat(timespec="seconds"),
        }
    )
    _save_raw_sessions(sessions)
    return token


def revoke_auth_session(token: str) -> None:
    token = token.strip()
    if not token:
        return
    sessions = _cleanup_sessions()
    filtered = [record for record in sessions if str(record.get("token", "")).strip() != token]
    if len(filtered) != len(sessions):
        _save_raw_sessions(filtered)


def revoke_user_sessions(username: str) -> None:
    username_key = username.strip().casefold()
    if not username_key:
        return
    sessions = _cleanup_sessions()
    filtered = [
        record
        for record in sessions
        if str(record.get("username", "")).strip().casefold() != username_key
    ]
    if len(filtered) != len(sessions):
        _save_raw_sessions(filtered)


def authenticate_session(token: str) -> dict[str, Any] | None:
    token = token.strip()
    if not token:
        return None

    sessions = _cleanup_sessions()
    for record in sessions:
        if str(record.get("token", "")).strip() != token:
            continue

        user = find_user(str(record.get("username", "")))
        if not user or not bool(user.get("is_active", True)):
            revoke_auth_session(token)
            return None
        return _public_user(user)

    return None


def list_users() -> list[dict[str, Any]]:
    return [_public_user(record) for record in _load_raw_users()]


def has_users() -> bool:
    return len(_load_raw_users()) > 0


def has_admin_users() -> bool:
    return any(str(record.get("role", "")).strip().lower() == "admin" for record in _load_raw_users())


def promote_first_manager_to_admin() -> dict[str, Any] | None:
    if has_admin_users():
        return None

    users = _load_raw_users()
    manager_candidates = [
        record for record in users if str(record.get("role", "")).strip().lower() == "manager"
    ]
    if not manager_candidates:
        return None

    manager_candidates.sort(
        key=lambda item: (
            str(item.get("created_at", "")),
            str(item.get("username", "")).casefold(),
        )
    )
    target_username = str(manager_candidates[0].get("username", "")).strip().casefold()
    promoted_record: dict[str, Any] | None = None

    for record in users:
        if str(record.get("username", "")).strip().casefold() == target_username:
            record["role"] = "admin"
            record["salon"] = ""
            promoted_record = record
            break

    if promoted_record is None:
        return None

    users.sort(
        key=lambda item: (
            ROLE_SORT_ORDER.get(str(item.get("role", "")).strip().lower(), 99),
            str(item.get("username", "")).casefold(),
        )
    )
    _save_raw_users(users)
    return _public_user(promoted_record)


def find_user(identifier: str) -> dict[str, Any] | None:
    normalized_identifier = identifier.strip().casefold()
    normalized_phone = normalize_phone(identifier)

    for record in _load_raw_users():
        username = str(record.get("username", "")).casefold()
        email = normalize_email(str(record.get("email", "")))
        phone = normalize_phone(str(record.get("phone", "")))

        if username == normalized_identifier:
            return record
        if email and email == normalized_identifier:
            return record
        if phone and normalized_phone and phone == normalized_phone:
            return record
    return None


def authenticate_user(identifier: str, password: str) -> dict[str, Any] | None:
    record = find_user(identifier)
    if not record or not bool(record.get("is_active", True)):
        return None

    salt = str(record.get("salt", ""))
    stored_hash = str(record.get("password_hash", ""))
    iterations = int(record.get("iterations", PBKDF2_ITERATIONS))
    candidate_hash = _build_hash(password, salt, iterations)

    if not hmac.compare_digest(candidate_hash, stored_hash):
        return None

    return _public_user(record)


def create_user(
    *,
    username: str,
    password: str,
    role: str,
    display_name: str,
    email: str = "",
    phone: str = "",
    salon: str = "",
) -> dict[str, Any]:
    username = username.strip()
    display_name = display_name.strip()
    salon = salon.strip()
    role = role.strip().lower()
    email = normalize_email(email)
    phone = normalize_phone(phone)

    if not username:
        raise ValueError("Укажите логин пользователя.")
    if not display_name:
        raise ValueError("Укажите отображаемое имя.")
    if not password or len(password) < 6:
        raise ValueError("Пароль должен содержать минимум 6 символов.")
    if role not in SUPPORTED_ROLES:
        raise ValueError("Неизвестная роль пользователя.")
    if role == "salon" and not salon:
        raise ValueError("Для пользователя салона нужно указать салон.")
    if not email and not phone:
        raise ValueError("Укажите хотя бы email или номер телефона.")
    if find_user(username):
        raise ValueError("Пользователь с таким логином уже существует.")
    if email and find_user(email):
        raise ValueError("Пользователь с таким email уже существует.")
    if phone and find_user(phone):
        raise ValueError("Пользователь с таким телефоном уже существует.")

    users = _load_raw_users()
    salt = secrets.token_hex(16)
    record = {
        "username": username,
        "display_name": display_name,
        "role": role,
        "salon": salon if role == "salon" else "",
        "email": email,
        "phone": phone,
        "salt": salt,
        "iterations": PBKDF2_ITERATIONS,
        "password_hash": _build_hash(password, salt, PBKDF2_ITERATIONS),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "is_active": True,
    }
    users.append(record)
    users.sort(
        key=lambda item: (
            ROLE_SORT_ORDER.get(str(item.get("role", "")).strip().lower(), 99),
            str(item.get("username", "")).casefold(),
        )
    )
    _save_raw_users(users)
    return _public_user(record)


def set_user_password(username: str, new_password: str) -> None:
    if not new_password or len(new_password) < 6:
        raise ValueError("Новый пароль должен содержать минимум 6 символов.")

    users = _load_raw_users()
    updated = False
    for record in users:
        if str(record.get("username", "")).casefold() == username.strip().casefold():
            salt = secrets.token_hex(16)
            record["salt"] = salt
            record["iterations"] = PBKDF2_ITERATIONS
            record["password_hash"] = _build_hash(new_password, salt, PBKDF2_ITERATIONS)
            updated = True
            break

    if not updated:
        raise ValueError("Пользователь не найден.")

    _save_raw_users(users)


def delete_user(username: str, *, actor_username: str | None = None) -> dict[str, Any]:
    username_key = username.strip().casefold()
    actor_key = actor_username.strip().casefold() if actor_username else ""
    if not username_key:
        raise ValueError("Укажите пользователя для удаления.")
    if actor_key and actor_key == username_key:
        raise ValueError("Нельзя удалить текущую учетную запись, под которой вы вошли.")

    users = _load_raw_users()
    target_user = next((record for record in users if str(record.get("username", "")).strip().casefold() == username_key), None)
    if target_user is None:
        raise ValueError("Пользователь не найден.")

    if str(target_user.get("role", "")).strip().lower() == "admin":
        admin_count = sum(1 for record in users if str(record.get("role", "")).strip().lower() == "admin")
        if admin_count <= 1:
            raise ValueError("Нельзя удалить последнего администратора в системе.")

    remaining_users = [
        record for record in users if str(record.get("username", "")).strip().casefold() != username_key
    ]
    _save_raw_users(remaining_users)
    revoke_user_sessions(str(target_user.get("username", "")))
    return _public_user(target_user)


def delete_users_by_salon(salon_name: str) -> int:
    salon_key = salon_name.strip()
    if not salon_key:
        return 0

    users = _load_raw_users()
    to_delete = [
        record
        for record in users
        if str(record.get("salon", "")).strip().casefold() == salon_key.casefold()
    ]
    if not to_delete:
        return 0

    remaining_users = [
        record
        for record in users
        if str(record.get("salon", "")).strip().casefold() != salon_key.casefold()
    ]
    _save_raw_users(remaining_users)
    for record in to_delete:
        revoke_user_sessions(str(record.get("username", "")))
    return len(to_delete)


def bootstrap_first_admin(
    username: str,
    password: str,
    display_name: str,
    *,
    email: str = "",
    phone: str = "",
) -> dict[str, Any]:
    if has_users():
        raise ValueError("Пользователи уже существуют.")
    return create_user(
        username=username,
        password=password,
        role="admin",
        display_name=display_name,
        email=email,
        phone=phone,
    )


def bootstrap_first_manager(
    username: str,
    password: str,
    display_name: str,
    *,
    email: str = "",
    phone: str = "",
) -> dict[str, Any]:
    return bootstrap_first_admin(
        username=username,
        password=password,
        display_name=display_name,
        email=email,
        phone=phone,
    )
