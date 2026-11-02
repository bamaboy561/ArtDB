from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sales_analytics import fit_period  # noqa: E402

FIRST = date(2025, 10, 1)
LAST = date(2026, 10, 25)


class FitPeriodTest(unittest.TestCase):
    def test_range_inside_the_data_stays(self) -> None:
        chosen = (date(2025, 10, 13), date(2025, 10, 20))
        self.assertEqual(fit_period(chosen, FIRST, LAST), chosen)

    def test_range_is_cut_to_the_data_instead_of_lost(self) -> None:
        chosen = (date(2025, 9, 1), date(2025, 10, 20))
        self.assertEqual(fit_period(chosen, FIRST, LAST), (FIRST, date(2025, 10, 20)))

    def test_range_outside_the_data_is_dropped(self) -> None:
        self.assertIsNone(fit_period((date(2024, 1, 1), date(2024, 2, 1)), FIRST, LAST))

    def test_unfinished_choice_is_not_a_range(self) -> None:
        self.assertIsNone(fit_period((date(2025, 10, 13),), FIRST, LAST))
        self.assertIsNone(fit_period((), FIRST, LAST))
        self.assertIsNone(fit_period(None, FIRST, LAST))


if __name__ == "__main__":
    unittest.main()
