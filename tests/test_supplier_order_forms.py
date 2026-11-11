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
    HETTICH_FORM,
    LIAMAX_FORM,
    NUOMI_FORM,
    SECTION_HPL,
    SECTION_OTHER,
    SUPPLIER_FORMS,
    WQ_FORM,
    apply_order_file,
    build_order_form_rows,
    build_supplier_order_workbook,
    generic_supplier_form,
    generic_supplier_forms,
    order_file_comment,
    order_form_from_items,
    order_form_supplier,
    order_items_from_form,
    parse_order_form_file,
    supplier_form_for,
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


from supplier_order_forms import NOTE_NEW_POSITION, NOTE_OTHER_SUPPLIER  # noqa: E402


class SupplierSelectionTest(unittest.TestCase):
    def test_supplier_name_is_recognised(self) -> None:
        for name in ("WQ", "wq", "ООО WQ", "WQ Китай"):
            self.assertTrue(WQ_FORM.matches(name), name)
        for name in ("", "Не назначен", "AWQA", "Кроношпан"):
            self.assertFalse(WQ_FORM.matches(name), name)

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
        self.workbook = load_workbook(BytesIO(build_supplier_order_workbook(self.rows)))
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
        self.assertEqual(parsed.extra_rows, [])
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

    def test_bad_number_is_reported(self) -> None:
        self.sheet.cell(find_row(self.sheet, "Маффин"), BOARD_COLUMN).value = "нет"
        parsed = self.parsed()
        self.assertEqual(len(parsed.errors), 1)
        self.assertIn("WQ63893M", parsed.errors[0])

    def add_line(self, code, name, quantity) -> None:
        extra = self.sheet.max_row + 1
        self.sheet.cell(extra, CODE_COLUMN).value = code
        self.sheet.cell(extra, CODE_COLUMN + 1).value = name
        self.sheet.cell(extra, BOARD_COLUMN).value = quantity

    def test_new_position_is_not_lost(self) -> None:
        book = Workbook()
        book.active.append(["№", "Код", "Название", "Старый код", "Плиты, шт", "Кромка, м"])
        book.active.append([1, "WQ63893M", "Маффин", "US77301/PGR4201", 220, 1500])
        book.active.append([2, "WQ999", "Новый декор", "NEW", 40, 30])
        buffer = BytesIO()
        book.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), self.rows)
        self.assertEqual(parsed.errors, [])
        applied = apply_order_file(self.rows, parsed)
        self.assertEqual(row_named(applied, "Новый декор")["quantity"], 40)
        self.assertEqual(row_named(applied, "Новый декор")["file_note"], NOTE_NEW_POSITION)
        items = order_items_from_form(applied).set_index("product")["quantity"]
        self.assertEqual(items["WQ999 Новый декор 18*1220*2800мм"], 40.0)
        self.assertEqual(items["WQ999 Новый декор 1*22мм"], 30.0)
        self.assertEqual(items[MUFFIN], 220.0)

    def test_position_of_another_supplier_uses_the_site_product(self) -> None:
        self.add_line("WQ63833M", "Моти", 40)
        site = ("Моти 18*1220*2800мм", "Петля Моти", MUFFIN)
        parsed = parse_order_form_file(self.content(), self.rows, site)
        applied = apply_order_file(self.rows, parsed)
        self.assertEqual(row_named(applied, "Моти")["file_note"], NOTE_OTHER_SUPPLIER)
        items = order_items_from_form(applied).set_index("product")["quantity"]
        self.assertEqual(items["Моти 18*1220*2800мм"], 40.0)

    def test_name_alone_does_not_pull_in_goods_of_another_kind(self) -> None:
        self.add_line("WQ777", "Титан", 20)
        parsed = parse_order_form_file(self.content(), self.rows, ("Петля Титан", "Ручка Титан 128мм"))
        self.assertEqual(row_named(apply_order_file(self.rows, parsed), "Титан")["file_note"], NOTE_NEW_POSITION)

    def test_question_mark_instead_of_a_code_falls_back_to_the_name(self) -> None:
        self.add_line("?", "Бисквит", 15)
        parsed = self.parsed()
        self.assertEqual(parsed.extra_rows, [])
        self.assertEqual(row_named(apply_order_file(self.rows, parsed), "Бисквит")["quantity"], 15)

    def test_hpl_line_outside_the_supplier_goes_to_sheets(self) -> None:
        self.add_line("WQS31015-MT", "Мрамор Венато", 5)
        applied = apply_order_file(self.rows, self.parsed())
        line = row_named(applied, "Мрамор Венато")
        self.assertEqual((line["quantity"], line["edge_or_sheets"], line["section"]), (0, 5, "hpl"))

    def test_upload_works_when_the_site_has_no_goods_of_the_supplier(self) -> None:
        empty = build_order_form_rows(pd.DataFrame())
        parsed = parse_order_form_file(self.content(), empty)
        applied = apply_order_file(empty, parsed)
        self.assertEqual(int(applied["quantity"].sum()), 220 + 80)

    def test_position_without_an_article_is_matched_by_name(self) -> None:
        rows = build_order_form_rows(forecast(("Петля накладная", 12)))
        content = build_supplier_order_workbook(rows)
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
        content = build_supplier_order_workbook(rows, order_date=date(2026, 10, 31))
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
        sheet = load_workbook(BytesIO(build_supplier_order_workbook(rows))).active
        values = [sheet.cell(row, 1).value for row in range(1, sheet.max_row + 1)]
        self.assertNotIn("Итого плит, шт", values)
        self.assertIn("HPL", values)


