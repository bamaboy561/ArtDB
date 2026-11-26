from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import procurement_store  # noqa: E402


class ReceiptTest(unittest.TestCase):
    """Goods received by hand land on the catalogue item and change nothing else."""

    def setUp(self) -> None:
        folder = Path(tempfile.mkdtemp())
        for item in (
            patch.object(procurement_store, "database_enabled", lambda: False),
            patch.object(procurement_store, "DATA_DIR", folder),
            patch.object(procurement_store, "PROCUREMENT_ITEMS_PATH", folder / "procurement_items.json"),
            patch.object(procurement_store, "INVENTORY_SNAPSHOTS_PATH", folder / "inventory_snapshots.json"),
        ):
            item.start()
            self.addCleanup(item.stop)
        procurement_store.upsert_procurement_items(
            pd.DataFrame(
                [
                    {"product": "Петля Blum 71B3550", "supplier": "Blum", "stock_on_hand": 10, "min_order_qty": 50, "lead_time_days": 30},
                    {"product": "Ручка скоба 128", "supplier": "Samet", "stock_on_hand": 5},
                ]
            ),
            updated_by="склад",
        )
        self.before = procurement_store.load_procurement_items().set_index("product")

    def test_a_name_spelled_a_little_differently_keeps_the_item_settings(self) -> None:
        procurement_store.apply_procurement_receipts(
            pd.DataFrame([{"product": "петля blum 71b3550", "quantity": 20}]), updated_by="приёмка"
        )
        items = procurement_store.load_procurement_items().set_index("product")
        self.assertEqual(len(items), 2)
        hinge = items.loc["Петля Blum 71B3550"]
        self.assertEqual(float(hinge["stock_on_hand"]), 30.0)
        self.assertEqual(hinge["supplier"], "Blum")
        self.assertEqual(float(hinge["min_order_qty"]), 50.0)
        self.assertEqual(int(hinge["lead_time_days"]), 30)

    def test_items_that_received_nothing_keep_their_stock_date(self) -> None:
        procurement_store.apply_procurement_receipts(
            pd.DataFrame([{"product": "Петля Blum 71B3550", "quantity": 20}]), updated_by="приёмка"
        )
        handle = procurement_store.load_procurement_items().set_index("product").loc["Ручка скоба 128"]
        self.assertEqual(handle["updated_by"], "склад")
        self.assertEqual(handle["updated_at"], self.before.loc["Ручка скоба 128", "updated_at"])

    def test_goods_new_to_the_catalogue_are_added(self) -> None:
        procurement_store.apply_procurement_receipts(
            pd.DataFrame([{"product": "Шуруп 4*16", "quantity": 100}]), updated_by="приёмка"
        )
        items = procurement_store.load_procurement_items().set_index("product")
        self.assertEqual(float(items.loc["Шуруп 4*16", "stock_on_hand"]), 100.0)
        self.assertEqual(len(items), 3)


if __name__ == "__main__":
    unittest.main()
