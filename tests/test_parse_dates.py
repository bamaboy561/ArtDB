from __future__ import annotations

from datetime import datetime
import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sales_analytics import parse_dates  # noqa: E402


def days(values: list[object]) -> list[str]:
    return [stamp.strftime("%Y-%m-%d %H:%M") if pd.notna(stamp) else "-" for stamp in parse_dates(pd.Series(values, dtype=object))]


class ParseDatesTest(unittest.TestCase):
    def test_a_year_first_date_with_time_keeps_its_month(self) -> None:
        self.assertEqual(days(["2026-11-05 10:30:00", "2026-11-20 00:00:00"]), ["2026-11-05 10:30", "2026-11-20 00:00"])

    def test_real_date_cells_next_to_a_total_row(self) -> None:
        self.assertEqual(days([datetime(2026, 11, 5), datetime(2026, 11, 20), "Итого"]), ["2026-11-05 00:00", "2026-11-20 00:00", "-"])

    def test_day_first_dates_with_and_without_time(self) -> None:
        self.assertEqual(days(["05.11.2026", "5.11.2026 10:15", "20.11.2026"]), ["2026-11-05 00:00", "2026-11-05 10:15", "2026-11-20 00:00"])

    def test_excel_day_numbers(self) -> None:
        self.assertEqual(days([46331, 3.0]), ["2026-11-05 00:00", "-"])

    def test_empty_cells(self) -> None:
        self.assertEqual(days([None, "", float("nan")]), ["-", "-", "-"])


if __name__ == "__main__":
    unittest.main()
