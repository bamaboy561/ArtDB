from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from telegram_bot import (
    TelegramReportMenu,
    _enrich_sales_catalog,
    parse_date_range,
    resolve_report_period,
)


def message_update(text: str, *, chat_id: int = 123, sender_id: int = 77) -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": sender_id},
            "text": text,
        },
    }


def callback_update(data: str, *, chat_id: int = 123, sender_id: int = 77) -> dict[str, object]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": sender_id},
            "data": data,
            "message": {"chat": {"id": chat_id, "type": "private"}},
        },
    }


class TelegramBotDateTests(unittest.TestCase):
    def test_parse_date_range_accepts_human_and_iso_dates(self) -> None:
        self.assertEqual(
            parse_date_range("01.09.2026 30.09.2026"),
            (date(2026, 9, 1), date(2026, 9, 30)),
        )
        self.assertEqual(
            parse_date_range("2026-09-01 - 2026-09-30"),
            (date(2026, 9, 1), date(2026, 9, 30)),
        )

    def test_quick_periods_follow_latest_available_sale_date(self) -> None:
        data = pd.DataFrame(
            {"date": [pd.Timestamp("2026-08-01"), pd.Timestamp("2026-09-20")]}
        )

        self.assertEqual(
            resolve_report_period(data, "30"),
            (date(2026, 8, 22), date(2026, 9, 20)),
        )
        self.assertEqual(
            resolve_report_period(data, "month"),
            (date(2026, 9, 1), date(2026, 9, 20)),
        )


class TelegramBotMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sales = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-09-10"),
                    "item_code": "A-1",
                    "product_key": "A-1",
                    "product": "Плита Дуб",
                    "category": "ЛДСП",
                    "brand": "Egger",
                    "supplier": "Slotex",
                    "quantity": 2.0,
                    "revenue": 300.0,
                    "cost": 180.0,
                    "margin": 120.0,
                }
            ]
        )

    def test_menu_command_sends_persistent_keyboard(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with patch("telegram_bot.send_telegram_message") as send_message:
            menu.handle_update(message_update("/menu"), allowed_chat_id="123")

        reply_markup = send_message.call_args.kwargs["reply_markup"]
        self.assertTrue(reply_markup["is_persistent"])
        button_labels = [button["text"] for row in reply_markup["keyboard"] for button in row]
        self.assertIn("Сводка", button_labels)
        self.assertIn("Карточка SKU", button_labels)
        self.assertIn("Бренды", button_labels)
        self.assertIn("Поставщики", button_labels)

    def test_unauthorized_chat_is_ignored(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with patch("telegram_bot.send_telegram_message") as send_message:
            menu.handle_update(message_update("/summary", chat_id=999), allowed_chat_id="123")

        send_message.assert_not_called()

    def test_unauthorized_chat_can_request_its_chat_id(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with patch("telegram_bot.send_telegram_message") as send_message:
            menu.handle_update(message_update("/chatid", chat_id=999), allowed_chat_id="123")

        self.assertIn("999", send_message.call_args.args[0])
        self.assertEqual(send_message.call_args.kwargs["chat_id"], "999")

    def test_second_authorized_chat_can_open_menu(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with patch("telegram_bot.send_telegram_message") as send_message:
            menu.handle_update(
                message_update("/menu", chat_id=-100555),
                allowed_chat_ids=("123", "-100555"),
            )

        self.assertEqual(send_message.call_args.kwargs["chat_id"], "-100555")

    def test_sku_command_runs_exact_report_for_requested_period(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with (
            patch("telegram_bot.send_telegram_message"),
            patch("telegram_bot.send_targeted_telegram_report", return_value=1) as send_report,
            patch("telegram_bot.log_audit_event"),
        ):
            menu.handle_update(
                message_update("/sku A-1 01.09.2026 30.09.2026"),
                allowed_chat_id="123",
            )

        self.assertEqual(send_report.call_args.kwargs["report_kind"], "sku")
        self.assertEqual(send_report.call_args.kwargs["product_key"], "A-1")
        self.assertEqual(send_report.call_args.kwargs["date_from"], date(2026, 9, 1))
        self.assertEqual(send_report.call_args.kwargs["date_to"], date(2026, 9, 30))
        self.assertEqual(send_report.call_args.kwargs["chat_id"], "123")

    def test_supplier_command_runs_report_for_selected_supplier(self) -> None:
        menu = TelegramReportMenu(data_loader=lambda: self.sales)
        with (
            patch("telegram_bot.send_telegram_message"),
            patch("telegram_bot.send_targeted_telegram_report", return_value=1) as send_report,
            patch("telegram_bot.log_audit_event"),
        ):
            menu.handle_update(
                message_update("/supplier Slotex 01.09.2026 30.09.2026"),
                allowed_chat_id="123",
            )

        self.assertEqual(send_report.call_args.kwargs["report_kind"], "supplier")
        self.assertEqual(send_report.call_args.kwargs["supplier"], "Slotex")
        self.assertIsNone(send_report.call_args.kwargs["brand"])

    def test_manual_supplier_assignment_overrides_imported_supplier(self) -> None:
        catalog = pd.DataFrame(
            [{"product": "Плита Дуб", "supplier": "Old", "brand": "Egger"}]
        )
        assignments = pd.DataFrame(
            [{"product_key": "A-1", "product": "Плита Дуб", "supplier": "Slotex"}]
        )

        enriched = _enrich_sales_catalog(
            self.sales,
            procurement_items=catalog,
            supplier_product_assignments=assignments,
        )

        self.assertEqual(enriched.iloc[0]["supplier"], "Slotex")
        self.assertEqual(enriched.iloc[0]["brand"], "Egger")

    def test_selected_sku_keeps_dates_from_command(self) -> None:
        sales = pd.concat(
            [
                self.sales,
                pd.DataFrame(
                    [
                        {
                            "date": pd.Timestamp("2026-09-11"),
                            "item_code": "A-2",
                            "product_key": "A-2",
                            "product": "Плита Орех",
                            "category": "ЛДСП",
                            "quantity": 1.0,
                            "revenue": 200.0,
                            "cost": 120.0,
                            "margin": 80.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        menu = TelegramReportMenu(data_loader=lambda: sales)
        with (
            patch("telegram_bot.send_telegram_message"),
            patch("telegram_bot.answer_telegram_callback"),
            patch("telegram_bot.send_targeted_telegram_report", return_value=1) as send_report,
            patch("telegram_bot.log_audit_event"),
        ):
            menu.handle_update(
                message_update("/sku Плита 01.09.2026 30.09.2026"),
                allowed_chat_id="123",
            )
            menu.handle_update(callback_update("sku:0"), allowed_chat_id="123")

        self.assertEqual(send_report.call_count, 1)
        self.assertEqual(send_report.call_args.kwargs["date_from"], date(2026, 9, 1))
        self.assertEqual(send_report.call_args.kwargs["date_to"], date(2026, 9, 30))


if __name__ == "__main__":
    unittest.main()
