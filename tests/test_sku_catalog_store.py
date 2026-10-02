from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from sku_catalog_store import (
    _validated_alias_records,
    apply_sku_aliases,
    apply_sku_attribute_overrides,
)


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


class SkuAliasTests(unittest.TestCase):
    def test_alias_chain_resolves_to_final_canonical_sku(self) -> None:
        sales = pd.DataFrame(
            [
                {"product_key": "A", "product": "Старое имя", "brand": "", "revenue": 100.0},
                {"product_key": "C", "product": "Основной товар", "brand": "Hettich", "revenue": 200.0},
            ]
        )
        aliases = pd.DataFrame(
            [
                {
                    "source_product_key": "A",
                    "source_product": "Старое имя",
                    "canonical_product_key": "B",
                    "canonical_product": "Промежуточное имя",
                    "is_archived": False,
                },
                {
                    "source_product_key": "B",
                    "source_product": "Промежуточное имя",
                    "canonical_product_key": "C",
                    "canonical_product": "Основной товар",
                    "is_archived": False,
                },
            ]
        )

        result = apply_sku_aliases(sales, aliases)

        self.assertEqual(result.iloc[0]["product_key"], "C")
        self.assertEqual(result.iloc[0]["product"], "Основной товар")
        self.assertEqual(result.iloc[0]["brand"], "Hettich")

    def test_inventory_aliases_are_aggregated(self) -> None:
        inventory = pd.DataFrame(
            [
                {"product": "Петля старая", "stock_on_hand": 3.0, "stock_value": 300.0},
                {"product": "Петля основная", "stock_on_hand": 7.0, "stock_value": 700.0},
            ]
        )
        aliases = pd.DataFrame(
            [
                {
                    "source_product_key": "OLD",
                    "source_product": "Петля старая",
                    "canonical_product_key": "MAIN",
                    "canonical_product": "Петля основная",
                    "is_archived": False,
                }
            ]
        )

        result = apply_sku_aliases(inventory, aliases, aggregate_inventory=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["product"], "Петля основная")
        self.assertEqual(result.iloc[0]["stock_on_hand"], 10.0)
        self.assertEqual(result.iloc[0]["stock_value"], 1000.0)

    def test_cycle_is_rejected(self) -> None:
        existing = pd.DataFrame(
            [
                {
                    "source_product_key": "A",
                    "canonical_product_key": "B",
                    "is_archived": False,
                }
            ]
        )
        proposed = pd.DataFrame(
            [
                {
                    "source_product_key": "B",
                    "source_product": "Товар B",
                    "canonical_product_key": "A",
                    "canonical_product": "Товар A",
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "цикл"):
            _validated_alias_records(proposed, updated_by="admin", existing_aliases=existing)


if __name__ == "__main__":
    unittest.main()
