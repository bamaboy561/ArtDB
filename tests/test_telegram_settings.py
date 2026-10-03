from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from telegram_reports import discover_telegram_chats
from telegram_settings_store import load_environment_telegram_settings


class TelegramEnvironmentSettingsTests(unittest.TestCase):
    def test_environment_settings_are_normalized(self) -> None:
        environment = {
            "TG_BOT_TOKEN": " token ",
            "TG_CHAT_ID": " -100123 ",
            "TELEGRAM_DAILY_ENABLED": "true",
            "TELEGRAM_DAILY_REPORT_HOUR": "25",
            "TELEGRAM_DAILY_REPORT_MINUTE": "-4",
            "TELEGRAM_SEND_REPORT_FILES": "yes",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_environment_telegram_settings()

        self.assertEqual(settings.bot_token, "token")
        self.assertEqual(settings.chat_id, "-100123")
        self.assertTrue(settings.configured)
        self.assertTrue(settings.daily_enabled)
        self.assertEqual(settings.report_hour, 23)
        self.assertEqual(settings.report_minute, 0)
        self.assertTrue(settings.send_report_files)


class TelegramChatDiscoveryTests(unittest.TestCase):
    def test_discovery_returns_unique_recent_chats(self) -> None:
        response = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {
                            "id": 123,
                            "type": "private",
                            "first_name": "Sultan",
                            "username": "sultan",
                        }
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "chat": {
                            "id": 123,
                            "type": "private",
                            "first_name": "Sultan",
                        }
                    },
                },
                {
                    "update_id": 3,
                    "message": {
                        "chat": {
                            "id": -100555,
                            "type": "supergroup",
                            "title": "ArtDB reports",
                        }
                    },
                },
            ],
        }

        with patch("telegram_reports._telegram_api_request", return_value=response):
            chats = discover_telegram_chats()

        self.assertEqual({item["chat_id"] for item in chats}, {"123", "-100555"})
        labels = {item["chat_id"]: item["label"] for item in chats}
        self.assertEqual(labels["123"], "Sultan")
        self.assertEqual(labels["-100555"], "ArtDB reports")


if __name__ == "__main__":
    unittest.main()
