from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sales_analytics import prepare_sales_data  # noqa: E402


class GroupSupplierTest(unittest.TestCase):
    """A 1C group row naming a supplier applies to its own goods only."""

    def test_the_next_group_does_not_inherit_the_supplier(self) -> None:
        raw = pd.DataFrame(
            {
                "Дата": [None, "05.11.2026", None, "05.11.2026"],
                "Номенклатура": ["Петли Blum", "Петля 110", "Ручки", "Ручка скоба 128"],
                "Количество": [None, 2, None, 3],
                "Сумма": [None, 100, None, 60],
            }
        )
        mapping = {"date": "Дата", "product": "Номенклатура", "quantity": "Количество", "revenue": "Сумма"}
        suppliers = prepare_sales_data(raw, mapping).data.set_index("product")["supplier"]
        self.assertEqual(suppliers["Петля 110"], "Blum")
        self.assertNotEqual(suppliers["Ручка скоба 128"], "Blum")


if __name__ == "__main__":
    unittest.main()
