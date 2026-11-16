from __future__ import annotations

from io import BytesIO
import sys
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from extravert_order import parse_site_name  # noqa: E402
from supplier_order_forms import (  # noqa: E402
    EXTRAVERT_FORM,
    NOTE_NEW_POSITION,
    apply_order_file,
    build_order_form_rows,
    build_supplier_order_workbook,
    order_form_from_items,
    order_items_from_form,
    parse_order_form_file,
    supplier_form_for,
)

COCOA = "ЛДСП D.302.W04 Дуб Уральский какао E1 2800X2070X16 STD"
COCOA_EDGE = "Кромка ABS D.302.W04 Дуб Уральский какао 19*0,8"
CASHMERE = "ЛДСП M.326.S01 Бежевый кашемировый E1 2800X2070X16 STD"
CASHMERE_T01 = "ЛДСП M.326.T01 Бежевый кашемировый E1 2800X2070X16 STD"
CASHMERE_TSP = "ТСП Motus M.326.F05 Бежевый кашемировый E1 V100 2800X2070X18 STD"
SAVIN_EDGE = "Кромка ABS D.516.W04 Дуб Савинский шоколадный 19*0,8"


def forecast(*lines: tuple[str, float], supplier: str = "Экстраверт") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product": [product for product, _ in lines],
            "recommended_order_qty": [quantity for _, quantity in lines],
            "supplier": supplier,
            "priority": "Высокий",
        }
    )


def rows(*lines: tuple[str, float]) -> pd.DataFrame:
    return build_order_form_rows(forecast(*lines), form=EXTRAVERT_FORM)


def line(frame: pd.DataFrame, code: str, structure: str) -> pd.Series:
    return frame[(frame["code"] == code) & frame["name"].str.startswith(structure)].iloc[0]


def supplier_file(board_lines: list[tuple], tsp_lines: list[tuple]) -> bytes:
    """The supplier's price list as the user fills it: a contact line, a title, two tables."""
    workbook = Workbook()
    sheet = workbook.active
    sheet["B1"] = "Контакты: …"
    sheet["A2"] = "Прайс ЛДСП Экстраверт 2800х2070х16"
    sheet.append(["Код", "Номенклатура", "Формат", "Кол-во", "кромка "])
    for values in board_lines:
        sheet.append(list(values))
    sheet.append([])
    sheet.append([None, "ТСС  ПЛИТА   ЛДСП   2800Х2070Х18ММ "])
    sheet.append(["КОД", "НАИМЕНОВАНИЕ ", "ФОРМАТ ", "КОЛ-ВО "])
    for values in tsp_lines:
        sheet.append(list(values))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class SiteNameTest(unittest.TestCase):
    def test_board_edge_and_tsp_are_read(self) -> None:
        self.assertEqual(
            parse_site_name(COCOA),
            {"code": "302", "structure": "W04", "kind": "board", "table": "board", "name": "W04 Дуб Уральский какао E1", "format": "2800х2070х16"},
        )
        edge = parse_site_name(COCOA_EDGE)
        self.assertEqual((edge["code"], edge["structure"], edge["kind"]), ("302", "W04", "edge"))
        tsp = parse_site_name(CASHMERE_TSP)
        self.assertEqual((tsp["table"], tsp["name"], tsp["format"]), ("tsp", "ТСП Motus F05 Бежевый кашемировый E1", "2800х2070х18"))
        self.assertIsNone(parse_site_name("Петля Blum"))

    def test_the_supplier_has_its_own_form(self) -> None:
        self.assertIs(supplier_form_for("Экстраверт"), EXTRAVERT_FORM)


class ExtravertRowsTest(unittest.TestCase):
    def test_edge_band_shares_the_line_of_its_board(self) -> None:
        frame = rows((COCOA_EDGE, 1000), (COCOA, 72), (CASHMERE, 26), (CASHMERE_T01, 0), (CASHMERE_TSP, 5), (SAVIN_EDGE, 500))
        cocoa = line(frame, "302", "W04")
        self.assertEqual((cocoa["quantity"], cocoa["edge_or_sheets"], cocoa["product"], cocoa["edge_product"]), (72, 1000, COCOA, COCOA_EDGE))
        # One code, three decors: S01 and T01 boards and the ТСП.
        self.assertEqual(len(frame[frame["code"] == "326"]), 3)
        self.assertEqual(line(frame, "326", "ТСП")["group"], "tsp")
        # An edge band without its board still has a line of its own.
        savin = line(frame, "516", "W04")
        self.assertEqual((savin["quantity"], savin["edge_or_sheets"]), (0, 500))

    def test_goods_of_another_supplier_stay_out(self) -> None:
        data = pd.concat([forecast((COCOA, 26)), forecast(("US77301/PGR4201 Маффин 18*1220*2800мм", 5), supplier="WQ")])
        self.assertEqual(build_order_form_rows(data, form=EXTRAVERT_FORM)["code"].tolist(), ["302"])


