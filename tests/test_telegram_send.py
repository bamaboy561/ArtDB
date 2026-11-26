from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import telegram_reports  # noqa: E402


class SendToEveryChatTest(unittest.TestCase):
    """A chat that fails, for example one the bot was removed from, keeps no one else from the report."""

    def setUp(self) -> None:
        self.sent: list[str] = []

        def request(method, payload, **kwargs):
            chat = next(part.split("=", 1)[1] for part in payload.decode("utf-8").split("&") if part.startswith("chat_id="))
            if chat == "111":
                raise RuntimeError("Forbidden: bot was kicked from the group chat")
            self.sent.append(chat)
            return {"ok": True}

        for item in (
            patch.object(telegram_reports, "_get_telegram_credentials", lambda: ("token", "111,222,333")),
            patch.object(telegram_reports, "_telegram_api_request", request),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_the_other_chats_still_get_the_message(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            telegram_reports.send_telegram_message("Сводка")
        self.assertEqual(self.sent, ["222", "333"])
        self.assertIn("111", str(raised.exception))

    def test_the_files_go_out_even_when_the_summary_fails(self) -> None:
        files = [telegram_reports.TelegramReportFile(filename="отчёт.xlsx", content=b"x", caption="Отчёт")]
        with (
            patch.object(telegram_reports, "build_daily_telegram_card", lambda: None),
            patch.object(telegram_reports, "send_telegram_message", side_effect=RuntimeError("summary failed")),
            patch.object(telegram_reports, "build_telegram_report_files", lambda: files),
            patch.object(telegram_reports, "send_telegram_document") as send_document,
        ):
            with self.assertRaises(RuntimeError):
                telegram_reports.send_telegram_report_pack(caption="Сводка")
        send_document.assert_called_once()


if __name__ == "__main__":
    unittest.main()
