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

from supplier_order_forms import (  # noqa: E402
    SECTION_HPL,
    SECTION_OTHER,
    apply_order_file,
    build_order_form_rows,
    build_wq_order_workbook,
    is_wq_supplier,
    order_form_from_items,
    order_form_supplier,
    order_items_from_form,
    parse_order_form_file,
)

NAME_COLUMN = 3
CODE_COLUMN = 2
BOARD_COLUMN = 5
EDGE_COLUMN = 6

MUFFIN = "US77301/PGR4201 Маффин 18*1220*2800мм"
MUFFIN_EDGE = "PGR4201 Маффин 1*22мм"
CHEESECAKE = "US77311/PGR4211 Чизкейк 18*1220*2800мм"
BISCUIT = "US77306/PGR4206 Бисквит 18*1220*2800мм"
QUARTZITE = "HPL Compact WQS1837-3 FR Молочный Кварцит"


def forecast(*rows: tuple, supplier: str = "WQ") -> pd.DataFrame:
    records = []
    for row in rows:
        product, quantity = row[0], row[1]
        records.append(
            {
                "product": product,
                "recommended_order_qty": quantity,
                "supplier": row[3] if len(row) > 3 else supplier,
                "abc_class": "A",
                "xyz_class": "X",
                "priority": row[2] if len(row) > 2 else "Высокий",
                "stock_status": "Нужно пополнение",
                "available_stock_qty": 3.0,
                "forecast_qty": 40.0,
            }
        )
    return pd.DataFrame(records)


def row_named(frame: pd.DataFrame, name: str) -> pd.Series:
    return frame[frame["name"] == name].iloc[0]


def find_row(sheet, name: str) -> int:
    for row in range(1, sheet.max_row + 1):
        if sheet.cell(row, NAME_COLUMN).value == name:
            return row
    raise AssertionError(f"{name} is not in the form")


class SupplierSelectionTest(unittest.TestCase):
    def test_supplier_name_is_recognised(self) -> None:
        for name in ("WQ", "wq", "ООО WQ", "WQ Китай"):
            self.assertTrue(is_wq_supplier(name), name)
        for name in ("", "Не назначен", "AWQA", "Кроношпан"):
            self.assertFalse(is_wq_supplier(name), name)

    def test_only_goods_of_this_supplier_are_taken(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 20), (BISCUIT, 30, "Высокий", "Другой поставщик")))
        self.assertEqual(rows["name"].tolist(), ["Маффин"])

    def test_no_goods_of_this_supplier_gives_an_empty_form(self) -> None:
        self.assertTrue(build_order_form_rows(forecast((MUFFIN, 20), supplier="Другой")).empty)
        self.assertTrue(build_order_form_rows(pd.DataFrame()).empty)

    def test_examples_from_the_old_template_are_not_added(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 20)))
        self.assertEqual(len(rows), 1)


class OrderFormRowsTest(unittest.TestCase):
    def test_board_and_edge_of_one_decor_share_a_line(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 219.4), (MUFFIN_EDGE, 1500)))
        self.assertEqual(len(rows), 1)
        muffin = row_named(rows, "Маффин")
        self.assertEqual(muffin["quantity"], 220)
        self.assertEqual(muffin["edge_or_sheets"], 1500)
        self.assertEqual(muffin["code"], "WQ63893M")
        self.assertEqual(muffin["old_code"], "US77301/PGR4201")
        self.assertEqual(muffin["product"], MUFFIN)
        self.assertEqual(muffin["edge_product"], MUFFIN_EDGE)

    def test_product_unknown_to_the_old_template_is_taken_from_the_site(self) -> None:
        rows = build_order_form_rows(
            forecast(("US88888/PGR9999 Новый декор 18*1220*2800мм", 40), ("PGR9999 Новый декор 1*22мм", 100))
        )
        self.assertEqual(len(rows), 1)
        row = row_named(rows, "Новый декор")
        self.assertEqual(row["code"], "US88888/PGR9999")
        self.assertEqual((row["quantity"], row["edge_or_sheets"]), (40, 100))

    def test_oreo_uses_the_site_article(self) -> None:
        rows = build_order_form_rows(forecast(("US77304/PGR4204 Орео 18*1220*2800мм", 20)))
        self.assertEqual(row_named(rows, "Орео")["code"], "US77304/PGR4204")

    def test_hpl_and_other_goods_get_their_own_sections(self) -> None:
        rows = build_order_form_rows(forecast((QUARTZITE, 5), ("Петля накладная", 12)))
        self.assertEqual(row_named(rows, "Молочный Кварцит")["section"], SECTION_HPL)
        self.assertEqual(row_named(rows, "Молочный Кварцит")["edge_or_sheets"], 5)
        self.assertEqual(row_named(rows, "Петля накладная")["section"], SECTION_OTHER)
        self.assertEqual(row_named(rows, "Петля накладная")["edge_or_sheets"], 12)
        self.assertEqual(rows["quantity"].sum(), 0)

    def test_quantities_are_not_rounded_to_a_batch(self) -> None:
        self.assertEqual(row_named(build_order_form_rows(forecast((BISCUIT, 14))), "Бисквит")["quantity"], 14)


