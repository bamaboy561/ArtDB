"""The Экстраверт order in the layout of the supplier's price list.

A line is a decor: its code and structure, as "302 W04 Дуб Уральский какао",
with the sheets of board and the metres of its edge band. Boards 16 mm and
ТСП 18 mm are two tables, as in the supplier's file. The price list itself is
the supplier's, so it is not kept: the order lists the site's goods.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
import math
import re

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from supplier_order_forms import (
    EXCLUDED_ORDER_PRIORITY,
    NOTE_NEW_POSITION,
    NOTE_OTHER_SUPPLIER,
    ORDER_FORM_COLUMNS,
    SECTION_BOARDS,
    ParsedOrderFile,
    _file_quantity,
    _normalize,
    _normalize_code,
)
from local_time import local_today

BOARD_TABLE = "board"
TSP_TABLE = "tsp"
TABLE_FORMATS = {BOARD_TABLE: "2800х2070х16", TSP_TABLE: "2800х2070х18"}
TABLE_TITLES = {
    BOARD_TABLE: "ЛДСП Экстраверт 2800х2070х16",
    TSP_TABLE: "ТСС плита ЛДСП 2800х2070х18 мм",
}
# Site names mix in Cyrillic letters that look the same: "Т01 Лён Тверской".
_LOOKALIKES = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
# "ЛДСП D.302.W04 Дуб Уральский какао E1 2800X2070X16 STD", "Кромка ABS M.326.S01 …".
_SITE_DECOR = re.compile(r"(?<![0-9A-Z])[A-Z]\.(\d{3,4})\.([A-Z][0-9O]{2})(?![0-9A-Z])")
# The supplier writes the structure first: "W04 Дуб …", "ТСП Motus F05 …", even "МО1".
_FILE_STRUCTURE = re.compile(r"(?<![0-9A-Z])([A-Z][0-9O]{2})(?![0-9A-Z])")
_FORMAT = re.compile(r"(\d{4})\s*[xх×*]\s*(\d{4})\s*[xх×*]\s*(\d{2})", re.IGNORECASE)
_NAME_NOISE = re.compile(r"\bSTD\b|\bV\d+\b|\d+\s*\*\s*\d+(?:[.,]\d+)?", re.IGNORECASE)
_PREFIX_NOISE = re.compile(r"^\s*(?:ЛДСП|Кромка(?:\s+ABS)?)\s*", re.IGNORECASE)


def _latin(value: str) -> str:
    return value.upper().translate(_LOOKALIKES)


def _structure(value: str) -> str:
    return value[0] + value[1:].replace("O", "0")


def _format(text: str) -> str:
    match = _FORMAT.search(text)
    return "х".join(match.groups()) if match else ""


def _table_of(text: str) -> str:
    match = _FORMAT.search(text)
    thick = match is not None and int(match.group(3)) >= 18
    return TSP_TABLE if thick or _normalize(text).startswith("тсп") else BOARD_TABLE


def parse_site_name(product: str) -> dict[str, str] | None:
    """Code, structure, kind and the supplier's way of naming a site product, or None."""
    match = _SITE_DECOR.search(_latin(product))
    if match is None:
        return None
    is_edge = _normalize(product).startswith("кромка")
    prefix = "" if is_edge else _PREFIX_NOISE.sub("", product[: match.start()]).strip()
    rest = _NAME_NOISE.sub("", _FORMAT.sub("", product[match.end():]))
    structure = _structure(match.group(2))
    return {
        "code": match.group(1),
        "structure": structure,
        "kind": "edge" if is_edge else "board",
        "table": "" if is_edge else _table_of(product),
        "name": " ".join(" ".join([prefix, structure, rest]).split()),
        "format": "" if is_edge else _format(product),
    }


def _empty_row(row_id: str, code: str, name: str, table: str, supplier: object) -> dict[str, object]:
    return {
        **{column: "" for column in ORDER_FORM_COLUMNS},
        "row_id": row_id,
        "code": code,
        "quantity": 0,
        "edge_or_sheets": 0,
        "name": name,
        "section": SECTION_BOARDS,
        "available_stock_qty": 0.0,
        "forecast_qty": 0.0,
        "supplier": supplier,
        "low_priority_skipped": False,
        "pack": 0.0,
        "group": table,
        "format": TABLE_FORMATS.get(table, ""),
    }