class LiamaxFormTest(unittest.TestCase):
    KANSAS = "WS723-2/LM142 Дуб Канзас 18*1220*2800мм"
    KANSAS_EDGE = "WS723-2/LM142 Дуб Канзас 1*22мм"
    ONYX_GOLD = "V6911-1/LM146 Оникс Голд 18*1220*2800мм"
    ONYX_GREY = "V6911-3/LM146 Оникс Грей 18*1220*2800мм"
    WYOMING = "ES8206 LM557 Вайоминг Грей 18*1220*2800мм"

    def rows(self, *lines: tuple) -> pd.DataFrame:
        return build_order_form_rows(forecast(*lines, supplier="Liamax"), form=LIAMAX_FORM)

    def test_supplier_name_is_recognised(self) -> None:
        for name in ("Liamax", "LIAMAX", "Лиамакс", "ООО Лиамакс"):
            self.assertTrue(LIAMAX_FORM.matches(name), name)
        for name in ("", "WQ", "Не назначен", "Swiss Krono"):
            self.assertFalse(LIAMAX_FORM.matches(name), name)

    def test_only_goods_of_this_supplier(self) -> None:
        data = forecast((self.KANSAS, 5), (MUFFIN, 7, "Высокий", "WQ"), supplier="Liamax")
        self.assertEqual(build_order_form_rows(data, form=LIAMAX_FORM)["code"].tolist(), ["WS723-2/LM142"])
        self.assertEqual(build_order_form_rows(data)["name"].tolist(), ["Маффин"])

    def test_board_and_its_edge_share_a_line(self) -> None:
        rows = self.rows((self.KANSAS_EDGE, 120), (self.KANSAS, 5))
        self.assertEqual(len(rows), 1)
        line = rows.iloc[0]
        self.assertEqual((line["code"], line["name"]), ("WS723-2/LM142", "Дуб Канзас"))
        self.assertEqual((line["quantity"], line["edge_or_sheets"]), (5, 120))

    def test_decors_with_one_edge_code_stay_apart(self) -> None:
        rows = self.rows((self.ONYX_GOLD, 3), (self.ONYX_GREY, 4))
        self.assertEqual(row_named(rows, "Оникс Голд")["quantity"], 3)
        self.assertEqual(row_named(rows, "Оникс Грей")["quantity"], 4)

    def test_edge_named_by_its_own_code_finds_the_board(self) -> None:
        rows = self.rows(("LM142 Дуб Канзас 1*22мм", 50), (self.KANSAS, 5))
        self.assertEqual(rows["code"].tolist(), ["WS723-2/LM142"])
        self.assertEqual(rows["edge_or_sheets"].tolist(), [50])

    def test_code_with_a_space_is_kept_whole(self) -> None:
        line = self.rows((self.WYOMING, 2)).iloc[0]
        self.assertEqual((line["code"], line["name"]), ("ES8206 LM557", "Вайоминг Грей"))

    def test_workbook_is_titled_for_the_supplier(self) -> None:
        rows = self.rows((self.KANSAS, 5), (self.KANSAS_EDGE, 120), (self.ONYX_GOLD, 0))
        sheet = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=LIAMAX_FORM))).active
        self.assertEqual(sheet.title, "Заказ Liamax")
        self.assertEqual(sheet["A1"].value, "Заказ поставщику Liamax")
        self.assertEqual(sheet.cell(5, 4).value, "备注")
        self.assertIsNone(sheet.cell(find_row(sheet, "Дуб Канзас"), 4).value)
        names = [sheet.cell(row, NAME_COLUMN).value for row in range(1, sheet.max_row + 1)]
        self.assertNotIn("Оникс Голд", names)

    def test_generated_file_comes_back_with_the_same_quantities(self) -> None:
        rows = self.rows((self.KANSAS, 5), (self.KANSAS_EDGE, 120), (self.ONYX_GOLD, 3))
        content = build_supplier_order_workbook(rows, form=LIAMAX_FORM)
        applied = apply_order_file(rows, parse_order_form_file(content, rows, form=LIAMAX_FORM))
        self.assertEqual(row_named(applied, "Дуб Канзас")["quantity"], 5)
        self.assertEqual(row_named(applied, "Дуб Канзас")["edge_or_sheets"], 120)
        self.assertEqual(row_named(applied, "Оникс Голд")["quantity"], 3)

    def sample_layout_file(self, *lines: tuple) -> bytes:
        """A file in the layout employees fill by hand, with the column order of the supplier's sample."""
        workbook = Workbook()
        sheet = workbook.active
        sheet["C3"] = "2800*1220*18мм"
        sheet.append([None, "Код", "Кол-во", "备注", "КРОМКА ", "Название"])
        for line in lines:
            sheet.append([None, *line])
        sheet.append([None, None, "=SUM(C5:C38)", None, "=SUM(E5:E38)"])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_file_in_the_sample_layout_is_read(self) -> None:
        rows = self.rows((self.KANSAS, 5), (self.ONYX_GOLD, 3), (self.WYOMING, 1))
        content = self.sample_layout_file(
            ("WS723-2/LM142 ", 8, None, 40, "Дуб Канзас"),
            # The sample writes this code without its slash: the name still finds the goods.
            ("V6911-1LM146", 2, None, None, "Оникс Голд "),
            ("ES8206 LM557 ", 6, None, None, "Вайоминг Грей"),
        )
        parsed = parse_order_form_file(content, rows, form=LIAMAX_FORM)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.extra_rows, [])
        applied = apply_order_file(rows, parsed)
        self.assertEqual(row_named(applied, "Дуб Канзас")["quantity"], 8)
        self.assertEqual(row_named(applied, "Дуб Канзас")["edge_or_sheets"], 40)
        self.assertEqual(row_named(applied, "Оникс Голд")["quantity"], 2)
        self.assertEqual(row_named(applied, "Вайоминг Грей")["quantity"], 6)

    def test_new_decor_in_the_file_enters_the_order(self) -> None:
        rows = self.rows((self.KANSAS, 5))
        content = self.sample_layout_file(("WS984-3/LM529", 4, None, None, "Дуб Теннесси"))
        applied = apply_order_file(rows, parse_order_form_file(content, rows, form=LIAMAX_FORM))
        line = row_named(applied, "Дуб Теннесси")
        self.assertEqual((line["code"], line["quantity"], line["file_note"]), ("WS984-3/LM529", 4, NOTE_NEW_POSITION))
        items = order_items_from_form(applied)
        self.assertIn("WS984-3/LM529 Дуб Теннесси 18*1220*2800мм", items["product"].tolist())

    def test_saved_order_rebuilds_the_same_form(self) -> None:
        rows = self.rows((self.KANSAS, 5), (self.KANSAS_EDGE, 120), (self.ONYX_GOLD, 3), (self.ONYX_GREY, 4))
        rebuilt = order_form_from_items(order_items_from_form(rows), form=LIAMAX_FORM)
        self.assertEqual(sorted(rebuilt["name"]), ["Дуб Канзас", "Оникс Голд", "Оникс Грей"])
        self.assertEqual(row_named(rebuilt, "Дуб Канзас")["edge_or_sheets"], 120)

    def test_comment_names_the_supplier(self) -> None:
        self.assertIn("Liamax", order_file_comment("a.xlsx", parse_order_form_file(b"x", self.rows()), form=LIAMAX_FORM))


