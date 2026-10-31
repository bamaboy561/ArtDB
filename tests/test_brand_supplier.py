from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_center import build_sku_change_preview  # noqa: E402
from procurement_analytics import build_procurement_forecast  # noqa: E402
from sales_analytics import apply_brand_supplier  # noqa: E402
from supplier_order_forms import build_order_form_rows  # noqa: E402


def sales(*rows: tuple) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["product", "brand", "supplier"])
    frame["product_key"] = frame["product"]
    return frame


class ApplyBrandSupplierTest(unittest.TestCase):
    def test_brand_replaces_a_guessed_or_missing_supplier(self) -> None:
        result = apply_brand_supplier(
            sales(
                ("Плита 1", "WQ", "Не назначен"),
                ("Плита 2", " WQ ", "Угаданный"),
                ("Петля", "Не назначен", "Hettich"),
                ("Ручка", "", "Nuomi"),
            )
        )
        self.assertEqual(result["supplier"].tolist(), ["WQ", "WQ", "Hettich", "Nuomi"])

    def test_supplier_assigned_by_hand_stays(self) -> None:
        assignments = pd.DataFrame({"product_key": ["плита 1"], "product": ["Плита 1"], "supplier": ["Ручной"]})
        result = apply_brand_supplier(
            sales(("Плита 1", "WQ", "Ручной"), ("Плита 2", "WQ", "")),
            assignments,
        )
        self.assertEqual(result["supplier"].tolist(), ["Ручной", "WQ"])

    def test_source_frame_is_untouched_by_default(self) -> None:
        source = sales(("Плита 1", "WQ", ""))
        apply_brand_supplier(source)
        self.assertEqual(source["supplier"].tolist(), [""])


class ForecastSupplierTest(unittest.TestCase):
    PRODUCTS = {
        "US77301/PGR4201 Маффин 18*1220*2800мм": "branded",
        "US77306/PGR4206 Бисквит 18*1220*2800мм": "manual",
        "Петля": "plain",
    }

    def forecast(self, items: pd.DataFrame) -> pd.DataFrame:
        dates = pd.date_range("2026-05-15", periods=6, freq="MS")
        rows = []
        for product in self.PRODUCTS:
            for day in dates:
                rows.append(
                    {
                        "date": day,
                        "product": product,
                        "quantity": 10.0,
                        "revenue": 100.0,
                        "cost": 60.0,
                        "margin": 40.0,
                        "category": "К",
                    }
                )
        data = pd.DataFrame(rows)
        data["product_key"] = data["product"]
        data["supplier"] = data["product"].map(
            {
                "US77301/PGR4201 Маффин 18*1220*2800мм": "WQ",
                "US77306/PGR4206 Бисквит 18*1220*2800мм": "Ручной",
                "Петля": "Hettich",
            }
        )
        data["brand"] = data["product"].map(
            {
                "US77301/PGR4201 Маффин 18*1220*2800мм": "WQ",
                "US77306/PGR4206 Бисквит 18*1220*2800мм": "WQ",
                "Петля": "Не назначен",
            }
        )
        return build_procurement_forecast(
            data,
            history_months=6,
            coverage_days=30,
            lead_time_days=14,
            safety_days=7,
            min_active_months=1,
            procurement_items=items,
            inbound_orders=pd.DataFrame(),
        ).set_index("product")

    def test_brand_wins_over_a_stored_supplier_and_a_manual_one_over_the_brand(self) -> None:
        items = pd.DataFrame(
            [
                {"product": "US77301/PGR4201 Маффин 18*1220*2800мм", "supplier": "Старый", "brand": "WQ"},
                {"product": "US77306/PGR4206 Бисквит 18*1220*2800мм", "supplier": "Старый", "brand": "WQ"},
                {"product": "Петля", "supplier": "Склад", "brand": ""},
                {"product": "Без продаж", "supplier": "Старый", "brand": "WQ"},
            ]
        )
        forecast = self.forecast(items)
        self.assertEqual(forecast.loc["US77301/PGR4201 Маффин 18*1220*2800мм", "supplier"], "WQ")
        self.assertEqual(forecast.loc["US77306/PGR4206 Бисквит 18*1220*2800мм", "supplier"], "Ручной")
        self.assertEqual(forecast.loc["Петля", "supplier"], "Склад")
        self.assertEqual(forecast.loc["Без продаж", "supplier"], "WQ")

    def test_goods_of_brand_wq_reach_the_wq_order_form(self) -> None:
        items = pd.DataFrame(
            [
                {"product": "US77301/PGR4201 Маффин 18*1220*2800мм", "supplier": "", "brand": "WQ"},
                {"product": "Петля", "supplier": "", "brand": ""},
            ]
        )
        rows = build_order_form_rows(self.forecast(items).reset_index())
        self.assertEqual(rows["name"].tolist(), ["Маффин"])
        self.assertEqual(rows["code"].tolist(), ["WQ63893M"])


class CleanupPreviewTest(unittest.TestCase):
    @staticmethod
    def row(**values: str) -> dict[str, object]:
        base = {
            "select": True,
            "product_key": "k",
            "product": "Товар",
            "current_supplier": "Старый",
            "current_category": "К",
            "current_brand": "",
            "current_item_code": "",
            "new_supplier": "",
            "new_category": "",
            "new_brand": "",
            "new_item_code": "",
        }
        return {**base, **values}

    def test_new_brand_brings_its_supplier(self) -> None:
        preview = build_sku_change_preview(pd.DataFrame([self.row()]), bulk_brand="WQ")
        self.assertEqual(preview.iloc[0]["after_supplier"], "WQ")
        self.assertEqual(preview.iloc[0]["changed_fields"], "supplier, brand")

    def test_supplier_chosen_together_with_the_brand_stays(self) -> None:
        preview = build_sku_change_preview(pd.DataFrame([self.row()]), bulk_brand="WQ", bulk_supplier="Другой")
        self.assertEqual(preview.iloc[0]["after_supplier"], "Другой")

    def test_unchanged_brand_leaves_the_supplier(self) -> None:
        preview = build_sku_change_preview(pd.DataFrame([self.row(current_brand="WQ", new_category="Новая")]))
        self.assertEqual(preview.iloc[0]["after_supplier"], "Старый")


if __name__ == "__main__":
    unittest.main()