def build_extravert_rows(forecast: pd.DataFrame) -> pd.DataFrame:
    """One line per decor: boards and ТСП in their tables, each edge band on the line of its decor."""
    records = forecast.to_dict(orient="records")
    parsed = [parse_site_name(str(record["product"])) for record in records]
    rows: dict[str, dict[str, object]] = {}
    by_decor: dict[tuple[str, str], str] = {}
    # Boards first, so that an edge band finds the line of its board.
    order = sorted(range(len(records)), key=lambda index: (parsed[index] or {}).get("kind") == "edge")
    for index in order:
        record, site = records[index], parsed[index]
        product = str(record["product"])
        if site is None:
            is_edge = _normalize(product).startswith("кромка")
            row = rows.setdefault(
                f"other:{_normalize_code(product)}",
                _empty_row(f"other:{_normalize_code(product)}", "", product, BOARD_TABLE, record.get("supplier", "")),
            )
        else:
            decor = (site["code"], site["structure"])
            row_id = by_decor.get(decor) if site["kind"] == "edge" else None
            if row_id is None:
                table = site["table"] or BOARD_TABLE
                row_id = f"{table}:{site['code']}:{site['structure']}"
                by_decor.setdefault(decor, row_id)
            row = rows.setdefault(row_id, _empty_row(row_id, site["code"], site["name"], site["table"] or BOARD_TABLE, record.get("supplier", "")))
            is_edge = site["kind"] == "edge"
            if not is_edge and site["format"]:
                row["format"] = site["format"]
        recommended = pd.to_numeric(pd.Series([record.get("recommended_order_qty")]), errors="coerce").fillna(0).iloc[0]
        needed = max(float(recommended), 0.0)
        if _normalize(record.get("priority", "")) == EXCLUDED_ORDER_PRIORITY:
            row["low_priority_skipped"] = bool(row["low_priority_skipped"]) or needed > 0
            needed = 0.0
        whole = math.ceil(needed - 1e-9) if needed > 0 else 0
        if is_edge:
            row["edge_or_sheets"] += whole
            row["edge_product"] = row["edge_product"] or product
        else:
            row["quantity"] += whole
            row["product"] = row["product"] or product
        if not is_edge or not row["priority"]:
            for column in ("abc_class", "xyz_class", "priority", "stock_status"):
                row[column] = record.get(column, "") or row[column]
        if not is_edge:
            for column in ("available_stock_qty", "forecast_qty"):
                row[column] += float(pd.to_numeric(pd.Series([record.get(column)]), errors="coerce").fillna(0).iloc[0])
    frame = pd.DataFrame(list(rows.values()), columns=ORDER_FORM_COLUMNS)
    if frame.empty:
        return frame
    sort_code = pd.to_numeric(frame["code"], errors="coerce").fillna(10**9)
    return (
        frame.assign(_table=frame["group"].ne(BOARD_TABLE), _code=sort_code)
        .sort_values(["_table", "_code", "name"], kind="stable")
        .drop(columns=["_table", "_code"])
        .reset_index(drop=True)
    )


