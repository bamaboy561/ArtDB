from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import procurement_order_store as orders_store  # noqa: E402


def stock(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["product", "stock_on_hand", "updated_at"])


def file_stock(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["product", "stock_on_hand"])


def sales(*rows: tuple) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["product", "date", "quantity"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def orders(*rows: tuple) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["order_id", "supplier", "status", "order_date"])
    frame["order_date"] = pd.to_datetime(frame["order_date"])
    return frame


def items(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["order_id", "product", "quantity", "received_quantity"])


class ArrivalLinesTest(unittest.TestCase):
    def build(self, order_rows, item_rows, before, in_file, sold=None, *, full_snapshot=True) -> pd.DataFrame:
        return orders_store.build_arrival_lines(
            order_rows,
            item_rows,
            before,
            in_file,
            sold if sold is not None else sales(),
            full_snapshot=full_snapshot,
        )

    def test_growth_plus_sales_since_the_stored_stock(self) -> None:
        lines = self.build(
            orders(("A", "WQ", "in_transit", "2026-10-01")),
            items(("A", "Плита", 50.0, 0.0)),
            stock(("Плита", 20.0, "2026-10-10T09:00:00")),
            file_stock(("плита", 62.0)),
            sales(
                ("Плита", "2026-10-09", 100.0),  # Already in the stored stock.
                ("Плита", "2026-10-10", 100.0),  # The day of the stored stock: already counted too.
                ("Плита", "2026-10-11", 5.0),
                ("Плита", "2026-10-20", 3.0),
                ("Другой", "2026-10-20", 7.0),
            ),
        )
        row = lines.iloc[0]
        self.assertEqual((row["stock_before"], row["stock_in_file"], row["sold_qty"]), (20.0, 62.0, 8.0))
        self.assertEqual(row["arrived_qty"], 50.0)

    def test_arrival_is_capped_and_never_negative(self) -> None:
        order_rows = orders(("A", "WQ", "in_transit", "2026-10-01"))
        item_rows = items(("A", "Плита", 10.0, 0.0))
        capped = self.build(order_rows, item_rows, stock(("Плита", 0.0, "")), file_stock(("Плита", 99.0)))
        self.assertEqual(capped.iloc[0]["arrived_qty"], 10.0)
        fell = self.build(order_rows, item_rows, stock(("Плита", 30.0, "")), file_stock(("Плита", 5.0)))
        self.assertEqual(fell.iloc[0]["arrived_qty"], 0.0)

    def test_only_open_quantities_of_active_orders(self) -> None:
        lines = self.build(
            orders(
                ("OPEN", "WQ", "in_transit", "2026-10-01"),
                ("PLACED", "WQ", "ordered", "2026-10-02"),
                ("DRAFT", "WQ", "draft", "2026-10-03"),
                ("DONE", "WQ", "received", "2026-10-04"),
                ("OFF", "WQ", "cancelled", "2026-10-05"),
            ),
            items(
                ("OPEN", "Плита", 50.0, 30.0),
                ("OPEN", "Кромка", 10.0, 10.0),
                ("PLACED", "HPL", 5.0, 0.0),
                ("DRAFT", "Плита", 5.0, 0.0),
                ("DONE", "Плита", 5.0, 5.0),
                ("OFF", "Плита", 5.0, 0.0),
            ),
            stock(),
            file_stock(("Плита", 100.0), ("HPL", 1.0)),
        )
        self.assertEqual(lines["order_id"].tolist(), ["OPEN", "PLACED"])
        self.assertEqual(lines["remaining_qty"].tolist(), [20.0, 5.0])
        self.assertEqual(lines["arrived_qty"].tolist(), [20.0, 1.0])

    def test_oldest_order_takes_the_arrival_first(self) -> None:
        lines = self.build(
            orders(("NEW", "WQ", "in_transit", "2026-10-10"), ("OLD", "WQ", "in_transit", "2026-10-01")),
            items(("NEW", "Плита", 10.0, 0.0), ("OLD", "Плита", 10.0, 0.0)),
            stock(),
            file_stock(("Плита", 15.0)),
        )
        self.assertEqual(dict(zip(lines["order_id"], lines["arrived_qty"])), {"OLD": 10.0, "NEW": 5.0})

    def test_partial_file_skips_products_it_does_not_list(self) -> None:
        order_rows = orders(("A", "WQ", "in_transit", "2026-10-01"))
        item_rows = items(("A", "Плита", 10.0, 0.0), ("A", "Кромка", 10.0, 0.0))
        before = stock(("Кромка", 4.0, ""))
        in_file = file_stock(("Плита", 3.0))
        partial = self.build(order_rows, item_rows, before, in_file, full_snapshot=False)
        self.assertEqual(partial["product"].tolist(), ["Плита"])
        full = self.build(order_rows, item_rows, before, in_file, full_snapshot=True)
        self.assertEqual(full.set_index("product").loc["Кромка", "stock_in_file"], 0.0)
        self.assertEqual(full.set_index("product").loc["Кромка", "arrived_qty"], 0.0)

    def test_no_open_orders_gives_an_empty_table(self) -> None:
        lines = self.build(pd.DataFrame(), pd.DataFrame(), stock(), file_stock(("Плита", 1.0)))
        self.assertTrue(lines.empty)
        self.assertIn("arrived_qty", lines.columns)


class OrderReceiptFileModeTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        patches = [
            mock.patch.object(orders_store, "database_enabled", return_value=False),
            mock.patch.object(orders_store, "DATA_DIR", base),
            mock.patch.object(orders_store, "PROCUREMENT_ORDERS_PATH", base / "orders.json"),
            mock.patch.object(orders_store, "PROCUREMENT_ORDER_ITEMS_PATH", base / "items.json"),
            mock.patch.object(orders_store, "apply_procurement_receipts"),
        ]
        for patch in patches:
            started = patch.start()
            self.addCleanup(patch.stop)
        self.stock_receipts = started

    def create_order(self) -> str:
        return orders_store.create_procurement_order(
            supplier="WQ",
            items_frame=pd.DataFrame(
                [{"product": "Плита", "quantity": 50.0}, {"product": "Кромка", "quantity": 100.0}]
            ),
            status="in_transit",
        )["order_id"]

    def arrive(self, order_id: str, product: str, quantity: float) -> set[str]:
        return orders_store.apply_order_arrivals(
            pd.DataFrame([{"order_id": order_id, "product": product, "arrived_qty": quantity}]),
            updated_by="tester",
        )

    def in_transit(self) -> dict[str, float]:
        summary = orders_store.build_open_order_summary(
            orders_store.load_procurement_orders(),
            orders_store.load_procurement_order_items(),
        )
        return dict(zip(summary["product"], summary["ordered_in_transit_qty"]))

    def status(self, order_id: str) -> str:
        frame = orders_store.load_procurement_orders()
        return frame.loc[frame["order_id"] == order_id, "status"].iloc[0]

    def test_partial_delivery_keeps_the_rest_on_the_way(self) -> None:
        order_id = self.create_order()
        self.assertEqual(self.arrive(order_id, "Плита", 30.0), set())
        self.assertEqual(self.status(order_id), "in_transit")
        self.assertEqual(self.in_transit(), {"Кромка": 100.0, "Плита": 20.0})
        self.stock_receipts.assert_not_called()

    def test_order_closes_when_everything_has_arrived(self) -> None:
        order_id = self.create_order()
        self.arrive(order_id, "Плита", 50.0)
        self.assertEqual(self.arrive(order_id, "Кромка", 100.0), {order_id})
        self.assertEqual(self.status(order_id), "received")
        self.assertEqual(self.in_transit(), {})

    def test_arrival_beyond_the_order_is_not_counted(self) -> None:
        order_id = self.create_order()
        self.arrive(order_id, "Плита", 500.0)
        lines = orders_store.load_procurement_order_items().set_index("product")
        self.assertEqual(lines.loc["Плита", "received_quantity"], 50.0)

    def test_closed_order_takes_no_arrivals(self) -> None:
        order_id = self.create_order()
        orders_store.update_procurement_order(order_id, status="cancelled")
        self.assertEqual(self.arrive(order_id, "Плита", 50.0), set())
        lines = orders_store.load_procurement_order_items().set_index("product")
        self.assertEqual(lines.loc["Плита", "received_quantity"], 0.0)

    def test_manual_receipt_adds_only_what_has_not_arrived(self) -> None:
        order_id = self.create_order()
        self.arrive(order_id, "Плита", 30.0)
        orders_store.apply_procurement_order_receipt(order_id, updated_by="tester")
        receipt = self.stock_receipts.call_args.args[0].set_index("product")["quantity"].to_dict()
        self.assertEqual(receipt, {"Плита": 20.0, "Кромка": 100.0})
        self.assertEqual(self.status(order_id), "received")
        lines = orders_store.load_procurement_order_items().set_index("product")
        self.assertEqual(lines["received_quantity"].to_dict(), {"Плита": 50.0, "Кромка": 100.0})

    def test_reopened_order_is_on_the_way_again(self) -> None:
        order_id = self.create_order()
        orders_store.apply_procurement_order_receipt(order_id, updated_by="tester")
        orders_store.update_procurement_order(order_id, status="in_transit")
        self.assertEqual(self.in_transit(), {"Кромка": 100.0, "Плита": 50.0})

    def test_overview_shows_what_is_left(self) -> None:
        order_id = self.create_order()
        self.arrive(order_id, "Плита", 30.0)
        overview = orders_store.build_procurement_order_overview(
            orders_store.load_procurement_orders(),
            orders_store.load_procurement_order_items(),
        ).iloc[0]
        self.assertEqual(overview["total_quantity"], 150.0)
        self.assertEqual(overview["remaining_quantity"], 120.0)

    def test_orders_saved_before_the_change_are_read_as_nothing_received(self) -> None:
        orders_store.ensure_procurement_order_store()
        orders_store.PROCUREMENT_ORDER_ITEMS_PATH.write_text(
            '[{"order_id": "OLD", "product": "Плита", "quantity": 5, "unit_cost": 0, "comment": ""}]',
            encoding="utf-8",
        )
        self.assertEqual(orders_store.load_procurement_order_items()["received_quantity"].tolist(), [0.0])


if __name__ == "__main__":
    unittest.main()
