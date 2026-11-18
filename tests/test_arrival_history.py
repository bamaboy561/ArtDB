from __future__ import annotations

from datetime import datetime, timezone
import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from procurement_order_store import build_arrival_history  # noqa: E402

ORDERS = pd.DataFrame({"order_id": ["PO-1", "PO-2"], "supplier": ["WQ", "Swiss Krono"]})
ITEMS = pd.DataFrame(
    {
        "order_id": ["PO-1", "PO-2", "PO-2"],
        "product": ["Маффин", "Аделия", "Мист"],
        "quantity": [10.0, 792.0, 1683.0],
        "received_quantity": [4.0, 792.0, 1683.0],
    }
)


def log(action: str, when: datetime, details: dict) -> dict:
    return {"action": action, "timestamp": when, "user_id": "admin", "details": details}


class ArrivalHistoryTest(unittest.TestCase):
    def test_stock_uploads_and_manual_receipts_are_listed_newest_first(self) -> None:
        logs = [
            log("procurement.order_update", datetime(2026, 11, 18, 9, tzinfo=timezone.utc), {"order_id": "PO-1"}),
            log(
                "procurement.stock_upload",
                datetime(2026, 11, 17, 8, tzinfo=timezone.utc),
                {"filename": "остатки 17.11.xlsx", "order_arrivals": [{"order_id": "PO-1", "product": "Маффин", "arrived_qty": 4}]},
            ),
            log("procurement.order_receive", datetime(2026, 11, 18, 10, tzinfo=timezone.utc), {"order_id": "PO-2"}),
            # An upload without arrivals adds nothing.
            log("procurement.stock_upload", datetime(2026, 11, 16, 8, tzinfo=timezone.utc), {"filename": "x.xlsx", "order_arrivals": []}),
        ]
        history = build_arrival_history(logs, ORDERS, ITEMS)
        self.assertEqual(
            history[["source", "order_id", "supplier", "product", "quantity"]].values.tolist(),
            [
                ["Заказ принят целиком", "PO-2", "Swiss Krono", "Аделия", 792.0],
                ["Заказ принят целиком", "PO-2", "Swiss Krono", "Мист", 1683.0],
                ["Остатки: остатки 17.11.xlsx", "PO-1", "WQ", "Маффин", 4.0],
            ],
        )

    def test_a_missing_quantity_is_zero(self) -> None:
        logs = [log("procurement.stock_upload", datetime(2026, 11, 17, tzinfo=timezone.utc), {"order_arrivals": [{"order_id": "PO-9", "product": "Х", "arrived_qty": None}]})]
        history = build_arrival_history(logs, ORDERS, ITEMS)
        self.assertEqual((history.iloc[0]["quantity"], history.iloc[0]["supplier"], history.iloc[0]["source"]), (0.0, "", "Остатки: файл"))

    def test_no_journal_gives_an_empty_history(self) -> None:
        self.assertTrue(build_arrival_history([], ORDERS, ITEMS).empty)


if __name__ == "__main__":
    unittest.main()