class LowPriorityTest(unittest.TestCase):
    def test_low_priority_is_not_proposed_but_stays_on_the_site_list(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 40, "Низкий"), (MUFFIN_EDGE, 300, "Высокий")))
        muffin = row_named(rows, "Маффин")
        self.assertEqual(muffin["quantity"], 0)
        self.assertEqual(muffin["edge_or_sheets"], 300)
        self.assertTrue(muffin["low_priority_skipped"])
        self.assertEqual(muffin["product"], MUFFIN)

    def test_low_priority_edge_and_hpl_are_not_proposed(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 40), (MUFFIN_EDGE, 300, "Низкий"), (QUARTZITE, 6, "низкий ")))
        self.assertEqual(row_named(rows, "Маффин")["quantity"], 40)
        self.assertEqual(row_named(rows, "Маффин")["edge_or_sheets"], 0)
        self.assertEqual(row_named(rows, "Молочный Кварцит")["edge_or_sheets"], 0)

    def test_other_priorities_are_proposed(self) -> None:
        for priority in ("Критичный", "Высокий", "Средний", "Новый"):
            rows = build_order_form_rows(forecast((BISCUIT, 14, priority)))
            self.assertEqual(row_named(rows, "Бисквит")["quantity"], 14, priority)
            self.assertFalse(row_named(rows, "Бисквит")["low_priority_skipped"])

    def test_nothing_to_order_is_not_reported_as_skipped(self) -> None:
        rows = build_order_form_rows(forecast((BISCUIT, 0, "Низкий")))
        self.assertFalse(row_named(rows, "Бисквит")["low_priority_skipped"])


class OrderFromFormTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = build_order_form_rows(forecast((MUFFIN, 220), (MUFFIN_EDGE, 1500), (QUARTZITE, 10), (BISCUIT, 0)))

    def test_order_lines_keep_the_site_products_and_skip_empty_rows(self) -> None:
        items = order_items_from_form(self.rows).set_index("product")["quantity"]
        self.assertEqual(items.to_dict(), {MUFFIN: 220.0, MUFFIN_EDGE: 1500.0, QUARTZITE: 10.0})

    def test_quantities_changed_in_the_form_reach_the_order(self) -> None:
        edited = self.rows.copy()
        edited.loc[edited["name"] == "Маффин", "quantity"] = 240
        self.assertEqual(order_items_from_form(edited).set_index("product")["quantity"][MUFFIN], 240.0)

    def test_saved_order_rebuilds_its_own_positions(self) -> None:
        rebuilt = order_form_from_items(order_items_from_form(self.rows))
        self.assertEqual(sorted(rebuilt["name"]), ["Маффин", "Молочный Кварцит"])
        self.assertEqual(row_named(rebuilt, "Маффин")["quantity"], 220)
        self.assertEqual(row_named(rebuilt, "Маффин")["edge_or_sheets"], 1500)
        self.assertEqual(row_named(rebuilt, "Молочный Кварцит")["edge_or_sheets"], 10)

    def test_order_supplier_comes_from_the_site_or_falls_back(self) -> None:
        self.assertEqual(order_form_supplier(self.rows), "WQ")
        self.assertEqual(order_form_supplier(self.rows.assign(supplier="ООО WQ")), "ООО WQ")
        self.assertEqual(order_form_supplier(self.rows.assign(supplier="")), "WQ")


class UploadedOrderFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = build_order_form_rows(
            forecast((MUFFIN, 220), (MUFFIN_EDGE, 1500), (CHEESECAKE, 80), (QUARTZITE, 10), (BISCUIT, 0))
        )
        self.workbook = load_workbook(BytesIO(build_wq_order_workbook(self.rows)))
        self.sheet = self.workbook.active

    def content(self) -> bytes:
        buffer = BytesIO()
        self.workbook.save(buffer)
        return buffer.getvalue()

    def parsed(self):
        return parse_order_form_file(self.content(), self.rows)

    def test_unchanged_file_gives_back_the_same_quantities(self) -> None:
        parsed = self.parsed()
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.unknown_codes, [])
        applied = apply_order_file(self.rows, parsed)
        self.assertEqual(applied["quantity"].tolist(), self.rows["quantity"].tolist())
        self.assertEqual(applied["edge_or_sheets"].tolist(), self.rows["edge_or_sheets"].tolist())

    def test_changed_and_cleared_quantities_become_final(self) -> None:
        self.sheet.cell(find_row(self.sheet, "Маффин"), BOARD_COLUMN).value = 200
        self.sheet.cell(find_row(self.sheet, "Чизкейк"), BOARD_COLUMN).value = None
        items = order_items_from_form(apply_order_file(self.rows, self.parsed())).set_index("product")["quantity"]
        self.assertEqual(items[MUFFIN], 200.0)
        self.assertNotIn(CHEESECAKE, items.index)

    def test_position_added_to_the_file_by_hand_is_taken_if_it_is_on_the_site(self) -> None:
        extra = self.sheet.max_row + 1
        self.sheet.cell(extra, CODE_COLUMN).value = "WQ63819M"
        self.sheet.cell(extra, BOARD_COLUMN).value = 20
        applied = apply_order_file(self.rows, parse_order_form_file(self.content(), self.rows))
        self.assertEqual(row_named(applied, "Бисквит")["quantity"], 20)

    def test_position_that_stopped_being_needed_is_still_recognised(self) -> None:
        later = build_order_form_rows(forecast((MUFFIN, 0), (MUFFIN_EDGE, 0), (CHEESECAKE, 80), (QUARTZITE, 10)))
        applied = apply_order_file(later, parse_order_form_file(self.content(), later))
        self.assertEqual(row_named(applied, "Маффин")["quantity"], 220)

    def test_code_is_matched_despite_spaces(self) -> None:
        self.sheet.cell(find_row(self.sheet, "Маффин"), CODE_COLUMN).value = " WQ63893M "
        self.assertEqual(row_named(apply_order_file(self.rows, self.parsed()), "Маффин")["quantity"], 220)

    def test_unknown_code_and_bad_number_are_reported(self) -> None:
        extra = self.sheet.max_row + 1
        self.sheet.cell(extra, CODE_COLUMN).value = "WQ999"
        self.sheet.cell(extra, BOARD_COLUMN).value = 40
        self.sheet.cell(find_row(self.sheet, "Маффин"), BOARD_COLUMN).value = "нет"
        parsed = self.parsed()
        self.assertEqual(parsed.unknown_codes, ["WQ999"])
        self.assertEqual(len(parsed.errors), 1)
        self.assertIn("WQ63893M", parsed.errors[0])

    def test_position_without_an_article_is_matched_by_name(self) -> None:
        rows = build_order_form_rows(forecast(("Петля накладная", 12)))
        content = build_wq_order_workbook(rows)
        applied = apply_order_file(rows, parse_order_form_file(content, rows))
        self.assertEqual(row_named(applied, "Петля накладная")["edge_or_sheets"], 12)

    def test_not_a_form_is_rejected(self) -> None:
        self.assertTrue(parse_order_form_file(b"not an excel file", self.rows).errors)
        other = Workbook()
        other.active.append(["Номенклатура", "Количество"])
        other.active.append(["Плита", 5])
        buffer = BytesIO()
        other.save(buffer)
        self.assertTrue(parse_order_form_file(buffer.getvalue(), self.rows).errors)

    def test_file_saved_from_the_earlier_layout_is_still_accepted(self) -> None:
        old = Workbook()
        sheet = old.active
        sheet["C3"] = "2800*1220*18мм"
        for column, title in enumerate(("Код", "кол-во", "код старый", "кромка", "Название"), start=2):
            sheet.cell(4, column, title)
        sheet.append([None, "WQ63893M ", 220, "US77301/PGR4201", 1500, "Маффин"])
        sheet.append([None, "HPL", None, None, "листов", None])
        sheet.append([None, " WQS1837-3 FR", None, None, 10, "Молочный Кварцит"])
        sheet.append([None, None, "=SUM(C5:C7)"])
        buffer = BytesIO()
        old.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), self.rows)
        self.assertEqual(parsed.errors, [])
        applied = apply_order_file(self.rows, parsed)
        self.assertEqual(row_named(applied, "Маффин")["quantity"], 220)
        self.assertEqual(row_named(applied, "Маффин")["edge_or_sheets"], 1500)
        self.assertEqual(row_named(applied, "Молочный Кварцит")["edge_or_sheets"], 10)

    def test_fingerprint_identifies_the_same_file(self) -> None:
        content = self.content()
        self.assertEqual(
            parse_order_form_file(content, self.rows).fingerprint,
            parse_order_form_file(content, self.rows).fingerprint,
        )
        self.sheet.cell(find_row(self.sheet, "Маффин"), BOARD_COLUMN).value = 1
        self.assertNotEqual(parse_order_form_file(content, self.rows).fingerprint, self.parsed().fingerprint)