class ExtravertWorkbookTest(unittest.TestCase):
    def test_two_tables_in_the_suppliers_columns(self) -> None:
        frame = rows((COCOA, 72), (COCOA_EDGE, 1000), (CASHMERE_TSP, 5), (CASHMERE, 0))
        sheet = load_workbook(BytesIO(build_supplier_order_workbook(frame, form=EXTRAVERT_FORM))).active
        values = [[cell.value for cell in row] for row in sheet.iter_rows()]
        self.assertIn(["Код", "Номенклатура", "Формат", "Кол-во", "кромка"], values)
        self.assertIn([302, "W04 Дуб Уральский какао E1", "2800х2070х16", 72, 1000], values)
        self.assertIn([326, "ТСП Motus F05 Бежевый кашемировый E1", "2800х2070х18", 5, None], values)
        # Nothing to order for the S01 board: it is not in the file.
        self.assertEqual(sum(1 for row in values if row[0] == 326), 1)

    def test_the_generated_file_comes_back(self) -> None:
        frame = rows((COCOA, 72), (COCOA_EDGE, 1000), (CASHMERE_TSP, 5))
        content = build_supplier_order_workbook(frame, form=EXTRAVERT_FORM)
        parsed = parse_order_form_file(content, frame, form=EXTRAVERT_FORM)
        self.assertEqual((parsed.errors, parsed.extra_rows), ([], []))
        items = order_items_from_form(apply_order_file(frame, parsed)).set_index("product")["quantity"].to_dict()
        self.assertEqual(items, {COCOA: 72.0, COCOA_EDGE: 1000.0, CASHMERE_TSP: 5.0})


class SupplierFileTest(unittest.TestCase):
    def test_the_suppliers_filled_price_list_is_read(self) -> None:
        frame = rows((COCOA, 10), (COCOA_EDGE, 10), (CASHMERE, 10), (CASHMERE_T01, 10), (CASHMERE_TSP, 1), (SAVIN_EDGE, 10))
        content = supplier_file(
            [
                (300, "W01 Орех Ставропольский ванильный Е1 ", "2800х2070х16", None, None),
                (302, "W04 Дуб Уральский какао E1 ", "2800х2070х16", 72, 1000),
                (516, "W04 Дуб Савинский шоколадный E1 ", "2800X2070X16", None, 500),
                (326, "S01 Бежевый кашемировый Е1 ", "2800х2070х16", 26, 1000),
                (326, "Т01 Бежевый кашемировый Е1 ", "2800х2070х16", None, None),
                # The supplier's own typo: a Cyrillic "О" for the zero.
                (346, "МО1 Золото Амурское белое Е1", "2800х2070х16", None, 300),
            ],
            # No "кромка" title over the ТСП table, yet its edge band is in that column.
            [(326, "ТСП Motus F05 Бежевый кашемировый E1 ", "2800Х2070Х18", 5, 500)],
        )
        parsed = parse_order_form_file(content, frame, [COCOA, CASHMERE], form=EXTRAVERT_FORM)
        self.assertEqual(parsed.errors, [])
        applied = apply_order_file(frame, parsed)
        self.assertEqual((line(applied, "302", "W04")["quantity"], line(applied, "302", "W04")["edge_or_sheets"]), (72, 1000))
        self.assertEqual(line(applied, "326", "S01")["quantity"], 26)
        self.assertEqual(line(applied, "326", "T01")["quantity"], 0)
        tsp = line(applied, "326", "ТСП")
        self.assertEqual((tsp["quantity"], tsp["edge_or_sheets"]), (5, 500))
        self.assertEqual(line(applied, "516", "W04")["edge_or_sheets"], 500)
        new = line(applied, "346", "МО1")
        self.assertEqual((new["edge_or_sheets"], new["file_note"]), (300, NOTE_NEW_POSITION))
        items = order_items_from_form(applied).set_index("product")["quantity"].to_dict()
        self.assertEqual(items["Кромка 346 МО1 Золото Амурское белое Е1"], 300.0)

    def test_the_sites_own_export_still_comes_back(self) -> None:
        frame = rows((COCOA, 10), (COCOA_EDGE, 10))
        workbook = Workbook()
        workbook.active.append(["SKU / Товар", "Количество", "Принято"])
        workbook.active.append([COCOA, 26, 0])
        buffer = BytesIO()
        workbook.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), frame, form=EXTRAVERT_FORM)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(line(apply_order_file(frame, parsed), "302", "W04")["quantity"], 26)

    def test_saved_order_rebuilds_the_lines(self) -> None:
        frame = rows((COCOA, 72), (COCOA_EDGE, 1000))
        rebuilt = order_form_from_items(order_items_from_form(frame), form=EXTRAVERT_FORM)
        cocoa = line(rebuilt, "302", "W04")
        self.assertEqual((cocoa["quantity"], cocoa["edge_or_sheets"]), (72, 1000))


if __name__ == "__main__":
    unittest.main()
