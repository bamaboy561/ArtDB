from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from report_workbook import FIRST_DATA_ROW, HEADER_ROW, build_report_workbook  # noqa: E402


def open_workbook(content: bytes):
    return load_workbook(BytesIO(content))


class ReportWorkbookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = pd.DataFrame(
            [
                {"Показатель": "Период с", "Значение": "2026-03-01", "Единица": ""},
                {"Показатель": "Категория", "Значение": "Все категории", "Единица": ""},
                {"Показатель": "Выручка", "Значение": 1500000.0, "Единица": "сом"},
                {"Показатель": "Маржинальность", "Значение": 17.4, "Единица": "%"},
                {"Показатель": "Изменение выручки", "Значение": None, "Единица": "%"},
            ]
        )
        self.monthly = pd.DataFrame(
            {
                "Месяц": ["2026-03", "2026-04"],
                "Выручка": [1000000.0, 500000.0],
                "Маржинальность, %": [18.0, 16.5],
                "Изменение выручки, %": [None, -50.0],
                "Дата": [pd.Timestamp("2026-03-31"), pd.Timestamp("2026-04-30")],
                "Статус остатка": ["Нет остатка", "Покрыто остатком"],
            }
        )
        self.content = build_report_workbook(
            {"Сводка": self.summary, "Динамика": self.monthly, "Пусто": pd.DataFrame()},
            title="Тестовый отчёт",
            subtitle="Период: 01.03.2026 - 30.04.2026",
            generated_at=datetime(2026, 10, 14, 9, 30),
        )

    def test_cover_lists_figures_and_links_to_sheets(self) -> None:
        sheet = open_workbook(self.content)["Сводка"]
        self.assertEqual(sheet["A1"].value, "Тестовый отчёт")
        self.assertIn("Сформирован 14.10.2026 09:30", sheet["A2"].value)

        cells = {row[0].value: row for row in sheet.iter_rows(min_row=HEADER_ROW) if row[0].value}
        self.assertEqual(cells["Период с"][1].value.date(), date(2026, 3, 1))
        self.assertEqual(cells["Выручка"][1].value, 1500000.0)
        self.assertIn("сом", cells["Выручка"][1].number_format)
        self.assertEqual(cells["Изменение выручки"][1].value, "нет данных")
        self.assertEqual(cells["Динамика"][0].hyperlink.location, "'Динамика'!A1")
        self.assertEqual(cells["Динамика"][1].value, "2 строк")

    def test_table_sheet_has_title_header_formats_and_totals(self) -> None:
        sheet = open_workbook(self.content)["Динамика"]
        self.assertEqual(sheet["A1"].value, "Динамика")
        self.assertIn("Продажи по месяцам", sheet["A2"].value)
        self.assertEqual(sheet.cell(row=HEADER_ROW, column=1).value, "Месяц")
        self.assertEqual(sheet.freeze_panes, f"B{FIRST_DATA_ROW}")
        self.assertEqual(sheet.auto_filter.ref, f"A{HEADER_ROW}:F{FIRST_DATA_ROW + 1}")

        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW, column=1).value, "март 2026")
        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW, column=2).number_format, "#,##0")
        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW, column=3).number_format, '0.0"%"')
        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW, column=5).number_format, "DD.MM.YYYY")
        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW + 1, column=4).font.color.rgb, "00B91C1C")
        self.assertEqual(sheet.cell(row=FIRST_DATA_ROW, column=6).fill.fgColor.rgb, "00FDE2E4")

        total_row = FIRST_DATA_ROW + 2
        self.assertEqual(sheet.cell(row=total_row, column=1).value, "Итого (2 строк)")
        self.assertEqual(sheet.cell(row=total_row, column=2).value, 1500000.0)
        self.assertIsNone(sheet.cell(row=total_row, column=3).value)

    def test_empty_sheet_explains_itself(self) -> None:
        sheet = open_workbook(self.content)["Пусто"]
        self.assertIn("данных для этого листа нет", sheet.cell(row=HEADER_ROW, column=1).value)


if __name__ == "__main__":
    unittest.main()
