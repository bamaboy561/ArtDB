from __future__ import annotations

from datetime import date
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from data_center import classify_cleanup_priority, evaluate_freshness


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


if __name__ == "__main__":
    unittest.main()