class OrderWorkbookTest(unittest.TestCase):
    def setUp(self) -> None:
        rows = build_order_form_rows(
            forecast(
                (MUFFIN, 220),
                (CHEESECAKE, 80),
                (MUFFIN_EDGE, 1500),
                (BISCUIT, 0),
                (QUARTZITE, 10),
                ("Петля накладная", 12),
            )
        )
        content = build_wq_order_workbook(rows, order_date=date(2026, 10, 31))
        self.sheet = load_workbook(BytesIO(content)).active
        self.values = load_workbook(BytesIO(content), data_only=True).active

    def label_row(self, label: str) -> int:
        for row in range(1, self.sheet.max_row + 1):
            if self.sheet.cell(row, 1).value == label:
                return row
        self.fail(f"{label} is not in the form")

    def names(self) -> list[object]:
        return [self.sheet.cell(row, NAME_COLUMN).value for row in range(1, self.sheet.max_row + 1)]

    def test_heading_and_separate_tables(self) -> None:
        self.assertEqual(self.sheet["A1"].value, "Заказ поставщику WQ")
        self.assertIn("31.10.2026", self.sheet["A2"].value)
        self.assertIn("2800*1220*18мм", self.sheet["A4"].value)
        self.assertEqual(
            [self.sheet.cell(5, column).value for column in range(1, 7)],
            ["№", "Код", "Название", "Старый код", "Плиты, шт", "Кромка, м"],
        )
        hpl_title = self.label_row("HPL")
        self.assertEqual(self.sheet.cell(hpl_title + 1, BOARD_COLUMN).value, "HPL, листов")
        self.assertGreater(hpl_title, self.label_row("Итого плит, шт"))
        self.assertGreater(self.label_row("Другие позиции"), hpl_title)

    def test_only_needed_positions_are_written(self) -> None:
        names = self.names()
        for name in ("Маффин", "Чизкейк", "Молочный Кварцит", "Петля накладная"):
            self.assertIn(name, names)
        self.assertNotIn("Бисквит", names)
        self.assertNotIn("Брауни", names)

    def test_numbering_is_continuous(self) -> None:
        first = find_row(self.sheet, "Маффин")
        numbers = [self.sheet.cell(row, 1).value for row in (first, first + 1)]
        self.assertEqual(sorted(numbers), [1, 2])

    def test_quantities_are_in_their_columns(self) -> None:
        muffin = find_row(self.sheet, "Маффин")
        self.assertEqual(self.sheet.cell(muffin, CODE_COLUMN).value, "WQ63893M")
        self.assertEqual(self.sheet.cell(muffin, BOARD_COLUMN).value, 220)
        self.assertEqual(self.sheet.cell(muffin, EDGE_COLUMN).value, 1500)
        self.assertEqual(self.sheet.cell(find_row(self.sheet, "Молочный Кварцит"), BOARD_COLUMN).value, 10)

    def test_only_the_boards_total_is_kept(self) -> None:
        total_row = self.label_row("Итого плит, шт")
        self.assertEqual(self.sheet.cell(total_row, BOARD_COLUMN).value, f"=SUM(E6:E{total_row - 1})")
        self.assertEqual(self.values.cell(total_row, BOARD_COLUMN).value, 300)
        self.assertIsNone(self.sheet.cell(total_row, EDGE_COLUMN).value)

    def test_file_without_boards_has_no_boards_table(self) -> None:
        rows = build_order_form_rows(forecast((QUARTZITE, 10)))
        sheet = load_workbook(BytesIO(build_wq_order_workbook(rows))).active
        values = [sheet.cell(row, 1).value for row in range(1, sheet.max_row + 1)]
        self.assertNotIn("Итого плит, шт", values)
        self.assertIn("HPL", values)


if __name__ == "__main__":
    unittest.main()
