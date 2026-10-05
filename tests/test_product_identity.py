from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from inventory_analytics import prepare_inventory_data
from procurement_analytics import build_procurement_forecast
from product_identity import normalize_product_match_key, refine_product_category
from sales_analytics import prepare_sales_data


class ProductIdentityTests(unittest.TestCase):
    def test_cosmetic_separators_produce_the_same_match_key(self) -> None:
        left = "MDF/3039/ MATT DELPHI OAK/one 18*1.220*2.800"
        right = "MDF / 3039 /MATT DELPHI OAK / one 18×1,220х2,800"

        self.assertEqual(
            normalize_product_match_key(left),
            normalize_product_match_key(right),
        )

    def test_mdf_is_split_from_ldsp_but_regular_panels_are_not(self) -> None:
        self.assertEqual(
            refine_product_category("MDF/6026/SPECTRUM ROSE", "ЛДСП"),
            "МДФ панели",
        )
        self.assertEqual(
            refine_product_category("Пристенная панель 1021/S", "ЛДСП"),
            "ЛДСП",
        )
        self.assertEqual(
            refine_product_category("MDF/6026/SPECTRUM ROSE", "AGT"),
            "AGT",
        )


class ProductCategoryPreparationTests(unittest.TestCase):
    def test_sales_preparation_reclassifies_mdf_from_ldsp(self) -> None:
        source = pd.DataFrame(
            {
                "date": ["2026-09-30"],
                "product": ["MDF/6026/SPECTRUM ROSE/double 18*1.220*2.800"],
                "category": ["ЛДСП"],
                "quantity": [1],
                "revenue": [12500],
                "cost": [9000],
            }
        )
        mapping = {
            "date": "date",
            "product": "product",
            "category": "category",
            "quantity": "quantity",
            "revenue": "revenue",
            "cost": "cost",
        }

        prepared = prepare_sales_data(source, mapping).data

        self.assertEqual(prepared.iloc[0]["category"], "МДФ панели")

    def test_inventory_variants_are_aggregated_by_safe_match_key(self) -> None:
        source = pd.DataFrame(
            {
                "product": [
                    "MDF/3039/ MATT DELPHI OAK/one 18*1.220*2.800",
                    "MDF/3039/MATT DELPHI OAK/one 18×1.220×2.800",
                ],
                "stock": [15, 1],
                "value": [73000, 4400],
            }
        )
        mapping = {
            "product": "product",
            "stock_on_hand": "stock",
            "stock_value": "value",
        }

        prepared = prepare_inventory_data(source, mapping).data

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared.iloc[0]["stock_on_hand"], 16)
        self.assertEqual(prepared.iloc[0]["stock_value"], 77400)

    def test_procurement_forecast_uses_inventory_with_cosmetic_name_difference(self) -> None:
        sales = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-30"]),
                "product": ["MDF/6026/SPECTRUM ROSE/double 18*1.220*2.800"],
                "category": ["МДФ панели"],
                "quantity": [1.0],
                "revenue": [12500.0],
                "cost": [9000.0],
                "margin": [3500.0],
            }
        )
        inventory = pd.DataFrame(
            {
                "product": ["MDF / 6026 / SPECTRUM ROSE / double 18×1.220х2.800"],
                "stock_on_hand": [7.0],
                "stock_value": [70830.4],
                "supplier": ["AGT"],
            }
        )

        forecast = build_procurement_forecast(sales, procurement_items=inventory)

        self.assertEqual(len(forecast), 1)
        self.assertEqual(forecast.iloc[0]["stock_on_hand"], 7.0)
        self.assertEqual(forecast.iloc[0]["supplier"], "AGT")


if __name__ == "__main__":
    unittest.main()
