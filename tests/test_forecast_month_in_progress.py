from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from procurement_analytics import build_procurement_forecast  # noqa: E402


def forecast_until(end: str) -> pd.Series:
    dates = pd.date_range("2026-05-01", end)
    sales = pd.DataFrame({"date": dates, "product": "Петля", "quantity": 1.0, "revenue": 10.0, "cost": 6.0, "margin": 4.0})
    return build_procurement_forecast(sales).iloc[0]


class MonthInProgressTest(unittest.TestCase):
    """One sale a day is about 30 a month on any day of the month, not half of it early on."""

    def test_early_in_a_month_only_whole_months_count(self) -> None:
        early, whole = forecast_until("2026-11-05"), forecast_until("2026-10-31")
        self.assertAlmostEqual(early["forecast_qty"], whole["forecast_qty"], places=6)
        self.assertAlmostEqual(early["recommended_order_qty"], whole["recommended_order_qty"], places=6)

    def test_later_in_a_month_it_counts_as_a_whole_month(self) -> None:
        row = forecast_until("2026-11-20")
        self.assertAlmostEqual(row["forecast_qty"], 30.0, delta=1.0)
        self.assertAlmostEqual(row["trend_factor"], 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
