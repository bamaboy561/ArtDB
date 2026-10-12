from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from salon_data_store import _resolve_archive_date_overlaps  # noqa: E402
from sales_analytics import (  # noqa: E402
    EXCLUSION_SEVERITY_EMPTY,
    EXCLUSION_SEVERITY_ERROR,
    prepare_sales_data,
)

MAPPING = {"date": "Дата", "product": "Товар", "quantity": "Количество", "revenue": "Выручка"}


class PrepareSalesExclusionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.DataFrame(
            {
                "Дата": ["01.04.2026", "32.13.2026", None, "02.04.2026", "03.04.2026"],
                "Товар": ["Плита А", "Плита Б", "Итого", "Плита В", "Плита Г"],
                "Количество": [1, 2, 3, 4, 5],
                "Выручка": ["100", "200", "300", "сто рублей", "500"],
            }
        )

    def test_excluded_rows_carry_row_number_and_reason(self) -> None:
        result = prepare_sales_data(self.frame, MAPPING)

        self.assertEqual(result.data["product"].tolist(), ["Плита А", "Плита Г"])
        self.assertEqual(result.stats["raw_rows"], 5)
        self.assertEqual(result.stats["loaded_rows"], 2)
        self.assertEqual(result.stats["excluded_rows"], 3)
        self.assertEqual(result.stats["error_rows"], 2)

        by_row = result.exclusions.set_index("row_number")
        self.assertEqual(sorted(by_row.index.tolist()), [2, 3, 4])
        self.assertEqual(by_row.loc[2, "severity"], EXCLUSION_SEVERITY_ERROR)
        self.assertIn("32.13.2026", by_row.loc[2, "reason"])
        self.assertEqual(by_row.loc[3, "severity"], EXCLUSION_SEVERITY_EMPTY)
        self.assertIn("Пустая дата", by_row.loc[3, "reason"])
        self.assertEqual(by_row.loc[4, "severity"], EXCLUSION_SEVERITY_ERROR)
        self.assertIn("сто рублей", by_row.loc[4, "reason"])
        self.assertEqual(by_row.loc[4, "product"], "Плита В")

    def test_errors_are_listed_before_empty_rows(self) -> None:
        result = prepare_sales_data(self.frame, MAPPING)
        self.assertEqual(result.exclusions["severity"].iloc[0], EXCLUSION_SEVERITY_ERROR)
        self.assertEqual(result.exclusions["severity"].iloc[-1], EXCLUSION_SEVERITY_EMPTY)

    def test_clean_file_reports_nothing(self) -> None:
        result = prepare_sales_data(self.frame.iloc[[0, 4]], MAPPING)
        self.assertTrue(result.exclusions.empty)
        self.assertEqual(result.stats["excluded_rows"], 0)
        self.assertNotIn("_source_row", result.data.columns)


class ArchiveOverlapDetailsTest(unittest.TestCase):
    def test_superseded_upload_is_reported_per_date(self) -> None:
        data = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-01", "2026-04-01", "2026-04-01", "2026-04-02"]),
                "salon": ["Artisan"] * 4,
                "revenue": [10, 20, 30, 40],
                "_archive_upload_id": ["old", "old", "new", "new"],
                "_archive_uploaded_at": ["2026-04-02T10:00:00", "2026-04-02T10:00:00", "2026-04-03T10:00:00", "2026-04-03T10:00:00"],
                "_archive_report_date": ["2026-04-01", "2026-04-01", "2026-04-02", "2026-04-02"],
            }
        )

        clean, warning, details = _resolve_archive_date_overlaps(data)

        self.assertEqual(clean["revenue"].tolist(), [30, 40])
        self.assertIsNotNone(warning)
        self.assertEqual(len(details), 1)
        row = details.iloc[0]
        self.assertEqual(row["upload_id"], "old")
        self.assertEqual(row["winner_upload_id"], "new")
        self.assertEqual(int(row["rows"]), 2)
        self.assertEqual(pd.Timestamp(row["sales_date"]), pd.Timestamp("2026-04-01"))

    def test_no_overlap_returns_empty_details(self) -> None:
        data = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-01"]),
                "salon": ["Artisan"],
                "_archive_upload_id": ["one"],
                "_archive_uploaded_at": ["2026-04-02T10:00:00"],
                "_archive_report_date": ["2026-04-01"],
            }
        )
        _, warning, details = _resolve_archive_date_overlaps(data)
        self.assertIsNone(warning)
        self.assertTrue(details.empty)


if __name__ == "__main__":
    unittest.main()
