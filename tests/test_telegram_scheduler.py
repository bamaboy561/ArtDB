from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import telegram_scheduler  # noqa: E402
from telegram_settings_store import TelegramSettings  # noqa: E402


class FakeServiceState:
    """In-memory stand-in for the service_state table with the same claim semantics."""

    def __init__(self) -> None:
        self.value = ""

    def get(self, _service_name: str) -> str:
        return self.value

    def claim(self, _service_name: str, run_key: str) -> bool:
        if self.value == run_key:
            return False
        self.value = run_key
        return True


class DailyReportSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TelegramSettings(
            bot_token="token",
            chat_id="1",
            daily_enabled=True,
            report_hour=9,
            report_minute=0,
            send_report_files=True,
        )
        self.state = FakeServiceState()
        patches = [
            mock.patch.object(telegram_scheduler, "get_service_state", side_effect=self.state.get),
            mock.patch.object(telegram_scheduler, "claim_service_run", side_effect=self.state.claim),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        sender = mock.patch.object(telegram_scheduler, "send_telegram_report_pack")
        self.send = sender.start()
        self.addCleanup(sender.stop)

    def run_at(self, hour: int, minute: int = 0) -> bool:
        return telegram_scheduler.run_scheduled_report_if_due(datetime(2026, 10, 15, hour, minute), self.settings)

    def test_sends_once_per_day_however_often_it_is_checked(self) -> None:
        self.assertFalse(self.run_at(8, 59))
        self.assertTrue(self.run_at(9, 0))
        for minute in range(1, 30):
            self.assertFalse(self.run_at(9, minute))
        self.assertEqual(self.send.call_count, 1)

    def test_failed_upload_does_not_resend_the_report(self) -> None:
        self.send.side_effect = RuntimeError("timed out")
        with self.assertRaises(RuntimeError):
            self.run_at(9, 0)
        self.send.side_effect = None
        self.assertFalse(self.run_at(9, 2))
        self.assertEqual(self.send.call_count, 1)

    def test_second_instance_that_loses_the_claim_stays_silent(self) -> None:
        self.state.value = "2026-10-14"
        with mock.patch.object(telegram_scheduler, "claim_service_run", return_value=False):
            self.assertFalse(self.run_at(9, 0))
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
