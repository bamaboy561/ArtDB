from __future__ import annotations

from dataclasses import dataclass
import os

from db import (
    PGCRYPTO_OPTIONS,
    database_enabled,
    ensure_database_ready,
    get_db_connection,
    get_pgcrypto_key,
)


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str = ""
    chat_id: str = ""
    daily_enabled: bool = False
    report_hour: int = 9
    report_minute: int = 0
    send_report_files: bool = False
    source: str = "environment"

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name, "").strip().casefold()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def load_environment_telegram_settings() -> TelegramSettings:
    return TelegramSettings(
        bot_token=os.getenv("TG_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")).strip(),
        chat_id=os.getenv("TG_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip(),
        daily_enabled=_env_flag("TELEGRAM_DAILY_ENABLED", default=True),
        report_hour=_env_int("TELEGRAM_DAILY_REPORT_HOUR", 9, 0, 23),
        report_minute=_env_int("TELEGRAM_DAILY_REPORT_MINUTE", 0, 0, 59),
        send_report_files=_env_flag("TELEGRAM_SEND_REPORT_FILES", default=False),
        source="environment",
    )


def load_telegram_settings() -> TelegramSettings:
    environment_settings = load_environment_telegram_settings()
    if not database_enabled():
        return environment_settings

    ensure_database_ready()
    pgcrypto_key = get_pgcrypto_key()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COALESCE(pgp_sym_decrypt(bot_token_encrypted, %s), '') AS bot_token,
                    chat_id,
                    daily_enabled,
                    report_hour,
                    report_minute,
                    send_report_files
                FROM telegram_settings
                WHERE settings_id = 1
                """,
                (pgcrypto_key,),
            )
            row = cursor.fetchone()

    if not row:
        return environment_settings

    return TelegramSettings(
        bot_token=str(row.get("bot_token", "")).strip() or environment_settings.bot_token,
        chat_id=str(row.get("chat_id", "")).strip() or environment_settings.chat_id,
        daily_enabled=bool(row.get("daily_enabled", False)),
        report_hour=int(row.get("report_hour", 9)),
        report_minute=int(row.get("report_minute", 0)),
        send_report_files=bool(row.get("send_report_files", False)),
        source="database",
    )


def save_telegram_settings(
    *,
    bot_token: str | None,
    chat_id: str,
    daily_enabled: bool,
    report_hour: int,
    report_minute: int,
    send_report_files: bool,
    updated_by: str,
) -> TelegramSettings:
    if not database_enabled():
        raise RuntimeError("Настройки Telegram из интерфейса доступны только с PostgreSQL.")

    normalized_token = str(bot_token or "").strip()
    normalized_chat_id = str(chat_id or "").strip()
    normalized_hour = max(0, min(23, int(report_hour)))
    normalized_minute = max(0, min(59, int(report_minute)))

    ensure_database_ready()
    pgcrypto_key = get_pgcrypto_key()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO telegram_settings (
                    settings_id,
                    bot_token_encrypted,
                    chat_id,
                    daily_enabled,
                    report_hour,
                    report_minute,
                    send_report_files,
                    updated_by,
                    updated_at
                )
                VALUES (
                    1,
                    CASE
                        WHEN %s::text = '' THEN NULL
                        ELSE pgp_sym_encrypt(%s::text, %s::text, %s::text)
                    END,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW()
                )
                ON CONFLICT (settings_id) DO UPDATE SET
                    bot_token_encrypted = CASE
                        WHEN %s::text = '' THEN telegram_settings.bot_token_encrypted
                        ELSE pgp_sym_encrypt(%s::text, %s::text, %s::text)
                    END,
                    chat_id = EXCLUDED.chat_id,
                    daily_enabled = EXCLUDED.daily_enabled,
                    report_hour = EXCLUDED.report_hour,
                    report_minute = EXCLUDED.report_minute,
                    send_report_files = EXCLUDED.send_report_files,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (
                    normalized_token,
                    normalized_token,
                    pgcrypto_key,
                    PGCRYPTO_OPTIONS,
                    normalized_chat_id,
                    bool(daily_enabled),
                    normalized_hour,
                    normalized_minute,
                    bool(send_report_files),
                    str(updated_by or "").strip(),
                    normalized_token,
                    normalized_token,
                    pgcrypto_key,
                    PGCRYPTO_OPTIONS,
                ),
            )

    settings = load_telegram_settings()
    if not settings.bot_token:
        raise ValueError("Укажите токен Telegram-бота.")
    return settings
