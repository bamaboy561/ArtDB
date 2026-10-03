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

    def test_mapped_revenue_difference_warns_without_blocking(self) -> None:
        raw = pd.DataFrame(
            {
                "Дата": ["01.08.2026", "01.08.2026"],
                "Номенклатура": ["Тестовый товар", "Служебная строка"],
                "Всего": [100.0, 50.0],
            }
        )
        prepared = pd.concat([_prepared_frame(50.0), _prepared_frame(50.0)], ignore_index=True)
        prepared.loc[1, "product"] = "Служебная строка"
        prepared.loc[1, "product_key"] = "Служебная строка"

        report = analyze_sales_quality(
            raw,
            prepared,
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
        )

        self.assertTrue(report.can_save)
        self.assertEqual(report.status, "warning")
        self.assertEqual(report.reconciliation_metrics["source_kind"], "mapped_column")
        self.assertEqual(report.reconciliation_metrics["difference"], -50.0)
        self.assertTrue(any(issue.title == "Сумма изменилась после очистки" for issue in report.issues))

    def test_existing_archive_date_requires_confirmation(self) -> None:
        raw = pd.DataFrame({"Дата": ["01.08.2026"], "Номенклатура": ["Тестовый товар"], "Всего": [100.0]})
        manifest = pd.DataFrame(
            {
                "salon": ["Artisan"],
                "report_date": ["2026-08-01"],
            }
        )

        report = analyze_sales_quality(
            raw,
            _prepared_frame(100.0),
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
            archive_manifest=manifest,
            salon_name="Artisan",
            expected_report_date=pd.Timestamp("2026-08-01").date(),
            replace_existing=True,
        )

        self.assertTrue(report.can_save)
        self.assertEqual(report.archive_metrics["matching_uploads"], 1)
        self.assertTrue(report.archive_metrics["will_replace"])
        self.assertTrue(any(issue.title == "За эту дату уже есть файл" for issue in report.issues))

    def test_existing_archive_date_blocks_save_without_replacement(self) -> None:
        raw = pd.DataFrame({"Дата": ["01.08.2026"], "Номенклатура": ["Тестовый товар"], "Всего": [100.0]})
        manifest = pd.DataFrame({"salon": ["Artisan"], "report_date": ["2026-08-01"]})

        report = analyze_sales_quality(
            raw,
            _prepared_frame(100.0),
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
            archive_manifest=manifest,
            salon_name="Artisan",
            expected_report_date=pd.Timestamp("2026-08-01").date(),
            replace_existing=False,
        )

        self.assertFalse(report.can_save)
        self.assertFalse(report.archive_metrics["will_replace"])

    def test_duplicates_and_unassigned_supplier_are_in_protocol(self) -> None:
        raw = pd.DataFrame(
            {
                "Дата": ["01.08.2026", "01.08.2026"],
                "Номенклатура": ["Тестовый товар", "Тестовый товар"],
                "Всего": [100.0, 100.0],
            }
        )
        prepared = pd.concat([_prepared_frame(100.0), _prepared_frame(100.0)], ignore_index=True)
        prepared["supplier"] = ""

        report = analyze_sales_quality(
            raw,
            prepared,
            {"date": "Дата", "product": "Номенклатура", "revenue": "Всего"},
        )

        self.assertEqual(report.processing_metrics["duplicate_rows"], 1)
        self.assertEqual(report.processing_metrics["unassigned_supplier_products"], 1)
        self.assertTrue(any(issue.title == "Есть похожие дубли" for issue in report.issues))
        self.assertTrue(any(issue.title == "Не всем товарам назначен поставщик" for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