BLACK_HANDLE = "HB-128 Ручка-скоба 128мм черная"


class NuomiFormTest(unittest.TestCase):
    BLACK = BLACK_HANDLE
    CHROME = "HB-128 Ручка-скоба 128мм хром"
    KNOB = "Ручка-кнопка золото"

    def rows(self, *lines: tuple) -> pd.DataFrame:
        return build_order_form_rows(forecast(*lines, supplier="Nuomi"), form=NUOMI_FORM)

    def test_supplier_name_is_recognised(self) -> None:
        for name in ("Nuomi", "NUOMI", "Нуоми"):
            self.assertTrue(NUOMI_FORM.matches(name), name)
        self.assertFalse(NUOMI_FORM.matches("WQ"))
        self.assertIn(NUOMI_FORM, SUPPLIER_FORMS)

    def test_each_site_product_is_its_own_line_in_pieces(self) -> None:
        rows = self.rows((self.BLACK, 40), (self.CHROME, 25.2), (self.KNOB, 0), (MUFFIN, 3))
        self.assertEqual(set(rows["section"]), {SECTION_OTHER})
        self.assertEqual(len(rows), 4)
        black = row_named(rows, "Ручка-скоба 128мм черная")
        self.assertEqual((black["code"], black["quantity"], black["edge_or_sheets"]), ("HB-128", 0, 40))
        self.assertEqual(row_named(rows, "Ручка-скоба 128мм хром")["edge_or_sheets"], 26)
        # A board of this supplier is a piece too: the form has no board table.
        self.assertEqual(row_named(rows, "Маффин 18*1220*2800мм")["edge_or_sheets"], 3)

    def test_workbook_has_one_table_with_a_total(self) -> None:
        rows = self.rows((self.BLACK, 40), (self.CHROME, 25), (self.KNOB, 0))
        sheet = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=NUOMI_FORM))).active
        self.assertEqual(sheet.title, "Заказ Nuomi")
        values = [sheet.cell(row, column).value for row in range(1, sheet.max_row + 1) for column in range(1, 7)]
        self.assertIn("Товары", values)
        self.assertIn("Кол-во, шт", values)
        self.assertNotIn("Плиты, шт", values)
        self.assertNotIn("Ручка-кнопка золото", values)
        self.assertEqual(sheet.cell(find_row(sheet, "Ручка-скоба 128мм черная"), BOARD_COLUMN).value, 40)
        total = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row, 1).value == "Итого, шт")
        self.assertTrue(str(sheet.cell(total, BOARD_COLUMN).value).startswith("=SUM("))
        cached = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=NUOMI_FORM)), data_only=True).active
        self.assertEqual(cached.cell(total, BOARD_COLUMN).value, 65)

    def test_file_comes_back_and_colours_of_one_model_stay_apart(self) -> None:
        rows = self.rows((self.BLACK, 40), (self.CHROME, 25), (self.KNOB, 0))
        workbook = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=NUOMI_FORM)))
        sheet = workbook.active
        sheet.cell(find_row(sheet, "Ручка-скоба 128мм хром"), BOARD_COLUMN).value = 30
        buffer = BytesIO()
        workbook.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), rows, form=NUOMI_FORM)
        self.assertEqual((parsed.errors, parsed.extra_rows), ([], []))
        applied = apply_order_file(rows, parsed)
        self.assertEqual(row_named(applied, "Ручка-скоба 128мм черная")["edge_or_sheets"], 40)
        self.assertEqual(row_named(applied, "Ручка-скоба 128мм хром")["edge_or_sheets"], 30)
        self.assertEqual(row_named(applied, "Ручка-кнопка золото")["edge_or_sheets"], 0)

    def test_order_lines_keep_the_site_product(self) -> None:
        rows = self.rows((self.BLACK, 40), (self.KNOB, 2))
        items = order_items_from_form(rows).set_index("product")["quantity"].to_dict()
        self.assertEqual(items, {self.BLACK: 40.0, self.KNOB: 2.0})
        rebuilt = order_form_from_items(order_items_from_form(rows), form=NUOMI_FORM)
        self.assertEqual(row_named(rebuilt, "Ручка-скоба 128мм черная")["edge_or_sheets"], 40)


