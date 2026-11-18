from __future__ import annotations

from datetime import date
import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fittings_plan import (  # noqa: E402
    MotivationTest,
    fittings_mask,
    monthly_structure,
    payout_coefficient,
    scope_fittings_plans,
)


def sales(*lines: tuple[str, float, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(day) for day, _, _ in lines],
            "revenue": [revenue for _, revenue, _ in lines],
            "Путь категории": [path for _, _, path in lines],
            "category": [path.split(">")[0].strip() for _, _, path in lines],
        }
    )


class CoefficientTest(unittest.TestCase):
    def test_the_scale_of_the_proposal(self) -> None:
        for share, expected in ((0.0, 0.80), (0.10, 0.90), (0.13, 0.93), (0.135, 0.935), (0.14, 0.94), (0.15, 1.0), (0.30, 1.0)):
            self.assertAlmostEqual(payout_coefficient(share), expected, msg=share)

    def test_the_threshold_is_the_unrounded_share(self) -> None:
        # 14.999% shows as 15.00% after rounding, yet stays below the target.
        self.assertAlmostEqual(payout_coefficient(0.14999), 0.94999)

    def test_no_share_gives_no_coefficient(self) -> None:
        self.assertIsNone(payout_coefficient(None))


class FittingsMaskTest(unittest.TestCase):
    def test_the_seven_groups_are_fittings_and_the_rest_is_board(self) -> None:
        frame = sales(
            ("2026-10-01", 1, "Фурнитура > Hettich"),
            ("2026-10-01", 1, "Фурнитура > Nuomi > LIGHTING ACCESSORIES"),
            ("2026-10-01", 1, "Фурнитура > Emaks > NEXUS AKS"),
            ("2026-10-01", 1, "Фурнитура > ITALIANA FERRAMENTA"),
            ("2026-10-01", 1, "Фурнитура > Самет"),
            ("2026-10-01", 1, "Фурнитура > Слорос"),
            ("2026-10-01", 1, "Фурнитура > Слорос Titus"),
            ("2026-10-01", 1, "ЛДСП > Экстраверт > ЛДСП Экстраверт"),
            ("2026-10-01", 1, "Услуги > Распил"),
        )
        self.assertEqual(fittings_mask(frame).tolist(), [True] * 7 + [False, False])

    def test_without_a_group_path_the_category_decides(self) -> None:
        frame = pd.DataFrame({"category": ["Фурнитура", "ЛДСП"], "revenue": [1, 1]})
        self.assertEqual(fittings_mask(frame).tolist(), [True, False])


class MonthlyStructureTest(unittest.TestCase):
    def test_the_proposals_october(self) -> None:
        # October 2026 of the proposal: 2 718 450,52 of 41 808 585,92 are fittings, 6,50%.
        frame = sales(
            ("2026-10-10", 2_718_450.52, "Фурнитура > Hettich"),
            ("2026-10-12", 41_808_585.92 - 2_718_450.52, "ЛДСП > Экстраверт"),
        )
        month = monthly_structure(frame).iloc[0]
        self.assertAlmostEqual(month["share"], 0.0650, places=4)
        self.assertAlmostEqual(month["coefficient"], 0.865, places=3)
        self.assertFalse(month["in_test"])

    def test_january_and_april_count_only_the_test_days(self) -> None:
        test = MotivationTest(date(2027, 1, 5), date(2027, 4, 4))
        frame = sales(
            ("2027-01-03", 1000, "Фурнитура > Hettich"),  # Before the test: left out.
            ("2027-01-10", 150, "Фурнитура > Hettich"),
            ("2027-01-20", 850, "ЛДСП > Экстраверт"),
            ("2027-04-02", 100, "Фурнитура > Nuomi"),
            ("2027-04-20", 5000, "Фурнитура > Nuomi"),  # After the test: left out.
            ("2027-04-03", 900, "ЛДСП > Экстраверт"),
        )
        months = monthly_structure(frame, test=test).set_index("month_label")
        self.assertEqual(months.loc["2027-01", "days_label"], "5–31 янв")
        self.assertAlmostEqual(months.loc["2027-01", "share"], 0.15)
        self.assertEqual(months.loc["2027-01", "coefficient"], 1.0)
        self.assertEqual(months.loc["2027-04", "days_label"], "1–4 апр")
        self.assertAlmostEqual(months.loc["2027-04", "coefficient"], 0.90)

    def test_no_sales_leave_the_share_undefined(self) -> None:
        frame = sales(("2027-02-10", 100, "Фурнитура > Hettich"), ("2027-02-11", -100, "ЛДСП > Экстраверт"))
        month = monthly_structure(frame).iloc[0]
        self.assertIsNone(month["share"])
        self.assertIsNone(month["coefficient"])

    def test_the_plan_sets_the_target_and_the_sum(self) -> None:
        frame = sales(("2027-02-10", 120, "Фурнитура > Hettich"), ("2027-02-11", 880, "ЛДСП > Экстраверт"))
        plans = pd.DataFrame({"month_label": ["2027-02"], "share_plan": [0.12], "fittings_plan": [200.0]})
        month = monthly_structure(frame, plans).iloc[0]
        self.assertEqual((month["share_plan"], month["coefficient"], month["plan_execution"]), (0.12, 1.0, 0.6))


class ScopePlansTest(unittest.TestCase):
    PLANS = pd.DataFrame(
        {
            "plan_month": pd.to_datetime(["2027-01-01", "2027-01-01", "2027-01-01", "2027-02-01"]),
            "salon": ["Artisan", "Монолит", "", ""],
            "revenue_plan": [1000, 2000, 3000, 3000],
            "fittings_share_plan": [0.15, 0.17, None, 0.14],
            "fittings_revenue_plan": [100, 300, 500, None],
        }
    )

    def test_salon_plans_add_up_and_their_shares_average(self) -> None:
        plans = scope_fittings_plans(self.PLANS, ["Artisan", "Монолит"], allow_network_fallback=False).set_index("month_label")
        self.assertAlmostEqual(plans.loc["2027-01", "share_plan"], 0.16)
        self.assertEqual(plans.loc["2027-01", "fittings_plan"], 400)
        # The network's share is the target for one salon too; its sum in сом is not.
        self.assertAlmostEqual(plans.loc["2027-02", "share_plan"], 0.14)
        self.assertTrue(pd.isna(plans.loc["2027-02", "fittings_plan"]))

    def test_a_salon_without_a_fittings_plan_takes_the_networks_share(self) -> None:
        plans = scope_fittings_plans(self.PLANS, ["Стройпарк"], allow_network_fallback=False).set_index("month_label")
        self.assertTrue(pd.isna(plans.loc["2027-01", "share_plan"]))
        self.assertAlmostEqual(plans.loc["2027-02", "share_plan"], 0.14)

    def test_the_network_plan_fills_months_the_salons_leave_open(self) -> None:
        plans = scope_fittings_plans(self.PLANS, ["Artisan", "Монолит"], allow_network_fallback=True).set_index("month_label")
        self.assertEqual(plans.loc["2027-01", "fittings_plan"], 400)
        self.assertAlmostEqual(plans.loc["2027-02", "share_plan"], 0.14)


if __name__ == "__main__":
    unittest.main()
