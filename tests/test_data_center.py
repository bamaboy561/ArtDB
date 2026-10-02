from __future__ import annotations

from datetime import date
import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from data_center import (
    build_sku_alias_candidates,
    build_sku_change_preview,
    build_sku_merge_preview,
    classify_cleanup_priority,
    evaluate_freshness,
)


class DataCenterFreshnessTests(unittest.TestCase):
    def test_recent_source_is_current(self) -> None:
        status = evaluate_freshness("2026-10-01", today=date(2026, 10, 2))

        self.assertEqual(status.label, "Актуально")
        self.assertEqual(status.tone, "success")
        self.assertEqual(status.age_days, 1)

    def test_aging_source_requests_update(self) -> None:
        status = evaluate_freshness("2026-09-30", today=date(2026, 10, 2))

        self.assertEqual(status.label, "Пора обновить")
        self.assertEqual(status.tone, "warning")

    def test_missing_source_is_flagged(self) -> None:
        status = evaluate_freshness(None, today=date(2026, 10, 2))

        self.assertEqual(status.label, "Нет данных")
        self.assertEqual(status.tone, "danger")
        self.assertIsNone(status.age_days)


class DataCenterCleanupPriorityTests(unittest.TestCase):
    def test_three_issues_are_critical_even_for_low_revenue(self) -> None:
        priority = classify_cleanup_priority(3, 0.1)

        self.assertEqual(priority.label, "Критично")
        self.assertEqual(priority.rank, 1)

    def test_high_revenue_single_issue_is_high_priority(self) -> None:
        priority = classify_cleanup_priority(1, 4.0)

        self.assertEqual(priority.label, "Высокий")
        self.assertEqual(priority.rank, 2)

    def test_low_impact_single_issue_is_low_priority(self) -> None:
        priority = classify_cleanup_priority(1, 0.1)

        self.assertEqual(priority.label, "Низкий")
        self.assertEqual(priority.rank, 4)


class DataCenterSkuChangePreviewTests(unittest.TestCase):
    def test_bulk_value_applies_only_to_selected_changed_rows(self) -> None:
        edited = pd.DataFrame(
            [
                {
                    "select": True,
                    "product_key": "SKU-1",
                    "product": "Товар 1",
                    "current_supplier": "",
                    "new_supplier": "",
                    "current_category": "Старая",
                    "new_category": "Старая",
                    "current_brand": "Бренд 1",
                    "new_brand": "Бренд 2",
                    "current_item_code": "001",
                    "new_item_code": "001",
                },
                {
                    "select": False,
                    "product_key": "SKU-2",
                    "product": "Товар 2",
                    "current_supplier": "",
                    "new_supplier": "",
                    "current_category": "Старая",
                    "new_category": "Новая",
                    "current_brand": "",
                    "new_brand": "",
                    "current_item_code": "002",
                    "new_item_code": "002",
                },
            ]
        )

        preview = build_sku_change_preview(edited, bulk_supplier="Hettich")

        self.assertEqual(len(preview), 1)
        self.assertEqual(preview.iloc[0]["after_supplier"], "Hettich")
        self.assertEqual(preview.iloc[0]["after_brand"], "Бренд 2")
        self.assertEqual(preview.iloc[0]["changed_fields"], "supplier, brand")


class DataCenterSkuAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sales = pd.DataFrame(
            [
                {
                    "product_key": "OLD-1",
                    "product": "Петля 110° ",
                    "item_code": "HT-01",
                    "category": "Петли",
                    "brand": "Hettich",
                    "supplier": "Hettich",
                    "revenue": 100.0,
                    "quantity": 1.0,
                },
                {
                    "product_key": "MAIN-1",
                    "product": "Петля 110",
                    "item_code": "HT-01",
                    "category": "Петли",
                    "brand": "Hettich",
                    "supplier": "Hettich",
                    "revenue": 900.0,
                    "quantity": 9.0,
                },
            ]
        )

    def test_candidate_prefers_higher_revenue_as_canonical(self) -> None:
        candidates = build_sku_alias_candidates(self.sales)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates.iloc[0]["source_product_key"], "OLD-1")
        self.assertEqual(candidates.iloc[0]["canonical_product_key"], "MAIN-1")
        self.assertEqual(candidates.iloc[0]["reason"], "Совпадает код товара")

    def test_merge_preview_preserves_combined_totals(self) -> None:
        preview = build_sku_merge_preview(
            self.sales,
            source_product_key="OLD-1",
            canonical_product_key="MAIN-1",
        )

        self.assertEqual(preview.iloc[0]["combined_revenue"], 1000.0)
        self.assertEqual(preview.iloc[0]["combined_lines"], 2)


if __name__ == "__main__":
    unittest.main()
