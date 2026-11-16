from __future__ import annotations

from datetime import date
from io import BytesIO
import sys
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import swiss_krono_order as krono  # noqa: E402


def row(decor: str, structure: str, name: str, pallets: float, *, group: int = 4, in_catalog: bool = True, thickness: int = 16) -> dict:
    return {
        **{column: "" for column in krono.ORDER_ROW_COLUMNS},
        "row_id": f"{decor}/{structure}/{thickness}/2750",
        "label": f"D/{decor} {structure}",
        "name": name,
        "decor": decor,
        "structure": structure,
        "thickness": thickness,
        "length": 2750,
        "pack": krono._default_pack(thickness, 2750),
        "price_group": group,
        "pallets": pallets,
        "needed_sheets": 0.0,
        "product": f"D/{decor} {structure}/{name} 2.75*1.83",
        "in_catalog": in_catalog,
        "low_priority_skipped": False,
    }


ROWS = pd.DataFrame(
    [
        row("20267", "OW", "Аделия", 24),
        row("K110", "SM", "Белый корпус", 111, group=1),
        row("K101", "SE", "Белый гладкий", 0, group=2),
        row("6933", "VL", "Кашемир", 0),
        row("U171", "VL", "Серый темный", 0, group=3),
        row("4446", "VL", "Нежно-розовый", 0, group=7),
        row("3274", "BS", "Бетон", 0, in_catalog=False),
    ],
    columns=krono.ORDER_ROW_COLUMNS,
)


def user_list(*lines: tuple) -> bytes:
    """The user's list as sent: the date over the decor column, the name merged over two columns."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.merge_cells("C1:D1")
    sheet.append(["09.10.2026", "группа", "ЛДСП 2750х1830", None, "к заказу "])
    for values in lines:
        sheet.append(list(values))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class SheetListWorkbookTest(unittest.TestCase):
    def test_the_users_columns_in_sheets(self) -> None:
        content = krono.build_sheet_list_workbook(ROWS, order_date=date(2026, 11, 16))
        sheet = load_workbook(BytesIO(content)).active
        self.assertEqual([sheet[f"{column}1"].value for column in "ABCE"], ["16.11.2026", "группа", "ЛДСП 2750х1830", "к заказу"])
        lines = {values[0]: values for values in sheet.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(lines["20267 OW"], ("20267 OW", 4, "Аделия 2.75*1.83", None, 792))
        self.assertEqual(lines["K110 SM"][4], 3663)
        # Every board of the supplier is listed, with nothing to order too, as in the user's list.
        self.assertEqual(lines["K101 SE"][4], 0)
        self.assertEqual(len(lines), len(ROWS))

    def test_the_downloaded_list_comes_back(self) -> None:
        parsed = krono.parse_order_file(krono.build_sheet_list_workbook(ROWS), ROWS)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.pallets, {"20267/OW/16/2750": 24.0, "K110/SM/16/2750": 111.0})


class SheetListParseTest(unittest.TestCase):
    def test_the_users_own_list_is_read(self) -> None:
        content = user_list(
            ("20267 OW", 4, "Аделия  2.75*1.83", None, 792),
            ("101 SE", 2, "Белый гладкий 2.75*1.83", None, 330),
            ("K110 SM", 1, "Белый корпус гладкий 2.75*1.83", None, 3663),
            ("U6933 VL", 4, "Кашемир  2.75*1.83", None, 627),
            ("171 VL", 3, "Серый темный 2.75*1.83                    ", None, 627),
            # Two spaces, and a Cyrillic "МХ" or "РЕ" elsewhere in the list.
            ("4446  VL", 7, "Нежно-розовый 2.75*1.83", None, 132),
            ("4433 MX", 4, "Вяз Белый 2.75*1.83", None, 0),
        )
        parsed = krono.parse_order_file(content, ROWS)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.unknown, [])
        self.assertEqual(
            parsed.pallets,
            {
                "20267/OW/16/2750": 24.0,
                "K101/SE/16/2750": 10.0,
                "K110/SM/16/2750": 111.0,
                "6933/VL/16/2750": 19.0,
                "U171/VL/16/2750": 19.0,
                "4446/VL/16/2750": 4.0,
            },
        )
        applied = krono.apply_order_file(ROWS, parsed)
        items = krono.order_items_from_rows(applied).set_index("product")["quantity"].to_dict()
        self.assertEqual(items["D/20267 OW/Аделия 2.75*1.83"], 792.0)

    def test_decors_the_factory_does_not_list_are_named(self) -> None:
        parsed = krono.parse_order_file(user_list(("3274 BS", 4, "Бетон 2.75*1.83", None, 99), ("9999 VL", 4, "Новый", None, 33)), ROWS)
        self.assertEqual(parsed.pallets, {})
        self.assertEqual(parsed.unknown, ["3274 BS", "9999 VL"])

    def test_a_word_for_a_quantity_is_an_error(self) -> None:
        parsed = krono.parse_order_file(user_list(("20267 OW", 4, "Аделия 2.75*1.83", None, "много")), ROWS)
        self.assertTrue(parsed.errors)

    def test_an_unrelated_file_is_refused(self) -> None:
        workbook = Workbook()
        workbook.active.append(["Номенклатура", "Количество"])
        buffer = BytesIO()
        workbook.save(buffer)
        self.assertTrue(krono.parse_order_file(buffer.getvalue(), ROWS).errors)


if __name__ == "__main__":
    unittest.main()
