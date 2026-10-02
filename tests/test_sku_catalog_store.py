from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from sku_catalog_store import apply_sku_attribute_overrides


class SkuAttributeOverrideTests(unittest.TestCase):
    def test_override_matches_product_key_case_insensitively(self) -> None:
        sales = pd.DataFrame(
            [
                {
                    "product_key": "SKU-001",
                    "product": "Петля",
                    "category": "Фурнитура",
                    "brand": "",
                    "item_code": "001",
                }
            ]
        )
        overrides = pd.DataFrame(
            [
                {
                    "product_key": "sku-001",
                    "product": "Петля",
                    "category": "Петли",
                    "brand": "Hettich",
                    "item_code": "HT-001",
                    "is_archived": False,
                }
            ]
        )

        result = apply_sku_attribute_overrides(sales, overrides)

        self.assertEqual(result.iloc[0]["category"], "Петли")
        self.assertEqual(result.iloc[0]["brand"], "Hettich")
        self.assertEqual(result.iloc[0]["item_code"], "HT-001")
        self.assertEqual(result.iloc[0]["product_key"], "SKU-001")

    def test_blank_override_does_not_erase_source_value(self) -> None:
        sales = pd.DataFrame(
            [
                {
                    "product_key": "SKU-002",
                    "product": "Направляющая",
                    "category": "Фурнитура",
                    "brand": "Samet",
                    "item_code": "002",
                }
            ]
        )
        overrides = pd.DataFrame(
            [
                {
                    "product_key": "SKU-002",
                    "product": "Направляющая",
                    "category": "",
                    "brand": "",
                    "item_code": "",
                    "is_archived": False,
                }
            ]
        )

        result = apply_sku_attribute_overrides(sales, overrides)

        self.assertEqual(result.iloc[0]["category"], "Фурнитура")
        self.assertEqual(result.iloc[0]["brand"], "Samet")
        self.assertEqual(result.iloc[0]["item_code"], "002")


if __name__ == "__main__":
    unittest.main()