def build_extravert_workbook(order_rows: pd.DataFrame, *, order_date: date | None = None) -> bytes:
    """The supplier's layout: a yellow title over each table, then code, name, format, sheets, edge band."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказ Экстраверт"
    for column, width in {"A": 7, "B": 46, "C": 14.3, "D": 8.9, "E": 12}.items():
        sheet.column_dimensions[column].width = width
    thin = Side(style="thin")
    frame = Border(left=thin, right=thin, top=thin, bottom=thin)
    yellow = PatternFill("solid", fgColor="FFFF00")
    center = Alignment(horizontal="center", vertical="center")
    sheet["B1"] = f"Заказ от {(order_date or local_today()):%d.%m.%Y}"
    sheet["B1"].font = Font(name="Calibri", size=11, bold=True)

    records = [
        record
        for record in order_rows.to_dict(orient="records")
        if int(record["quantity"]) > 0 or int(record["edge_or_sheets"]) > 0
    ]
    row = 2
    for table in (BOARD_TABLE, TSP_TABLE):
        lines = [record for record in records if (record.get("group") or BOARD_TABLE) == table]
        if not lines:
            continue
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        title = sheet.cell(row=row, column=1, value=TABLE_TITLES[table])
        title.font = Font(name="Calibri", size=11, bold=True)
        for column in range(1, 6):
            sheet.cell(row=row, column=column).fill = yellow
        row += 1
        for column, value in enumerate(("Код", "Номенклатура", "Формат", "Кол-во", "кромка"), start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.font = Font(name="Calibri", size=11, bold=True)
            cell.alignment = center
            cell.border = frame
        row += 1
        for record in lines:
            code = str(record["code"])
            values = (
                int(code) if code.isdigit() else code or None,
                record["name"],
                record.get("format") or TABLE_FORMATS[table],
                int(record["quantity"]) or None,
                int(record["edge_or_sheets"]) or None,
            )
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=column, value=value)
                cell.font = Font(name="Calibri", size=11, bold=column in (1, 2))
                cell.alignment = Alignment(vertical="center") if column == 2 else center
                cell.border = frame
            row += 1
        row += 2
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _file_decor(code: object, name: str) -> tuple[str, str]:
    if isinstance(code, float) and code.is_integer():
        code = int(code)
    match = _FILE_STRUCTURE.search(_latin(name))
    return str(code if code is not None else "").strip(), _structure(match.group(1)) if match else ""


def parse_extravert_file(
    content: bytes,
    order_rows: pd.DataFrame,
    site_products: tuple[str, ...] | list[str] | None,
    parsed: ParsedOrderFile,
) -> bool:
    """Quantities from the supplier's file into `parsed`; False when the file is not in its layout."""
    try:
        sheet = load_workbook(BytesIO(content), data_only=True, read_only=True).worksheets[0]
        table_rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    except Exception:
        return False

    by_decor: dict[tuple[str, str, str], dict[str, object]] = {}
    by_code: dict[tuple[str, str], dict[str, object]] = {}
    for record in order_rows.to_dict(orient="records"):
        if not record["code"]:
            continue  # A site product without a decor code is found by its name in other layouts.
        decor = (str(record["code"]), _file_decor(record["code"], str(record["name"]))[1])
        by_decor[(str(record.get("group") or BOARD_TABLE), *decor)] = record
        by_code.setdefault(decor, record)
    site_index: dict[tuple[str, str, str], str] = {}
    for product in site_products or ():
        site = parse_site_name(str(product))
        if site is not None:
            site_index.setdefault((site["kind"], site["code"], site["structure"]), str(product))
    extras: dict[str, dict[str, object]] = {}

    columns: dict[str, int] = {}
    headers = 0
    for number, values in enumerate(table_rows, start=1):
        titles = {" ".join(str(value or "").casefold().split()): position for position, value in enumerate(values)}
        if "код" in titles and set(titles) & {"номенклатура", "наименование"} and "кол-во" in titles:
            # The ТСП table has no "кромка" title, yet edge band is written in that column.
            if "кромка" not in titles and "кромка" in columns:
                titles["кромка"] = columns["кромка"]
            columns, headers = titles, headers + 1
            continue
        if not columns:
            continue

        def cell(*names: str) -> object:
            for name in names:
                position = columns.get(name)
                if position is not None and position < len(values) and values[position] is not None:
                    return values[position]
            return None

        name = " ".join(str(cell("номенклатура", "наименование") or "").split())
        code, structure = _file_decor(cell("код"), name)
        if not code and not name:
            continue
        sheets = _file_quantity(cell("кол-во"))
        edge = _file_quantity(cell("кромка"))
        if sheets is None or edge is None:
            parsed.errors.append(f"Строка {number}, код {code}: количество должно быть числом не меньше нуля.")
            continue
        if not sheets and not edge:
            continue
        format_text = str(cell("формат") or "")
        table = _table_of(f"{format_text} {name}") if format_text or name else (TSP_TABLE if headers > 1 else BOARD_TABLE)
        record = by_decor.get((table, code, structure)) or by_code.get((code, structure))
        if record is None:
            row_id = f"{table}:{code}:{structure}" if structure else f"{table}:{_normalize_code(name)}"
            board_product = site_index.get(("board", code, structure), "")
            edge_product = site_index.get(("edge", code, structure), "")
            record = extras.setdefault(
                row_id,
                {
                    **_empty_row(row_id, code, name, table, ""),
                    "format": _format(format_text) or TABLE_FORMATS[table],
                    "product": board_product,
                    "edge_product": edge_product,
                    "file_note": NOTE_OTHER_SUPPLIER if board_product or edge_product else NOTE_NEW_POSITION,
                },
            )
        previous = parsed.quantities.get(record["row_id"], (0, 0))
        parsed.quantities[record["row_id"]] = (previous[0] + sheets, previous[1] + edge)
    parsed.extra_rows = list(extras.values())
    return headers > 0
