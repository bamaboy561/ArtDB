from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auth_store import bootstrap_first_admin, find_user, has_users, set_user_password
from db import database_enabled, ensure_database_ready


def bootstrap_admin_from_env() -> None:
    username = os.getenv("INITIAL_ADMIN_USERNAME", "").strip()
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "").strip()
    display_name = os.getenv("INITIAL_ADMIN_DISPLAY_NAME", "").strip()
    email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip()
    phone = os.getenv("INITIAL_ADMIN_PHONE", "").strip()

    if has_users():
        print("Users already exist. Admin bootstrap skipped.")
        return

    if not username or not password or not display_name:
        print("Initial admin env vars not fully set. Admin bootstrap skipped.")
        return

    bootstrap_first_admin(
        username=username,
        password=password,
        display_name=display_name,
        email=email,
        phone=phone,
    )
    print(f"Initial admin '{username}' created.")


def _is_truthy_env(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def reset_admin_password_from_env() -> None:
    username = os.getenv("INITIAL_ADMIN_USERNAME", "").strip()
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "").strip()
    reset_requested = _is_truthy_env(os.getenv("RESET_INITIAL_ADMIN_PASSWORD", ""))

    if not reset_requested:
        return

    if not username or not password:
        print("Admin password reset skipped: username or password env var missing.")
        return

    existing_user = find_user(username)
    if not existing_user:
        print(f"Admin password reset skipped: user '{username}' not found.")
        return

    set_user_password(username, password)
    print(f"Password for admin '{username}' was reset from env.")


def main() -> int:
    if not database_enabled():
        print("DATABASE_URL не задан. Инициализация PostgreSQL пропущена.")
        return 0

    ensure_database_ready()
    print("PostgreSQL schema is ready.")
    bootstrap_admin_from_env()
    reset_admin_password_from_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
