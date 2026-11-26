from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sales_analytics import prefer_total_revenue_column  # noqa: E402


def chosen(frame: pd.DataFrame, revenue: str | None) -> str | None:
    mapping = {"revenue": revenue}
    prefer_total_revenue_column(frame, mapping)
    return mapping["revenue"]


class RevenueColumnTest(unittest.TestCase):
    """«Всего» (with VAT and tax) is preferred, but never an empty one the 1C reader added."""

    def test_an_empty_total_column_keeps_the_chosen_revenue(self) -> None:
        self.assertEqual(chosen(pd.DataFrame({"Доход": [100.0, 50.0], "Всего": [None, None]}), "Доход"), "Доход")

    def test_a_partly_filled_total_keeps_the_chosen_revenue(self) -> None:
        self.assertEqual(chosen(pd.DataFrame({"Доход": [100.0, 50.0], "Всего": [118.0, None]}), "Доход"), "Доход")

    def test_a_filled_total_is_taken(self) -> None:
        self.assertEqual(chosen(pd.DataFrame({"Доход": [100.0, 50.0], "Всего": [118.0, 59.0]}), "Доход"), "Всего")
        self.assertEqual(chosen(pd.DataFrame({"Всего": [118.0, 59.0]}), None), "Всего")


if __name__ == "__main__":
    unittest.main()
