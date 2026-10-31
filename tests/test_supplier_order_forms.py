from __future__ import annotations

from io import BytesIO
import sys
import unittest
from pathlib import Path

from openpyxl import load_workbook
import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from supplier_order_forms import (  # noqa: E402
    WQ_ORDER_ROWS,
    build_order_form_rows,
    build_wq_order_workbook,
)


def forecast(*rows: tuple[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "product": product,
                "recommended_order_qty": quantity,
                "abc_class": "A",
                "xyz_class": "X",
                "priority": "Высокий",
                "stock_status": "Нужно пополнение",
                "available_stock_qty": 3.0,
                "forecast_qty": 40.0,
            }
            for product, quantity in rows
        ]
    )


class OrderFormRowsTest(unittest.TestCase):
    def row(self, frame: pd.DataFrame, name: str) -> pd.Series:
        return frame[frame["name"] == name].iloc[0]

    def test_board_and_edge_of_one_decor_go_to_their_columns(self) -> None:
        result = build_order_form_rows(
            forecast(
                ("US77301/PGR4201 Маффин 18*1220*2800мм", 219.4),
                ("PGR4201 Маффин 1*22мм", 1500),
            )
        )
        muffin = self.row(result, "Маффин")
        self.assertEqual(muffin["quantity"], 220)
        self.assertEqual(muffin["edge_or_sheets"], 1500)
        self.assertEqual(muffin["abc_class"], "A")
        self.assertEqual(muffin["code"], "WQ63893M")

    def test_not_needed_position_stays_empty(self) -> None:
        result = build_order_form_rows(forecast(("US77306/PGR4206 Бисквит 18*1220*2800мм", 0)))
        biscuit = self.row(result, "Бисквит")
        self.assertEqual(biscuit["quantity"], 0)
        self.assertTrue(biscuit["found"])
        self.assertFalse(self.row(result, "Орео")["found"])

    def test_oreo_and_tiramisu_use_the_site_article(self) -> None:
        result = build_order_form_rows(
            forecast(
                ("US77304/PGR4204 Орео 18*1220*2800мм", 20),
                ("US77305/PGR4205 Тирамису 18*1220*2800мм", 40),
            )
        )
        self.assertEqual(self.row(result, "Орео")["code"], "US77304/PGR4204")
        self.assertEqual(self.row(result, "Орео")["quantity"], 20)
        self.assertEqual(self.row(result, "Тирамису")["code"], "US77305/PGR4205")
        self.assertEqual(self.row(result, "Тирамису")["quantity"], 40)

    def test_new_position_is_found_by_name_only_with_the_board_size(self) -> None:
        result = build_order_form_rows(
            forecast(
                ("Панель Титан 18*1220*2800мм", 20),
                ("Титан крепёж для панели", 500),
            )
        )
        self.assertEqual(self.row(result, "Титан")["quantity"], 20)

    def test_hpl_sheets_go_to_the_sheets_column_and_stay_out_of_the_boards_total(self) -> None:
        result = build_order_form_rows(
            forecast(
                ("HPL Compact WQS31008-MT Изысканный Камень", 5),
                ("HPL Compact белый 3050", 9),
            )
        )
        stone = self.row(result, "Изысканный Камень")
        self.assertEqual(stone["edge_or_sheets"], 5)
        self.assertEqual(stone["quantity"], 0)
        self.assertEqual(self.row(result, "белый")["edge_or_sheets"], 0)
        self.assertEqual(result["quantity"].sum(), 0)

    def test_empty_forecast_keeps_the_whole_form(self) -> None:
        result = build_order_form_rows(pd.DataFrame())
        self.assertEqual(len(result), len(WQ_ORDER_ROWS))
        self.assertEqual(result["quantity"].sum(), 0)


class OrderWorkbookTest(unittest.TestCase):
    def setUp(self) -> None:
        rows = build_order_form_rows(
            forecast(
                ("US77301/PGR4201 Маффин 18*1220*2800мм", 220),
                ("US77311/PGR4211 Чизкейк 18*1220*2800мм", 80),
                ("PGR4201 Маффин 1*22мм", 1500),
                ("HPL Compact WQS1837-3 FR Молочный Кварцит", 10),
            )
        )
        content = build_wq_order_workbook(rows)
        self.sheet = load_workbook(BytesIO(content)).active
        self.values = load_workbook(BytesIO(content), data_only=True).active

    def find_row(self, name: str) -> int:
        for row in range(5, self.sheet.max_row + 1):
            if self.sheet.cell(row, 6).value == name:
                return row
        self.fail(f"{name} is not in the form")

    def test_layout_matches_the_supplier_form(self) -> None:
        self.assertEqual(self.sheet["C3"].value, "2800*1220*18мм")
        self.assertEqual(
            [self.sheet.cell(4, column).value for column in range(2, 7)],
            ["Код", "кол-во", "код старый", "кромка", "Название"],
        )

    def test_quantities_are_written_and_empty_rows_stay_blank(self) -> None:
        muffin = self.find_row("Маффин")
        self.assertEqual(self.sheet.cell(muffin, 3).value, 220)
        self.assertEqual(self.sheet.cell(muffin, 5).value, 1500)
        self.assertIsNone(self.sheet.cell(self.find_row("Брауни"), 3).value)
        self.assertEqual(self.sheet.cell(self.find_row("Молочный Кварцит"), 5).value, 10)
        self.assertIsNone(self.sheet.cell(self.find_row("Молочный Кварцит"), 3).value)

    def test_only_the_boards_total_is_kept(self) -> None:
        total_row = self.sheet.max_row
        self.assertEqual(self.sheet.cell(total_row, 3).value, f"=SUM(C5:C{total_row - 1})")
        self.assertEqual(self.values.cell(total_row, 3).value, 300)
        for column in (2, 4, 5, 6):
            self.assertIsNone(self.sheet.cell(total_row, column).value)


if __name__ == "__main__":
    unittest.main()
