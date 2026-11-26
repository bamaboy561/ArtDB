from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sales_analytics import build_abc_analysis  # noqa: E402


def classes(revenue: list[float]) -> list[str]:
    summary = pd.DataFrame({"group_name": [f"товар {index}" for index in range(len(revenue))], "revenue": revenue})
    return build_abc_analysis(summary)["abc_class"].tolist()


class AbcClassTest(unittest.TestCase):
    def test_the_product_that_crosses_80_percent_is_a(self) -> None:
        self.assertEqual(classes([85.0, 10.0, 5.0]), ["A", "B", "C"])

    def test_even_sales(self) -> None:
        self.assertEqual(classes([10.0] * 10), ["A"] * 8 + ["B"] * 2)


if __name__ == "__main__":
    unittest.main()
