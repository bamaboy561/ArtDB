from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_quality import build_catalog_health, missing_supplier_rows  # noqa: E402


class MissingSupplierRowsTest(unittest.TestCase):
    def forecast(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "product": ["Есть", "Пусто", "Не назначен", "Пробел", "Пропуск"],
                "supplier": ["WQ", "", "не назначен", "  ", None],
                "brand": ["WQ", "", "", "", ""],
                "forecast_qty": [9.0, 1.0, 5.0, 3.0, 2.0],
            }
        )

    def test_list_matches_the_card(self) -> None:
        forecast = self.forecast()
        rows = missing_supplier_rows(forecast)
        self.assertEqual(len(rows), build_catalog_health(pd.DataFrame(), forecast)["missing_supplier"])

    def test_biggest_forecast_comes_first(self) -> None:
        self.assertEqual(missing_supplier_rows(self.forecast())["product"].tolist(), ["Не назначен", "Пробел", "Пропуск", "Пусто"])

    def test_empty_forecast(self) -> None:
        self.assertTrue(missing_supplier_rows(pd.DataFrame()).empty)


if __name__ == "__main__":
    unittest.main()
