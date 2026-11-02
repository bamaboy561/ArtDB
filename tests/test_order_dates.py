from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from procurement_analytics import add_order_dates, order_timeline, supplier_lead_time  # noqa: E402

TODAY = date(2026, 11, 2)


class OrderTimelineTest(unittest.TestCase):
    def test_settings_become_dates(self) -> None:
        timeline = order_timeline(TODAY, lead_time_days=14, coverage_days=35, safety_days=7)
        self.assertEqual(timeline.arrival, date(2026, 11, 16))
        self.assertEqual(timeline.covered_until, date(2026, 12, 21))
        self.assertEqual(timeline.reserve_until, date(2026, 12, 28))


class AddOrderDatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dated = add_order_dates(
            pd.DataFrame(
                {
                    "product": ["Кончится раньше", "Хватит", "Без спроса", "Почти вечный", "Уже нет"],
                    "lead_time_days": [14, 30, 14, 14, 14],
                    "stock_coverage_days": [5.7, 40.0, float("nan"), 1e9, -3.0],
                }
            ),
            TODAY,
        ).set_index("product")

    def test_arrival_uses_the_product_lead_time(self) -> None:
        self.assertEqual(self.dated.loc["Хватит", "arrival_date"], pd.Timestamp(2026, 12, 2))

    def test_stock_runs_out_on_its_last_whole_day(self) -> None:
        self.assertEqual(self.dated.loc["Кончится раньше", "stock_out_date"], pd.Timestamp(2026, 11, 7))
        self.assertEqual(self.dated.loc["Кончится раньше", "shortage_days"], 9)
        self.assertEqual(self.dated.loc["Хватит", "shortage_days"], 0)

    def test_no_demand_has_no_stock_out_date(self) -> None:
        self.assertTrue(pd.isna(self.dated.loc["Без спроса", "stock_out_date"]))
        self.assertEqual(self.dated.loc["Без спроса", "shortage_days"], 0)

    def test_without_stock_the_whole_lead_time_is_missing(self) -> None:
        self.assertTrue(pd.isna(self.dated.loc["Уже нет", "stock_out_date"]))
        self.assertEqual(self.dated.loc["Уже нет", "shortage_days"], 14)

    def test_huge_coverage_stays_a_valid_date(self) -> None:
        self.assertEqual(self.dated.loc["Почти вечный", "stock_out_date"].year, 2036)


class SupplierLeadTimeTest(unittest.TestCase):
    def test_the_slowest_product_of_the_supplier_counts(self) -> None:
        forecast = pd.DataFrame(
            {"supplier": ["WQ", "WQ", "Liamax"], "lead_time_days": [14, 45, 60]}
        )
        self.assertEqual(supplier_lead_time(forecast, lambda value: value == "WQ", 10), 45)

    def test_without_goods_the_setting_counts(self) -> None:
        forecast = pd.DataFrame({"supplier": ["Liamax"], "lead_time_days": [60]})
        self.assertEqual(supplier_lead_time(forecast, lambda value: value == "WQ", 10), 10)


if __name__ == "__main__":
    unittest.main()
