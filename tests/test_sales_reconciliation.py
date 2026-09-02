from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from data_quality import analyze_sales_quality


def _prepared_frame(revenue: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-01"]),
            "product": ["Тестовый товар"],
            "product_key": ["Тестовый товар"],
            "item_code": [""],
            "category": ["Товары"],
            "manager": ["Менеджер"],
            "quantity": [1.0],
            "revenue": [revenue],
            "cost": [80.0],
            "margin": [20.0],
            "margin_pct": [20.0],
        }
    )


class SalesReconciliationTests(unittest.TestCase):
    def test_matching_1c_total_is_reported(self) -> None:
        raw = pd.DataFrame({"Дата": ["01.08.2026"], "Номенклатура": ["Тестовый товар"], "Всего": [100.0]})
        raw.attrs["sales_report_totals"] = {
            "income": 88.5,
            "vat": 10.62,
            "sales_tax": 0.88,
            "total": 100.0,
        }

        report = analyze_sales_quality(
            raw,
            _prepared_frame(100.0),
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
        )

        self.assertNotEqual(report.status, "blocked")
        self.assertEqual(report.reconciliation_metrics["difference"], 0.0)
        self.assertEqual(report.reconciliation_metrics["source_income"], 88.5)

    def test_mismatched_1c_total_blocks_save(self) -> None:
        raw = pd.DataFrame({"Дата": ["01.08.2026"], "Номенклатура": ["Тестовый товар"], "Всего": [100.0]})
        raw.attrs["sales_report_totals"] = {"income": 88.5, "total": 100.0}

        report = analyze_sales_quality(
            raw,
            _prepared_frame(200.0),
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
        )

        self.assertEqual(report.status, "blocked")
        self.assertFalse(report.can_save)
        self.assertTrue(any(issue.title == "Итог файла не сходится" for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