class GenericSupplierFormTest(unittest.TestCase):
    BOARD = "ЛДСП D.333.S01 Яблоня Кубанская снежная E1 2800X2070X16 STD"
    EDGE = "Кромка ABS M.604.S01 Зеленый еловый 19*0,8"

    def test_supplier_without_a_form_gets_the_common_one(self) -> None:
        form = supplier_form_for("Экстраверт")
        self.assertEqual((form.title, form.key, form.goods_only), ("Экстраверт", "экстраверт", True))
        self.assertIs(supplier_form_for("WQ"), WQ_FORM)
        self.assertEqual(generic_supplier_form("ИП Иванов / Петров").key, "ип_иванов_петров")

    def test_spellings_of_one_supplier_share_a_form_and_keys_stay_unique(self) -> None:
        forms = generic_supplier_forms(["Emaks", "EMAKS", "Emaks ", "Emaks.", "AGT"])
        # "Emaks." is another name with the same key: the key gets a number, as Streamlit needs.
        self.assertEqual([form.title for form in forms], ["Emaks", "Emaks.", "AGT"])
        self.assertEqual(len({form.key for form in forms}), 3)

    def test_only_the_exact_supplier_name_counts(self) -> None:
        form = generic_supplier_form("AGT")
        self.assertTrue(form.matches(" agt "))
        self.assertFalse(form.matches("AGT Плюс"))

    def test_extravert_order_comes_back_from_its_file(self) -> None:
        form = generic_supplier_form("Экстраверт")
        data = forecast((self.BOARD, 12), (self.EDGE, 150.5), (MUFFIN, 7, "Высокий", "WQ"), supplier="Экстраверт")
        rows = build_order_form_rows(data, form=form)
        self.assertEqual(sorted(rows["product"]), sorted([self.BOARD, self.EDGE]))
        workbook = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=form)))
        sheet = workbook.active
        self.assertEqual(sheet["A1"].value, "Заказ поставщику Экстраверт")
        values = [sheet.cell(row, column).value for row in range(1, sheet.max_row + 1) for column in range(1, 7)]
        # Sheets of board and metres of edge band share the table, so no unit is named.
        self.assertIn("Кол-во", values)
        self.assertIn("Итого", values)
        sheet.cell(find_row(sheet, self.EDGE), BOARD_COLUMN).value = 200
        buffer = BytesIO()
        workbook.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), rows, form=form)
        self.assertEqual((parsed.errors, parsed.extra_rows), ([], []))
        items = order_items_from_form(apply_order_file(rows, parsed)).set_index("product")["quantity"].to_dict()
        self.assertEqual(items, {self.BOARD: 12.0, self.EDGE: 200.0})


def site_export(*lines: tuple) -> bytes:
    """The order as "Заказы в работе" downloads it."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказ"
    sheet.append(["SKU / Товар", "Количество", "Принято", "Цена закупки", "Сумма", "Комментарий"])
    for product, quantity in lines:
        sheet.append([product, quantity, 0, None, None, None])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class SiteExportFileTest(unittest.TestCase):
    BOARD = GenericSupplierFormTest.BOARD
    EDGE = GenericSupplierFormTest.EDGE

    def test_the_sites_own_export_comes_back(self) -> None:
        form = generic_supplier_form("Экстраверт")
        rows = build_order_form_rows(forecast((self.BOARD, 12), (self.EDGE, 150), supplier="Экстраверт"), form=form)
        content = site_export((self.BOARD, 26), (self.EDGE, 100), (self.BOARD, 26), ("ЛДСП Новый декор 2800X2070X16", 4))
        parsed = parse_order_form_file(content, rows, form=form)
        self.assertEqual(parsed.errors, [])
        items = order_items_from_form(apply_order_file(rows, parsed)).set_index("product")["quantity"].to_dict()
        # A product listed twice in the order is ordered twice; a new one keeps its name.
        self.assertEqual(items, {self.BOARD: 52.0, self.EDGE: 100.0, "ЛДСП Новый декор 2800X2070X16": 4.0})

    def test_edge_band_of_a_board_form_stays_edge_band(self) -> None:
        rows = build_order_form_rows(forecast((MUFFIN, 10), (MUFFIN_EDGE, 300)))
        parsed = parse_order_form_file(site_export((MUFFIN, 20), (MUFFIN_EDGE, 500)), rows)
        line = row_named(apply_order_file(rows, parsed), "Маффин")
        self.assertEqual((line["quantity"], line["edge_or_sheets"]), (20, 500))

    def test_a_file_without_known_columns_is_still_refused(self) -> None:
        workbook = Workbook()
        workbook.active.append(["Что-то", "Сколько"])
        buffer = BytesIO()
        workbook.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), build_order_form_rows(forecast((MUFFIN, 1))))
        self.assertTrue(parsed.errors)


class HettichFormTest(unittest.TestCase):
    QUADRO = "Quadro V6 YOU M (30 кг) с демпфер Silent System, 250 мм, левый"
    TOPLINE = "Комплект фурнитуры TopLine L для 2 дверей, передняя справа, толщина двери 18-30 мм, 50 кг"

    def test_supplier_name_is_recognised(self) -> None:
        for name in ("Hettich", "HETTICH", "Хеттих"):
            self.assertTrue(HETTICH_FORM.matches(name), name)
        self.assertFalse(HETTICH_FORM.matches("Nuomi"))
        self.assertIn(HETTICH_FORM, SUPPLIER_FORMS)

    def rows(self) -> pd.DataFrame:
        data = forecast(
            (self.QUADRO, 20),
            (self.TOPLINE, 3),
            ("Фиксаторы, левый", 0),
            (BLACK_HANDLE, 9, "Высокий", "Nuomi"),
            supplier="Hettich",
        )
        data["order_multiple"] = [10, 0, 50, 0]
        return build_order_form_rows(data, form=HETTICH_FORM)

    def test_only_this_supplier_in_pieces_with_the_box_size(self) -> None:
        rows = self.rows()
        self.assertEqual(sorted(rows["name"]), sorted([self.QUADRO, self.TOPLINE, "Фиксаторы, левый"]))
        quadro = row_named(rows, self.QUADRO)
        self.assertEqual((quadro["edge_or_sheets"], quadro["pack"]), (20, 10))

    def test_workbook_follows_the_sample(self) -> None:
        sheet = load_workbook(BytesIO(build_supplier_order_workbook(self.rows(), form=HETTICH_FORM))).active
        self.assertEqual(
            [sheet.cell(2, column).value for column in range(2, 7)],
            ["Артикул", "Наименование", "Количество в упаковке", "Короб", "Количество в заказе"],
        )
        lines = {sheet.cell(row, 3).value: [sheet.cell(row, column).value for column in (4, 5, 6)] for row in range(3, sheet.max_row + 1)}
        # 20 pieces in boxes of 10 are 2 boxes; without a box size only the pieces are known.
        self.assertEqual(lines, {self.QUADRO: [10, 2, 20], self.TOPLINE: [None, None, 3]})
        self.assertIsNone(sheet["A1"].value)

    def test_file_comes_back_with_its_quantities(self) -> None:
        rows = self.rows()
        workbook = load_workbook(BytesIO(build_supplier_order_workbook(rows, form=HETTICH_FORM)))
        sheet = workbook.active
        for row in range(3, sheet.max_row + 1):
            if sheet.cell(row, 3).value == self.TOPLINE:
                # An employee adds Hettich's article and changes the quantity.
                sheet.cell(row, 2).value = "9123456"
                sheet.cell(row, 6).value = 5
        buffer = BytesIO()
        workbook.save(buffer)
        parsed = parse_order_form_file(buffer.getvalue(), rows, form=HETTICH_FORM)
        self.assertEqual((parsed.errors, parsed.extra_rows), ([], []))
        applied = apply_order_file(rows, parsed)
        self.assertEqual(row_named(applied, self.QUADRO)["edge_or_sheets"], 20)
        self.assertEqual(row_named(applied, self.TOPLINE)["edge_or_sheets"], 5)


if __name__ == "__main__":
    unittest.main()
